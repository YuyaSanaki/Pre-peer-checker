"""同一投入セット内の顕微鏡・ラスタ画像の重複スキャン連携."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pre_peer_checker.imaging.duplicate_scan import scan_image_duplicates_auto
from pre_peer_checker.imaging.microscopy import export_frames_as_png, try_load_frames
from pre_peer_checker.warnings import WarningItem, WarningTag


@dataclass
class MicroscopyScanResult:
    warnings: list[WarningItem] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)


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
) -> MicroscopyScanResult:
    result = MicroscopyScanResult()
    selected = select_image_paths(lif_paths, image_paths, max_files=max_files)
    result.artifacts["selected_sources"] = [str(p) for p in selected]
    if len(selected) < 2:
        result.artifacts["note"] = "insufficient images for duplicate scan"
        return result

    tmp = Path(tempfile.mkdtemp(prefix="mc_micro_"))
    preview_paths: list[Path] = []
    load_errors: list[dict[str, str]] = []
    loaded = 0
    try:
        for src in selected:
            frames, err = try_load_frames(src, max_series=2)
            if err:
                load_errors.append({"path": str(src), "error": err})
                continue
            if not frames:
                continue
            written = export_frames_as_png(frames[:2], tmp / src.stem, max_side=384)
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

        seen: set[tuple[str, str]] = set()
        for m in dupes:
            key = tuple(sorted((m.path_a.name, m.path_b.name)))
            if key in seen:
                continue
            seen.add(key)
            # Recover original sources from preview naming when possible
            result.warnings.append(
                WarningItem(
                    tag=WarningTag.IMAGE_REUSE,
                    title="同一セット内の高類似度画像ペア",
                    location=f"{m.path_a.name} ↔ {m.path_b.name}",
                    reason=(
                        f"類似度 {m.cosine_similarity:.4f}（method={m.method}）。"
                        "同一投入セット内での画像再利用・取り違えの可能性があります。"
                        "過去論文コーパス未指定のため出典チェックは未実施です。"
                    ),
                    sources=[str(m.path_a), str(m.path_b)],
                    metadata={
                        "pattern_id": "P-IMAGE-REUSE-UNCITED",
                        "similarity": m.cosine_similarity,
                        "method": m.method,
                        "same_set_only": True,
                    },
                )
            )
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    return result
