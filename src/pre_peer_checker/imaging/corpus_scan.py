"""外部過去論文画像コーパスとの照合（H3 / P-IMAGE-REUSE-UNCITED / P-IMAGE-PARTIAL-REUSE）."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pre_peer_checker.imaging.duplicate_scan import ImagePairMatch, scan_image_duplicates_auto
from pre_peer_checker.imaging.microscopy import export_frames_as_png, try_load_frames
from pre_peer_checker.imaging.partial_match import partial_containment_score
from pre_peer_checker.warnings import WarningItem, WarningTag

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".lif", ".czi"}


@dataclass
class CorpusScanResult:
    warnings: list[WarningItem] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)
    corpus_present: bool = False


def collect_corpus_images(corpus_roots: list[Path | str], *, max_files: int = 80) -> list[Path]:
    found: list[Path] = []
    for raw in corpus_roots:
        root = Path(raw).expanduser().resolve()
        if root.is_file() and root.suffix.lower() in _IMAGE_EXTS:
            found.append(root)
            continue
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in _IMAGE_EXTS and not p.name.startswith("."):
                found.append(p)
                if len(found) >= max_files:
                    return found
    return found[:max_files]


def _export_previews(paths: list[Path], tmp: Path, *, max_side: int = 384) -> list[tuple[Path, Path]]:
    """Return list of (preview_path, source_path)."""
    out: list[tuple[Path, Path]] = []
    for i, src in enumerate(paths):
        frames, err = try_load_frames(src, max_series=1)
        if err or not frames:
            continue
        written = export_frames_as_png(frames[:1], tmp / f"c{i}_{src.stem}", max_side=max_side)
        for w in written:
            out.append((w, src))
    return out


def scan_against_corpus(
    query_images: list[Path | str],
    corpus_roots: list[Path | str],
    *,
    legend_has_citation: bool = False,
    max_query: int = 40,
    max_corpus: int = 80,
    prefer_dino: bool = True,
    prefer_lightglue: bool = True,
    enable_partial: bool = True,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> CorpusScanResult:
    """Compare manuscript images to a user-provided past-paper corpus.

    on_progress(done, total, label): total=0 はフェーズ切替のみ（件数なし）。
    部分一致（原稿×コーパスの全ペア）は 1 ペアずつ報告する。
    """

    def _notify(done: int, total: int, label: str) -> None:
        if on_progress is not None:
            on_progress(done, total, label)

    result = CorpusScanResult()
    corpus_paths = collect_corpus_images(corpus_roots, max_files=max_corpus)
    result.corpus_present = bool(corpus_paths)
    result.artifacts["corpus_roots"] = [str(Path(p)) for p in corpus_roots]
    result.artifacts["corpus_files"] = [str(p) for p in corpus_paths]
    result.artifacts["legend_has_citation"] = legend_has_citation

    if not corpus_paths:
        result.artifacts["note"] = "corpus empty or missing — H3 external match skipped"
        return result

    queries = [Path(p) for p in query_images][:max_query]
    if not queries:
        # allow raster-like only from paths already exported
        result.artifacts["note"] = "no query images for corpus scan"
        return result

    tmp = Path(tempfile.mkdtemp(prefix="mc_corpus_"))
    try:
        _notify(0, 0, f"画像を読み込み中（原稿 {len(queries)} 枚・コーパス {len(corpus_paths)} 枚）")
        q_prev = _export_previews(queries, tmp / "q")
        c_prev = _export_previews(corpus_paths, tmp / "c")
        result.artifacts["query_previews"] = len(q_prev)
        result.artifacts["corpus_previews"] = len(c_prev)
        if not q_prev or not c_prev:
            result.artifacts["note"] = "could not export previews for corpus match"
            return result

        # Pairwise across sets only (not within)
        preview_paths = [p for p, _ in q_prev] + [p for p, _ in c_prev]
        src_of = {str(prev): src for prev, src in q_prev + c_prev}
        q_set = {str(p) for p, _ in q_prev}

        _notify(0, 0, f"全体一致の類似度スキャン中（{len(preview_paths)} 枚）")
        matches, method = scan_image_duplicates_auto(
            preview_paths,
            prefer_dino=prefer_dino,
            prefer_lightglue=prefer_lightglue,
            refine_with_precise=True,
        )
        result.artifacts["scan_method"] = method

        cross: list[ImagePairMatch] = []
        for m in matches:
            a_q = str(m.path_a) in q_set
            b_q = str(m.path_b) in q_set
            if a_q == b_q:
                continue  # both query or both corpus
            if m.likely_duplicate:
                cross.append(m)

        result.artifacts["cross_duplicate_pairs"] = len(cross)
        seen: set[tuple[str, str]] = set()
        dup_src_pairs: set[tuple[str, str]] = set()
        pair_log: list[dict] = []
        for m in cross:
            sa = src_of.get(str(m.path_a), m.path_a)
            sb = src_of.get(str(m.path_b), m.path_b)
            key = tuple(sorted((str(sa), str(sb))))
            if key in seen:
                continue
            seen.add(key)
            dup_src_pairs.add(key)
            entry = {
                "a": str(sa),
                "b": str(sb),
                "similarity": float(m.cosine_similarity),
                "method": m.method,
                "kind": "full",
                "cited": bool(legend_has_citation),
                "precise_verified": getattr(m, "precise_verified", None),
            }
            pair_log.append(entry)
            if legend_has_citation:
                # Still record as informational soft warning? Spec: warn if uncited.
                # Skip hard warning when citation present.
                result.artifacts.setdefault("cited_matches", []).append(
                    {"a": str(sa), "b": str(sb), "similarity": m.cosine_similarity}
                )
                continue
            result.warnings.append(
                WarningItem(
                    tag=WarningTag.IMAGE_REUSE,
                    title="過去論文コーパスと高類似・出典未記載の疑い",
                    location=f"{Path(sa).name} ↔ {Path(sb).name}",
                    reason=(
                        f"類似度 {m.cosine_similarity:.4f}（method={m.method}）。"
                        "Legend に reproduced/adapted from 等の出典表記が見つかりません。"
                    ),
                    sources=[str(sa), str(sb)],
                    metadata={
                        "pattern_id": "P-IMAGE-REUSE-UNCITED",
                        "similarity": m.cosine_similarity,
                        "method": m.method,
                        "corpus_match": True,
                        "precise_verified": getattr(m, "precise_verified", None),
                    },
                )
            )

        # Partial / cropped containment across query↔corpus (Bik Cat II proxy)
        partial_hits = 0
        if enable_partial:
            n_pairs = len(q_prev) * len(c_prev)
            i_pair = 0
            for q_prev_path, q_src in q_prev:
                for c_prev_path, c_src in c_prev:
                    _notify(
                        i_pair,
                        n_pairs,
                        f"部分一致（切り抜き再利用）の照合中: {Path(q_src).name} ↔ "
                        f"{Path(c_src).name}（{i_pair + 1}/{n_pairs} ペア）",
                    )
                    i_pair += 1
                    key = tuple(sorted((str(q_src), str(c_src))))
                    if key in dup_src_pairs or key in seen:
                        continue
                    pm = partial_containment_score(q_prev_path, c_prev_path)
                    if pm is None or pm.score < 0.92:
                        continue
                    seen.add(key)
                    partial_hits += 1
                    entry = {
                        "a": str(q_src),
                        "b": str(c_src),
                        "similarity": float(pm.score),
                        "method": pm.method,
                        "kind": "partial",
                        "cited": bool(legend_has_citation),
                        "precise_verified": None,
                    }
                    pair_log.append(entry)
                    if legend_has_citation:
                        result.artifacts.setdefault("cited_partial_matches", []).append(
                            {"a": str(q_src), "b": str(c_src), "score": pm.score}
                        )
                        continue
                    result.warnings.append(
                        WarningItem(
                            tag=WarningTag.IMAGE_REUSE,
                            title="過去論文コーパスと部分一致・出典未記載の疑い",
                            location=f"{Path(q_src).name} ↔ {Path(c_src).name}",
                            reason=(
                                f"部分包含 NCC={pm.score:.4f}（method={pm.method}）。"
                                "クロップ／変形後の再利用の可能性。出典表記を確認してください。"
                            ),
                            sources=[str(q_src), str(c_src)],
                            metadata={
                                "pattern_id": "P-IMAGE-PARTIAL-REUSE",
                                "similarity": pm.score,
                                "method": pm.method,
                                "corpus_match": True,
                                "partial": True,
                            },
                        )
                    )
            _notify(n_pairs, n_pairs, "")
        result.artifacts["cross_partial_pairs"] = partial_hits
        result.artifacts["cross_match_pairs"] = pair_log
        result.artifacts["enable_partial"] = bool(enable_partial)
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    return result
