"""同一投入セット内の顕微鏡・ラスタ画像の重複スキャン連携."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pre_peer_checker.imaging.duplicate_scan import scan_image_duplicates_auto
from pre_peer_checker.imaging.lightglue_match import inversion_note
from pre_peer_checker.imaging.microscopy import export_frames_as_png, try_load_frames
from pre_peer_checker.warnings import WarningItem, WarningTag


@dataclass
class MicroscopyScanResult:
    warnings: list[WarningItem] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _PreviewOrigin:
    source: Path
    frame_label: str
    multi_frame: bool


def _file_digest(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def dedupe_identical_files(paths: list[Path]) -> tuple[list[Path], list[list[str]]]:
    """Drop byte-identical files, keeping the first occurrence.

    Only files sharing a size are hashed, so large LIF/CZI stacks with unique
    sizes are never read in full. Returns (unique paths, groups of identical
    paths with the kept path first).
    """
    by_size: dict[int, list[Path]] = {}
    for p in paths:
        try:
            by_size.setdefault(p.stat().st_size, []).append(p)
        except OSError:
            by_size.setdefault(-1, []).append(p)

    duplicate_of: dict[Path, Path] = {}
    groups: list[list[str]] = []
    for size, same_size in by_size.items():
        if size < 0 or len(same_size) < 2:
            continue
        by_digest: dict[str, list[Path]] = {}
        for p in same_size:
            try:
                by_digest.setdefault(_file_digest(p), []).append(p)
            except OSError:
                continue
        for members in by_digest.values():
            if len(members) < 2:
                continue
            kept = members[0]
            for dup in members[1:]:
                duplicate_of[dup] = kept
            groups.append([str(m) for m in members])

    unique = [p for p in paths if p not in duplicate_of]
    return unique, groups


def _display_pair(a: Path, b: Path) -> tuple[str, str]:
    """Paths relative to the pair's common folder, so same-named files stay distinguishable."""
    try:
        common = Path(os.path.commonpath([a.parent, b.parent]))
        return str(a.relative_to(common)), str(b.relative_to(common))
    except ValueError:
        return str(a), str(b)


def _origin_label(display: str, origin: _PreviewOrigin) -> str:
    if origin.multi_frame:
        return f"{display} [{origin.frame_label}]"
    return display


def select_image_paths(
    lif_paths: list[Path],
    image_paths: list[Path],
    *,
    max_files: int = 40,
) -> list[Path]:
    """Prefer small rasters; sample LIF. Cap total for Phase 1 runtime."""
    rasters = [
        p
        for p in image_paths
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
    ]
    # Prefer fig-ish folders / smaller files first
    rasters.sort(key=lambda p: (0 if any(k in str(p).lower() for k in ("fig", "supp")) else 1, p.stat().st_size))
    lifs = sorted(lif_paths, key=lambda p: p.stat().st_size)
    selected: list[Path] = []
    for p in rasters:
        if len(selected) >= max_files:
            break
        selected.append(p)
    for p in lifs:
        if len(selected) >= max_files:
            break
        selected.append(p)
    return selected


def scan_microscopy_duplicates(
    lif_paths: list[Path],
    image_paths: list[Path],
    *,
    max_files: int = 40,
    prefer_dino: bool = True,
    corpus_provided: bool = False,
) -> MicroscopyScanResult:
    result = MicroscopyScanResult()
    lif_unique, lif_groups = dedupe_identical_files(list(lif_paths))
    image_unique, image_groups = dedupe_identical_files(list(image_paths))
    identical_groups = lif_groups + image_groups
    if identical_groups:
        result.artifacts["identical_sources"] = identical_groups
    selected = select_image_paths(lif_unique, image_unique, max_files=max_files)
    result.artifacts["selected_sources"] = [str(p) for p in selected]
    if len(selected) < 2:
        result.artifacts["note"] = "insufficient images for duplicate scan"
        return result

    tmp = Path(tempfile.mkdtemp(prefix="mc_micro_"))
    preview_paths: list[Path] = []
    origins: dict[Path, _PreviewOrigin] = {}
    load_errors: list[dict[str, str]] = []
    loaded = 0
    try:
        for idx, src in enumerate(selected):
            frames, err = try_load_frames(src, max_series=2)
            if err:
                load_errors.append({"path": str(src), "error": err})
                continue
            if not frames:
                continue
            kept = frames[:2]
            written = export_frames_as_png(kept, tmp / f"{idx:03d}", max_side=384)
            for fr, dest in zip(kept, written):
                origins[dest] = _PreviewOrigin(
                    source=src,
                    frame_label=str(fr.label),
                    multi_frame=len(kept) > 1,
                )
            preview_paths.extend(written)
            loaded += 1

        result.artifacts["loaded_sources"] = loaded
        result.artifacts["load_errors"] = load_errors
        result.artifacts["preview_count"] = len(preview_paths)

        if len(preview_paths) < 2:
            result.artifacts["note"] = "could not export enough previews"
            return result

        matches, method = scan_image_duplicates_auto(
            preview_paths, prefer_dino=prefer_dino
        )
        result.artifacts["scan_method"] = method
        dupes = [m for m in matches if m.likely_duplicate]
        result.artifacts["duplicate_pairs"] = len(dupes)

        corpus_note = (
            "過去論文コーパスとの出典照合は別項目で実施しています"
            "（一致があれば「過去論文コーパスと同一写真」の Warning が出ます）。"
            if corpus_provided
            else "過去論文コーパス未指定のため出典チェックは未実施です。"
        )
        seen: set[tuple[tuple[str, str], tuple[str, str]]] = set()
        for m in dupes:
            origin_a = origins[m.path_a]
            origin_b = origins[m.path_b]
            key_a = (str(origin_a.source), origin_a.frame_label)
            key_b = (str(origin_b.source), origin_b.frame_label)
            if key_a == key_b:
                continue
            key = (min(key_a, key_b), max(key_a, key_b))
            if key in seen:
                continue
            seen.add(key)
            disp_a, disp_b = _display_pair(origin_a.source, origin_b.source)
            result.warnings.append(
                WarningItem(
                    tag=WarningTag.IMAGE_REUSE,
                    title="同一セット内の高類似度画像ペア",
                    location=(
                        f"{_origin_label(disp_a, origin_a)} ↔ "
                        f"{_origin_label(disp_b, origin_b)}"
                    ),
                    reason=(
                        f"類似度 {m.cosine_similarity:.4f}（method={m.method}）。"
                        "同一投入セット内での画像再利用・取り違えの可能性があります。"
                        + inversion_note(m.precise_inverted)
                        + corpus_note
                    ),
                    sources=[str(origin_a.source), str(origin_b.source)],
                    metadata={
                        "pattern_id": "P-IMAGE-REUSE-UNCITED",
                        "similarity": m.cosine_similarity,
                        "method": m.method,
                        "same_set_only": True,
                        "frame_a": origin_a.frame_label,
                        "frame_b": origin_b.frame_label,
                        "inverted": bool(m.precise_inverted),
                    },
                )
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return result
