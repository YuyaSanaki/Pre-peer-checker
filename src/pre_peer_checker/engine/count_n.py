"""カウント表 n ↔ Legend — P-COUNT-N-MISMATCH."""

from __future__ import annotations

import re
from pathlib import Path

from pre_peer_checker.data.group_vectors import GroupVector
from pre_peer_checker.engine.n_and_names import N_AUTHORITY_FOOTER, N_AUTHORITY_META
from pre_peer_checker.warnings import WarningItem, WarningTag

_COUNT_PATH_RE = re.compile(
    r"(?:colony|colonies|count|counts|gate|gated|event|events|cfu|foci|focus)",
    re.I,
)
_COUNT_LEGEND_RE = re.compile(
    r"(?P<n>\d+)\s*(?:colonies|colony|events?|cells?\s+counted|cfu|foci|gates?)",
    re.I,
)
_N_PANEL_RE = re.compile(
    r"\bn\s*=\s*(?P<n>\d+)\s*(?:\((?P<panel>[A-Za-z0-9]+)\))?",
    re.I,
)


def is_count_table(path: Path) -> bool:
    return bool(_COUNT_PATH_RE.search(str(path)))


def extract_count_legend_ns(texts: list[str]) -> list[tuple[int, str | None, str]]:
    """Return (n, panel_or_None, span). Prefer colony/event phrasing."""
    out: list[tuple[int, str | None, str]] = []
    seen: set[tuple[int, str | None]] = set()
    for t in texts:
        if not t:
            continue
        blob = str(t)
        for m in _COUNT_LEGEND_RE.finditer(blob):
            n = int(m.group("n"))
            key = (n, None)
            if key in seen:
                continue
            seen.add(key)
            out.append((n, None, m.group(0).strip()))
        # Also bare n= when surrounding text looks count-ish
        if _COUNT_PATH_RE.search(blob) or "count" in blob.lower():
            for m in _N_PANEL_RE.finditer(blob):
                n = int(m.group("n"))
                panel = m.group("panel")
                key = (n, panel.upper() if panel else None)
                if key in seen:
                    continue
                seen.add(key)
                out.append((n, panel.upper() if panel else None, m.group(0).strip()))
    return out


def warnings_from_count_n(
    texts: list[str],
    vectors: list[GroupVector],
) -> list[WarningItem]:
    """Compare count-table row counts to legend colony/event n."""
    count_vecs = [v for v in vectors if is_count_table(v.source)]
    if not count_vecs:
        return []
    legend_ns = extract_count_legend_ns(texts)
    if not legend_ns:
        return []

    warnings: list[WarningItem] = []
    seen: set[tuple[str, int, int]] = set()
    for legend_n, _panel, span in legend_ns:
        for v in count_vecs:
            if v.n == legend_n:
                continue
            key = (str(v.source.resolve()), legend_n, v.n)
            if key in seen:
                continue
            seen.add(key)
            warnings.append(
                WarningItem(
                    tag=WarningTag.SAMPLE_SIZE,
                    title="カウント表の n と Legend 記載が不一致",
                    location=f"{v.source.name}[{v.group_key}]",
                    reason=(
                        f"記載「{span}」→ n={legend_n} に対し、"
                        f"カウント表群 '{v.group_key}' の有効行数は n={v.n}。"
                        f"{N_AUTHORITY_FOOTER}"
                    ),
                    sources=[str(v.source)],
                    metadata={
                        "pattern_id": "P-COUNT-N-MISMATCH",
                        "legend_n": legend_n,
                        "data_n": v.n,
                        "claim_span": span,
                        "group_key": v.group_key,
                        **N_AUTHORITY_META,
                    },
                )
            )
    return warnings
