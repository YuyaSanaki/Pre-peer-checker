"""比・正規化由来の値が過剰な桁で載っている — P-DERIVED-VALUE-PRECISION.

1/3 → 0.3333 のような循環小数や、qPCR ソフト等が出力した 0.873452 のような
計算値は「有効桁数を超えている」と指摘されやすい。科学的には無害なことが多いため、
有罪断定ではなく「Methods に比計算を明記する／表示桁を丸める」ことを促す。

比べる＝数値の小数表現のみ（決定論）。本文の開示語は文面・降格の切替にだけ使う。
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from pre_peer_checker.data.group_vectors import GroupVector
from pre_peer_checker.engine.n_and_names import is_plot_quant_table as _is_plot_quant
from pre_peer_checker.warnings import WarningItem, WarningTag

PATTERN_ID = "P-DERIVED-VALUE-PRECISION"

_MAX_DENOMINATOR = 1000
# 偶然一致の確率 ≈ 0.6·q²·10^-d をこの値以下に抑える
_CHANCE_BUDGET = 0.02

_DISCLOSURE_RE = re.compile(
    r"normali[sz]ed\s+(?:to|against|by|with)"
    r"|fold[\s-]*changes?"
    r"|ΔΔ\s*C[tTqQ]|delta[\s-]*delta\s*C[tTqQ]|\bddC[tTqQ]\b|2\s*\^?\s*[-−–]\s*ΔΔ"
    r"|comparative\s+C[tTqQ]"
    r"|divided\s+by"
    r"|\bratios?\s+(?:of|between|to)\b"
    r"|expressed\s+as\s+(?:a\s+|the\s+)?(?:ratio|fraction|proportion|percentage)"
    r"|relative\s+(?:expression|quantity|quantification|mRNA|abundance|intensity|levels?)",
    re.I,
)


_FULL_DOUBLE_SIG = 15
_FULL_DOUBLE_REL_TOL = 1e-14


@dataclass(frozen=True)
class _Digits:
    decimals: int
    significant: int
    full_double: bool  # 倍精度の全桁（Excel 数式セル等）で、短い小数に戻せない


def _decimal_digits(text: str) -> tuple[int, int] | None:
    d = Decimal(text).normalize()
    _sign, digits, exp = d.as_tuple()
    if not isinstance(exp, int):
        return None
    return max(0, -exp), (len(digits) if digits != (0,) else 0)


def _digits(v: float) -> _Digits | None:
    if not math.isfinite(v):
        return None
    parsed = _decimal_digits(repr(float(v)))
    if parsed is None:
        return None
    decimals, significant = parsed
    if significant < _FULL_DOUBLE_SIG:
        return _Digits(decimals, significant, full_double=False)
    # 65400.96390000001 のような加算誤差は短い小数（65400.9639）として扱う
    tol = _FULL_DOUBLE_REL_TOL * max(1.0, abs(v))
    for k in range(0, 9):
        r = round(v, k)
        if abs(r - v) <= tol:
            short = _decimal_digits(repr(r))
            if short is not None:
                return _Digits(short[0], short[1], full_double=False)
    return _Digits(decimals, significant, full_double=True)


def _terminates(q: int) -> bool:
    while q % 2 == 0:
        q //= 2
    while q % 5 == 0:
        q //= 5
    return q == 1


def repeating_fraction(v: float) -> Fraction | None:
    """小数表示が「分母の小さい p/q の循環小数を途中で打ち切った／丸めた」ものなら p/q。"""
    dg = _digits(v)
    if dg is None or dg.decimals < 4:
        return None
    if float(v).is_integer():
        return None
    # 打ち切り表示（0.6666）も拾うため許容幅は最下位桁 1 つ分。倍精度の全桁表示は丸め誤差分だけ許す
    if dg.full_double:
        tol = _FULL_DOUBLE_REL_TOL * max(1.0, abs(v))
    else:
        tol = 10.0 ** (-dg.decimals)
    q_cap = min(_MAX_DENOMINATOR, int(math.sqrt(_CHANCE_BUDGET / (0.6 * tol))))
    if q_cap < 3:
        return None
    frac = Fraction(abs(v)).limit_denominator(q_cap)
    if frac.denominator < 3 or _terminates(frac.denominator):
        return None
    if abs(abs(v) - float(frac)) > tol:
        return None
    return frac if v >= 0 else -frac


def _is_excess_precision(v: float) -> bool:
    dg = _digits(v)
    if dg is None or float(v).is_integer():
        return False
    return dg.decimals >= 5 and dg.significant >= 6


@dataclass(frozen=True)
class GroupPrecisionFinding:
    vector: GroupVector
    kind: str  # repeating_fraction | excess_precision
    n_flagged: int
    n_nonint: int
    examples: tuple[tuple[float, str], ...]


def classify_group(
    vec: GroupVector,
    *,
    min_flagged: int = 3,
    fraction_share: float = 0.5,
    excess_share: float = 0.8,
) -> GroupPrecisionFinding | None:
    nonint = [v for v in vec.values if math.isfinite(v) and not float(v).is_integer()]
    if len(nonint) < min_flagged:
        return None

    fracs = [(v, repeating_fraction(v)) for v in nonint]
    hits = [(v, f) for v, f in fracs if f is not None]
    if len(hits) >= min_flagged and len(hits) >= fraction_share * len(nonint):
        examples = tuple(
            (v, f"{f.numerator}/{f.denominator}") for v, f in hits[:3]
        )
        return GroupPrecisionFinding(vec, "repeating_fraction", len(hits), len(nonint), examples)

    excess = [v for v in nonint if _is_excess_precision(v)]
    if len(excess) >= min_flagged and len(excess) >= excess_share * len(nonint):
        examples = tuple((v, "") for v in excess[:3])
        return GroupPrecisionFinding(vec, "excess_precision", len(excess), len(nonint), examples)
    return None


def find_disclosure_span(texts: list[str]) -> str | None:
    for t in texts:
        if not t:
            continue
        m = _DISCLOSURE_RE.search(str(t))
        if m:
            lo = max(0, m.start() - 40)
            hi = min(len(t), m.end() + 40)
            return str(t)[lo:hi].strip()
    return None


def _fmt_example(v: float, frac: str) -> str:
    return f"{v!r} ≈ {frac}" if frac else repr(v)


def warnings_from_derived_precision(
    vectors: list[GroupVector],
    claim_texts: list[str],
    *,
    plot_only: bool = True,
) -> list[WarningItem]:
    """ソース表ごとに 1 件。本文に比・正規化の記載があれば降格（丸めの推奨だけ残す）。"""
    pool = [v for v in vectors if (not plot_only) or _is_plot_quant(v.source)]
    by_src: dict[Path, list[GroupPrecisionFinding]] = defaultdict(list)
    for vec in pool:
        finding = classify_group(vec)
        if finding is not None:
            by_src[vec.source.resolve()].append(finding)
    if not by_src:
        return []

    disclosure = find_disclosure_span(claim_texts)
    demoted = disclosure is not None

    out: list[WarningItem] = []
    for src, findings in by_src.items():
        kinds = {f.kind for f in findings}
        repeating = "repeating_fraction" in kinds
        title = (
            "比（割り算）由来の循環小数が過剰な桁数で記載"
            if repeating
            else "計算値が有効桁を超える桁数で記載"
        )
        examples = [
            _fmt_example(v, fr) for f in findings for v, fr in f.examples
        ][:4]
        groups = ", ".join(f"{f.vector.group_key}({f.n_flagged}/{f.n_nonint})" for f in findings)
        bits = [
            f"群 {groups} の値が {'小さい分母の分数（例: ' if repeating else '6 桁以上の計算値（例: '}"
            f"{', '.join(examples)}）として並んでいます。",
            "測定精度を超える桁は PubPeer 等で「有効桁数ではない」と指摘されやすい"
            "（qPCR ソフトの自動計算や比の計算では科学的に問題ないことが多い）。",
        ]
        if src.suffix.lower() in {".xlsx", ".xls"}:
            bits.append(
                "Excel はセルの表示書式ではなく保存値で判定しています"
                "（Source Data として公開するとクリックで全桁が見えます）。"
            )
        if demoted:
            title = "【降格】" + title
            bits.append(
                f"本文に比・正規化の記載あり（「{disclosure}」）。"
                "必要なら表示桁を測定精度に合わせて丸めてください。"
            )
        else:
            bits.append(
                "対応: Methods に「値は ○○ で割った比（正規化値）として算出」と明記するか、"
                "表示桁を測定精度に合わせて丸めてください。"
            )
        out.append(
            WarningItem(
                tag=WarningTag.REF_INCONSISTENCY,
                title=title,
                location=f"{src.name}[{', '.join(f.vector.group_key for f in findings)}]",
                reason=" ".join(bits),
                sources=[str(src)],
                metadata={
                    "pattern_id": PATTERN_ID,
                    "kinds": sorted(kinds),
                    "groups": [
                        {
                            "group": f.vector.group_key,
                            "kind": f.kind,
                            "n_flagged": f.n_flagged,
                            "n_nonint": f.n_nonint,
                        }
                        for f in findings
                    ],
                    "examples": examples,
                    "methods_disclosed": demoted,
                    "disclosure_span": disclosure,
                    "demoted": demoted,
                    "severity": "info" if demoted else "warning",
                },
            )
        )
    return out
