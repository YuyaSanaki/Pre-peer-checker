"""外部過去論文画像コーパスとの照合（H3 / P-IMAGE-REUSE-UNCITED / P-IMAGE-PARTIAL-REUSE）."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pre_peer_checker.imaging.duplicate_scan import ImagePairMatch, scan_image_duplicates_auto
from pre_peer_checker.imaging.panel_units import (
    PanelUnit,
    make_panel_verifier,
    match_rank,
    panel_label,
    prepare_panel_sources,
    rank_vectors,
)
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


def _panel_candidates(
    q_units: list[PanelUnit],
    c_units: list[PanelUnit],
    *,
    max_pairs: int,
    min_top_k: int,
    prefer_dino: bool,
) -> tuple[list[tuple[int, int, float | None]], str]:
    """All query×corpus unit pairs when within budget, else DINO top-k shortlist."""
    n_q, n_c = len(q_units), len(c_units)
    if n_q * n_c <= max_pairs:
        return [(i, j, None) for i in range(n_q) for j in range(n_c)], "all_pairs"
    q_vec = rank_vectors([u.path for u in q_units], prefer_dino=prefer_dino)
    c_vec = rank_vectors([u.path for u in c_units], prefer_dino=prefer_dino)
    sims = q_vec @ c_vec.T
    k_q = min(n_c, max(min_top_k, max_pairs // max(n_q, 1)))
    k_c = min(n_q, max(2, max_pairs // max(4 * n_c, 1)))
    chosen: set[tuple[int, int]] = set()
    for i in range(n_q):
        for j in np.argsort(-sims[i])[:k_q]:
            chosen.add((i, int(j)))
    for j in range(n_c):
        for i in np.argsort(-sims[:, j])[:k_c]:
            chosen.add((int(i), j))
    ordered = sorted(chosen, key=lambda ij: -sims[ij[0], ij[1]])
    return [(i, j, float(sims[i, j])) for i, j in ordered], f"top{k_q}+rev{k_c}"


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
    enable_panels: bool = True,
    max_panel_query: int = 120,
    max_panel_units: int = 800,
    max_panel_pairs: int = 2000,
    panel_min_top_k: int = 5,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> CorpusScanResult:
    """Compare manuscript images to a user-provided past-paper corpus.

    on_progress(done, total, label): total=0 はフェーズ切替のみ（件数なし）。
    部分一致（原稿×コーパスの全ペア）は 1 ペアずつ報告する。

    パネル照合（enable_panels）: 合成図・ページ画像・スクリーンショットを写真パネルに
    分割し、原稿×コーパスのパネル対を LightGlue で照合する。図全体の見た目が違う
    （別レイアウト・別画質の同一写真）ため DINOv2 の全体類似度では拾えない再利用用。
    ペア数が ``max_panel_pairs`` を超える場合のみ DINOv2 上位候補に絞る。
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

    n_query_sources = max(max_query, max_panel_query) if enable_panels else max_query
    queries = [Path(p) for p in query_images][:n_query_sources]
    if not queries:
        # allow raster-like only from paths already exported
        result.artifacts["note"] = "no query images for corpus scan"
        return result

    tmp = Path(tempfile.mkdtemp(prefix="mc_corpus_"))
    try:
        _notify(0, 0, f"画像を読み込み中（原稿 {len(queries)} 枚・コーパス {len(corpus_paths)} 枚）")
        q_prev, q_units = prepare_panel_sources(
            queries, tmp / "q", n_preview=max_query, panels=enable_panels,
            max_units=max_panel_units,
        )
        c_prev, c_units = prepare_panel_sources(
            corpus_paths, tmp / "c", n_preview=len(corpus_paths), panels=enable_panels,
            max_units=max_panel_units,
        )
        result.artifacts["query_previews"] = len(q_prev)
        result.artifacts["corpus_previews"] = len(c_prev)
        result.artifacts["query_panel_units"] = len(q_units)
        result.artifacts["corpus_panel_units"] = len(c_units)
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

        panel_hits = 0
        if enable_panels and q_units and c_units:
            panel_hits = _scan_panel_pairs(
                q_units,
                c_units,
                result=result,
                pair_log=pair_log,
                seen=seen,
                legend_has_citation=legend_has_citation,
                prefer_dino=prefer_dino,
                prefer_lightglue=prefer_lightglue,
                max_pairs=max_panel_pairs,
                min_top_k=panel_min_top_k,
                notify=_notify,
            )
        result.artifacts["cross_panel_pairs"] = panel_hits
        result.artifacts["cross_match_pairs"] = pair_log
        result.artifacts["enable_partial"] = bool(enable_partial)
        result.artifacts["enable_panels"] = bool(enable_panels)
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    return result


def _scan_panel_pairs(
    q_units: list[PanelUnit],
    c_units: list[PanelUnit],
    *,
    result: CorpusScanResult,
    pair_log: list[dict],
    seen: set[tuple[str, str]],
    legend_has_citation: bool,
    prefer_dino: bool,
    prefer_lightglue: bool,
    max_pairs: int,
    min_top_k: int,
    notify: Callable[[int, int, str], None],
) -> int:
    """LightGlue over query×corpus panel units; one finding per source pair."""
    notify(0, 0, f"パネル候補を選定中（原稿 {len(q_units)}・コーパス {len(c_units)} パネル）")
    candidates, selection = _panel_candidates(
        q_units, c_units, max_pairs=max_pairs, min_top_k=min_top_k, prefer_dino=prefer_dino
    )
    result.artifacts["panel_selection"] = selection
    result.artifacts["panel_candidate_pairs"] = len(candidates)

    _verify, verifier_name = make_panel_verifier(prefer_lightglue=prefer_lightglue)

    best: dict[tuple[str, str], dict] = {}
    total = len(candidates)
    for k, (i, j, sim) in enumerate(candidates):
        qu, cu = q_units[i], c_units[j]
        key = tuple(sorted((str(qu.source), str(cu.source))))
        if key in seen:
            continue
        if k % 25 == 0:
            notify(k, total, f"パネル単位の照合中（{k + 1}/{total} 組）")
        vr = _verify(qu.path, cu.path)
        if not vr.verified:
            continue
        hit = best.setdefault(key, {"q": qu, "c": cu, "vr": vr, "sim": sim, "panels": []})
        hit["panels"].append((qu, cu, vr))
        if match_rank(qu, cu, vr) > match_rank(hit["q"], hit["c"], hit["vr"]):
            hit.update(q=qu, c=cu, vr=vr, sim=sim)
    notify(total, total, "")
    result.artifacts["panel_verifier"] = verifier_name

    for key, hit in best.items():
        seen.add(key)
        qu, cu, vr = hit["q"], hit["c"], hit["vr"]
        panels = sorted(hit["panels"], key=lambda p: match_rank(*p), reverse=True)
        panel_matches = [
            {"panel_a": p[0].box, "panel_b": p[1].box, "matches": p[2].num_matches,
             "inliers": p[2].inliers}
            for p in panels
        ]
        listing = "／".join(
            f"{panel_label(p[0])} ↔ {panel_label(p[1])}（{p[2].num_matches} 点）"
            for p in panels[:6]
        )
        pair_log.append(
            {
                "a": str(qu.source),
                "b": str(cu.source),
                "similarity": hit["sim"],
                "method": vr.method,
                "kind": "panel",
                "cited": bool(legend_has_citation),
                "precise_verified": True,
                "matches": vr.num_matches,
                "inliers": vr.inliers,
                "panel_a": qu.box,
                "panel_b": cu.box,
                "panel_matches": panel_matches,
            }
        )
        if legend_has_citation:
            result.artifacts.setdefault("cited_matches", []).append(
                {"a": str(qu.source), "b": str(cu.source), "matches": vr.num_matches}
            )
            continue
        result.warnings.append(
            WarningItem(
                tag=WarningTag.IMAGE_REUSE,
                title="過去論文コーパスと同一写真（パネル単位）・出典未記載の疑い",
                location=f"{panel_label(qu)} ↔ {panel_label(cu)}",
                reason=(
                    f"パネル単位の特徴点照合で一致 {vr.num_matches} 点"
                    + (f"（うち同一の幾何変換に乗る点 {vr.inliers}）" if vr.inliers is not None else "")
                    + f"。method={vr.method}。一致したパネル組 {len(panels)}: {listing}。"
                    "図のレイアウトや画質が違っても同じ写真の可能性があります。"
                    "Legend に reproduced/adapted from 等の出典表記が見つかりません。"
                ),
                sources=[str(qu.source), str(cu.source)],
                metadata={
                    "pattern_id": "P-IMAGE-REUSE-UNCITED",
                    "method": vr.method,
                    "precise_matches": vr.num_matches,
                    "precise_inliers": vr.inliers,
                    "corpus_match": True,
                    "panel": True,
                    "panel_a": qu.box,
                    "panel_b": cu.box,
                    "panel_matches": panel_matches,
                },
            )
        )
    return len(best)
