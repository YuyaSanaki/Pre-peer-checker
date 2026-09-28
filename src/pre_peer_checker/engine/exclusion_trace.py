"""除外 ID トレース — P-EXCLUSION-UNDECLARED（G13 拡張・set equality）."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from pre_peer_checker.warnings import WarningItem, WarningTag

_ID_COLS = ("id", "animal_id", "sample_id", "subject", "fly_id", "mouse_id")
_STATUS_COLS = ("included", "include", "status", "excluded", "omit", "keep")
# "excluded animals: F5, F6" / "omitted IDs: F5 and F6" / "excluded from analysis: F5, F7"
_DECLARED_ID_RE = re.compile(
    r"(?:excluded|omitted|removed|discarded)\b"
    r"(?:\s+(?:animals?|mice|flies|ids?|samples?|outliers?|from|the|analysis|based|on|"
    r"morphological|criteria|were|was))*"
    r"\s*[:#]\s*(?P<ids>[A-Za-z0-9_,;\sand-]{1,120})",
    re.I,
)
_ID_TOKEN = re.compile(r"[A-Za-z]*\d+[A-Za-z0-9]*")


def _find_col(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    cols = {str(c).lower(): c for c in df.columns}
    for key in candidates:
        if key in cols:
            return cols[key]
    return None


def extract_declared_exclusion_ids(texts: list[str]) -> set[str]:
    out: set[str] = set()
    for t in texts:
        if not t:
            continue
        for m in _DECLARED_ID_RE.finditer(str(t)):
            blob = m.group("ids") or ""
            for tok in _ID_TOKEN.findall(blob):
                if len(tok) > 24:
                    continue
                out.add(tok.upper())
    return out


def omitted_ids_from_table(path: Path) -> tuple[list[str], str | None]:
    """Return omitted IDs and id column name if a status/include column exists."""
    try:
        df = pd.read_csv(path)
    except Exception:
        return [], None
    id_col = _find_col(df, _ID_COLS)
    status_col = _find_col(df, _STATUS_COLS)
    if id_col is None or status_col is None:
        return [], None
    omitted: list[str] = []
    for _, row in df.iterrows():
        sid = str(row[id_col]).strip()
        if not sid or sid.lower() == "nan":
            continue
        raw = row[status_col]
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            included = bool(int(raw))
            if str(status_col).lower() in {"excluded", "omit"}:
                included = not included
        else:
            s = str(raw).strip().lower()
            if s in {"0", "false", "no", "n", "excluded", "omit", "omitted", "out"}:
                included = False
            elif s in {"1", "true", "yes", "y", "included", "keep", "in"}:
                included = True
            else:
                continue
        if not included:
            omitted.append(sid.upper())
    return omitted, str(id_col)


def compare_exclusion_id_sets(
    declared: set[str] | list[str],
    omitted: set[str] | list[str],
) -> dict[str, list[str]]:
    """Set-equality diff for exclusion lists.

    - ``undeclared``: in table omitted, not in Methods
    - ``phantom``: in Methods, not in table omitted
    """
    d = {str(x).upper() for x in declared}
    o = {str(x).upper() for x in omitted}
    return {
        "undeclared": sorted(o - d),
        "phantom": sorted(d - o),
        "matched": sorted(d & o),
    }


def warnings_from_exclusion_id_trace(
    texts: list[str],
    table_paths: list[Path],
    *,
    exclusion_mentioned: bool,
) -> list[WarningItem]:
    """When exclusion is claimed, Methods ID list must match table omitted set."""
    if not exclusion_mentioned:
        return []
    declared = extract_declared_exclusion_ids(texts)
    warnings: list[WarningItem] = []
    for path in table_paths:
        omitted, id_col = omitted_ids_from_table(path)
        if not omitted and not declared:
            continue
        diff = compare_exclusion_id_sets(declared, omitted)
        undeclared = diff["undeclared"]
        phantom = diff["phantom"]
        if not undeclared and not phantom:
            continue

        parts: list[str] = []
        if undeclared:
            parts.append(
                "テーブル除外 ID "
                + ", ".join(undeclared[:8])
                + ("…" if len(undeclared) > 8 else "")
                + " が Methods に無い"
            )
        if phantom:
            parts.append(
                "Methods 記載 ID "
                + ", ".join(phantom[:8])
                + ("…" if len(phantom) > 8 else "")
                + " がテーブル除外に無い"
            )
        title = (
            "除外 ID リストが Methods と一致しない"
            if (undeclared and phantom) or phantom
            else "除外 ID が Methods に列挙されていない"
        )
        warnings.append(
            WarningItem(
                tag=WarningTag.SAMPLE_SIZE,
                title=title,
                location=f"{path.name} ({id_col or 'id'})",
                reason=(
                    "除外基準の記載はある一方、"
                    + "；".join(parts)
                    + "。除外リストの完全一致を確認してください。"
                ),
                sources=[str(path)],
                metadata={
                    "pattern_id": "P-EXCLUSION-UNDECLARED",
                    "check": "exclusion_id_trace",
                    "omitted_ids": omitted,
                    "undeclared_ids": undeclared,
                    "phantom_ids": phantom,
                    "matched_ids": diff["matched"],
                    "declared_ids": sorted(declared),
                    "exclusion_mentioned": True,
                    "set_equality": False,
                },
            )
        )
    return warnings
