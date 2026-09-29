"""原稿・data 内での写真パネル再利用検出（P-IMAGE-DUPLICATE-INTERNAL）.

コーパス照合（corpus_scan）が原稿×過去論文なのに対し、こちらは原稿内の総当たり。
図をパネルに分割してから照合するので、同じ写真が別 Figure の別パネルに使い回されて
いる場合や、1 枚の合成図の中で同じパネルが 2 回出ている場合を拾える。
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pre_peer_checker.imaging.lightglue_match import inversion_note
from pre_peer_checker.imaging.panel_units import (
    PanelPositions,
    PanelUnit,
    make_panel_verifier,
    match_rank,
    panel_label,
    panel_position,
    panel_positions,
    panel_reading_order,
    prepare_panel_sources,
    rank_similarity,
)
from pre_peer_checker.warnings import WarningItem, WarningTag

# A pair of source files whose panels map onto each other almost one-to-one is the
# same figure saved twice (PDF embed vs exported PNG), not a reused photo.
SAME_FIGURE_MIN_PANELS = 3
SAME_FIGURE_MIN_COVERAGE = 0.6


@dataclass
class PanelReuseResult:
    warnings: list[WarningItem] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)


def _comparable(a: PanelUnit, b: PanelUnit) -> bool:
    """Skip whole-vs-own-panel inside one source: containment is trivial there."""
    if str(a.source) != str(b.source):
        return True
    return a.box is not None and b.box is not None


def _self_candidates(
    units: list[PanelUnit], *, max_pairs: int, min_top_k: int, prefer_dino: bool
) -> tuple[list[tuple[int, int, float | None]], str]:
    """All comparable unit pairs when within budget, else a DINO top-k shortlist."""
    n = len(units)
    allowed = [
        (i, j) for i in range(n) for j in range(i + 1, n) if _comparable(units[i], units[j])
    ]
    if len(allowed) <= max_pairs:
        return [(i, j, None) for i, j in allowed], "all_pairs"
    sims = rank_similarity([u.path for u in units], prefer_dino=prefer_dino)
    allowed_set = set(allowed)
    k = max(min_top_k, max_pairs // max(n, 1))
    chosen: set[tuple[int, int]] = set()
    for i in range(n):
        order = np.argsort(-sims[i])
        taken = 0
        for raw in order:
            j = int(raw)
            key = (i, j) if i < j else (j, i)
            if key in allowed_set and key not in chosen:
                chosen.add(key)
                taken += 1
                if taken >= k:
                    break
    ordered = sorted(chosen, key=lambda ij: -sims[ij[0], ij[1]])[:max_pairs]
    return [(i, j, float(sims[i, j])) for i, j in ordered], f"top{k}"


def scan_internal_panel_reuse(
    images: list[Path | str],
    *,
    max_sources: int = 120,
    max_units: int = 400,
    max_pairs: int = 2500,
    min_top_k: int = 5,
    prefer_dino: bool = True,
    prefer_lightglue: bool = True,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> PanelReuseResult:
    """Find the same photo used twice inside the manuscript's own images.

    on_progress(done, total, label): total=0 はフェーズ切替のみ（件数なし）。
    """

    def _notify(done: int, total: int, label: str) -> None:
        if on_progress is not None:
            on_progress(done, total, label)

    result = PanelReuseResult()
    sources = [Path(p) for p in images][:max_sources]
    result.artifacts["sources"] = len(sources)
    if len(sources) < 1:
        result.artifacts["note"] = "no images for internal panel reuse scan"
        return result

    tmp = Path(tempfile.mkdtemp(prefix="mc_panelreuse_"))
    try:
        _notify(0, 0, f"パネルに分割中（{len(sources)} 枚）")
        _prev, units = prepare_panel_sources(
            sources, tmp / "u", n_preview=0, panels=True, max_units=max_units
        )
        result.artifacts["panel_units"] = len(units)
        if len(units) < 2:
            result.artifacts["note"] = "fewer than two panel units"
            return result

        candidates, selection = _self_candidates(
            units, max_pairs=max_pairs, min_top_k=min_top_k, prefer_dino=prefer_dino
        )
        result.artifacts["panel_selection"] = selection
        result.artifacts["panel_candidate_pairs"] = len(candidates)

        _verify, verifier_name = make_panel_verifier(prefer_lightglue=prefer_lightglue)
        result.artifacts["panel_verifier"] = verifier_name

        groups: dict[tuple[str, str], list[tuple[PanelUnit, PanelUnit, object]]] = {}
        total = len(candidates)
        for k, (i, j, _sim) in enumerate(candidates):
            ua, ub = units[i], units[j]
            if k % 25 == 0:
                _notify(k, total, f"原稿内パネルの照合中（{k + 1}/{total} 組）")
            vr = _verify(ua.path, ub.path)
            if not vr.verified:
                continue
            key = tuple(sorted((str(ua.source), str(ub.source))))
            groups.setdefault(key, []).append((ua, ub, vr))
        _notify(total, total, "")

        panel_counts: dict[str, int] = {}
        for u in units:
            if u.box is not None:
                panel_counts[str(u.source)] = panel_counts.get(str(u.source), 0) + 1
        positions = panel_positions(units)
        reading = panel_reading_order(units)

        reuse: list[tuple[tuple, WarningItem]] = []
        for key, matches in groups.items():
            if _is_same_figure(key, matches, panel_counts):
                result.artifacts.setdefault("duplicate_file_pairs", []).append(
                    {"a": key[0], "b": key[1], "panels": len(matches)}
                )
                continue
            matches = _anchor_first(matches, reading)
            ua, ub, _vr = matches[0]
            sort_key = (_unit_order(ua, reading), _unit_order(ub, reading))
            reuse.append((sort_key, _warning_for(key, matches, positions)))
        # Anchor panel fixed on the left, in figure order: 1A↔2A, 1A↔3G, 2B↔2G, 2B↔7A …
        reuse.sort(key=lambda kw: kw[0])
        result.warnings.extend(w for _, w in reuse)
        result.artifacts["reuse_source_pairs"] = len(reuse)
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    return result


def _is_same_figure(
    key: tuple[str, str],
    matches: list[tuple[PanelUnit, PanelUnit, object]],
    panel_counts: dict[str, int],
) -> bool:
    if key[0] == key[1] or len(matches) < SAME_FIGURE_MIN_PANELS:
        return False
    smaller = min(panel_counts.get(key[0], 0), panel_counts.get(key[1], 0))
    if smaller <= 0:
        return False
    distinct_a = {str(m[0].box) for m in matches}
    distinct_b = {str(m[1].box) for m in matches}
    covered = min(len(distinct_a), len(distinct_b))
    return covered / smaller >= SAME_FIGURE_MIN_COVERAGE


def _natural_key(name: str) -> tuple:
    """image9 < image10; compare digit runs as numbers."""
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r"(\d+)", name.lower())
        if part
    )


def _unit_order(
    unit: PanelUnit, reading: dict[tuple[str, tuple[int, ...]], tuple[int, int]]
) -> tuple:
    """Figure file first, then reading order inside it (whole image before panels)."""
    rc = (-1, -1) if unit.box is None else reading.get((str(unit.source), tuple(unit.box)), (0, 0))
    return (_natural_key(Path(unit.source).name), str(unit.source), rc)


def _anchor_first(
    matches: list[tuple[PanelUnit, PanelUnit, object]],
    reading: dict[tuple[str, tuple[int, ...]], tuple[int, int]],
) -> list[tuple[PanelUnit, PanelUnit, object]]:
    """Put the earlier panel of every pair on the left, then list pairs anchor by anchor."""
    oriented = [
        (b, a, vr) if _unit_order(b, reading) < _unit_order(a, reading) else (a, b, vr)
        for a, b, vr in matches
    ]
    return sorted(
        oriented, key=lambda m: (_unit_order(m[0], reading), _unit_order(m[1], reading))
    )


def _warning_for(
    key: tuple[str, str],
    matches: list[tuple[PanelUnit, PanelUnit, object]],
    positions: PanelPositions | None = None,
) -> WarningItem:
    ua, ub, _ = matches[0]
    vr = max(matches, key=lambda m: match_rank(*m))[2]
    same_file = key[0] == key[1]
    panel_matches = [
        {
            "panel_a": m[0].box,
            "panel_b": m[1].box,
            "label_a": panel_position(m[0], positions),
            "label_b": panel_position(m[1], positions),
            "matches": m[2].num_matches,
            "inliers": m[2].inliers,
            "inverted": bool(getattr(m[2], "inverted", False)),
        }
        for m in matches
    ]
    inverted = any(pm["inverted"] for pm in panel_matches)
    listing = "／".join(
        f"{k}A {pm['label_a']} ↔ {k}B {pm['label_b']}（{pm['matches']} 点）"
        for k, pm in enumerate(panel_matches[:6], start=1)
    )
    where = "同一図の中" if same_file else "別ファイル間"
    if same_file:
        location = (
            f"{Path(key[0]).name}（{panel_matches[0]['label_a']} ↔ {panel_matches[0]['label_b']}）"
        )
    else:
        location = f"{panel_label(ua, positions)} ↔ {panel_label(ub, positions)}"
    return WarningItem(
        tag=WarningTag.IMAGE_REUSE,
        title=f"原稿内で同一写真の重複（パネル単位・{where}）",
        location=location,
        reason=(
            f"パネル単位の特徴点照合で一致 {vr.num_matches} 点"
            + (f"（うち同一の幾何変換に乗る点 {vr.inliers}）" if vr.inliers is not None else "")
            + f"。method={vr.method}。一致したパネル組 {len(matches)}: {listing}。"
            "別条件・別実験として提示したパネルが同じ写真になっていないか確認してください。"
            "全体像と拡大像のように意図的に同じ写真を再掲している場合もあります。"
            + inversion_note(inverted)
        ),
        sources=[key[0]] if same_file else [str(ua.source), str(ub.source)],
        metadata={
            "pattern_id": "P-IMAGE-DUPLICATE-INTERNAL",
            "method": vr.method,
            "precise_matches": vr.num_matches,
            "precise_inliers": vr.inliers,
            "internal_reuse": True,
            "inverted": inverted,
            "same_file": same_file,
            "panel": True,
            "panel_a": ua.box,
            "panel_b": ub.box,
            "panel_matches": panel_matches,
        },
    )
