"""データ指紋（ソート済み数値ベクトルのハッシュ＋群記述統計）。

ファイル名に依存しない Entity Linking（Phase 6B Tier1/2）の根拠。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from pre_peer_checker.data.group_vectors import GroupVector


@dataclass(frozen=True)
class DataFingerprint:
    """One group vector's content identity."""

    value_hash: str
    n: int
    mean: float
    sd: float
    median: float
    group_key: str
    source: Path
    decimals: int = 6

    @property
    def empty(self) -> bool:
        return self.n <= 0


def _sorted_rounded(values: tuple[float, ...], decimals: int) -> tuple[float, ...]:
    return tuple(sorted(round(float(v), decimals) for v in values))


def value_hash(values: tuple[float, ...], *, decimals: int = 6) -> str:
    rounded = _sorted_rounded(values, decimals)
    payload = ",".join(f"{v:.{decimals}f}" for v in rounded).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def fingerprint_from_values(
    values: tuple[float, ...],
    *,
    group_key: str = "all",
    source: Path | str = ".",
    decimals: int = 6,
) -> DataFingerprint:
    rounded = _sorted_rounded(values, decimals)
    n = len(rounded)
    if n == 0:
        return DataFingerprint(
            value_hash=value_hash((), decimals=decimals),
            n=0,
            mean=0.0,
            sd=0.0,
            median=0.0,
            group_key=str(group_key),
            source=Path(source),
            decimals=decimals,
        )
    mean = sum(rounded) / n
    if n > 1:
        var = sum((x - mean) ** 2 for x in rounded) / (n - 1)
        sd = var**0.5
    else:
        sd = 0.0
    mid = n // 2
    median = (
        rounded[mid]
        if n % 2 == 1
        else (rounded[mid - 1] + rounded[mid]) / 2.0
    )
    return DataFingerprint(
        value_hash=value_hash(values, decimals=decimals),
        n=n,
        mean=float(mean),
        sd=float(sd),
        median=float(median),
        group_key=str(group_key),
        source=Path(source),
        decimals=decimals,
    )


def fingerprint_from_vector(
    vector: GroupVector, *, decimals: int = 6
) -> DataFingerprint:
    return fingerprint_from_values(
        vector.values,
        group_key=vector.group_key,
        source=vector.source,
        decimals=decimals,
    )


def exact_match(a: DataFingerprint, b: DataFingerprint) -> bool:
    """Tier1: identical sorted value multiset (via hash) and n."""
    if a.empty or b.empty:
        return False
    return a.n == b.n and a.value_hash == b.value_hash


def values_exact_match(
    a: tuple[float, ...] | list[float],
    b: tuple[float, ...] | list[float],
    *,
    decimals_candidates: tuple[int, ...] = (6, 4, 3, 2),
) -> bool:
    """Tier1 with float noise tolerance (graph sheets often round to 4–5 dp)."""
    if len(a) != len(b) or not a:
        return False
    ta, tb = tuple(a), tuple(b)
    for d in decimals_candidates:
        if value_hash(ta, decimals=d) == value_hash(tb, decimals=d):
            return True
    return False


def stats_close(
    a: DataFingerprint,
    b: DataFingerprint,
    *,
    mean_eps: float = 1e-3,
    sd_eps: float = 1e-3,
    require_n: bool = True,
) -> bool:
    """Tier2: n (optional) + mean/SD within tolerance."""
    if a.empty or b.empty:
        return False
    if require_n and a.n != b.n:
        return False
    return abs(a.mean - b.mean) <= mean_eps and abs(a.sd - b.sd) <= sd_eps
