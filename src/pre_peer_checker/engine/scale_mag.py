"""同一画像 × 矛盾する倍率／スケール — P-SCALE-MAG-INCONSISTENT."""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from pre_peer_checker.imaging.duplicate_scan import scan_image_duplicates
from pre_peer_checker.warnings import WarningItem, WarningTag

_MAG_RE = re.compile(
    # Optical context required — avoid "4x Food" / "2x medium" false positives
    r"(?P<mag>\d+(?:\.\d+)?)\s*[×xX]\s*(?:magnification|mag\.?|objective)"
    r"|(?:magnification|mag\.?|objective)\s*(?:of\s*|at\s*)?(?P<mag2>\d+(?:\.\d+)?)\s*[×xX]?",
    re.I,
)
_SCALE_RE = re.compile(
    r"(?:scale\s*bars?[,:]?\s*)?(?P<um>\d+(?:\.\d+)?)\s*(?:µm|um|μm)"
    r"(?:\s*\((?P<label>[^)]+)\))?"
    r"|(?P<um2>\d+(?:\.\d+)?)\s*(?:µm|um|μm)\s*(?:scale\s*bar)",
    re.I,
)


@dataclass(frozen=True)
class MagClaim:
    value: float
    unit: str  # magnification | scale_um
    span: str


def extract_mag_claims(texts: list[str]) -> list[MagClaim]:
    out: list[MagClaim] = []
    seen: set[tuple[str, float]] = set()
    for t in texts:
        if not t:
            continue
        blob = str(t)
        for m in _MAG_RE.finditer(blob):
            raw = m.group("mag") or m.group("mag2")
            if not raw:
                continue
            val = float(raw)
            key = ("magnification", val)
            if key in seen:
                continue
            seen.add(key)
            out.append(MagClaim(value=val, unit="magnification", span=m.group(0).strip()))
        for m in _SCALE_RE.finditer(blob):
            raw = m.group("um") or m.group("um2")
            if not raw:
                continue
            val = float(raw)
            # Skip non-scale lengths (e.g. "2x6µm rectangle") unless scale bar context
            span = m.group(0).strip()
            if "scale" not in span.lower() and not re.search(
                r"scale\s*bars?", blob[max(0, m.start() - 24) : m.end() + 8], re.I
            ):
                # Allow bare "100µm" only when preceded by "Scale bars,"
                window = blob[max(0, m.start() - 24) : m.start()]
                if not re.search(r"scale\s*bars?\s*[,:]?\s*$", window, re.I):
                    continue
            key = ("scale_um", val)
            if key in seen:
                continue
            seen.add(key)
            out.append(MagClaim(value=val, unit="scale_um", span=span))
    return out


def warnings_from_scale_mag(
    texts: list[str],
    image_paths: list[Path],
    *,
    similarity_threshold: float = 0.98,
) -> list[WarningItem]:
    """Identical (or near-identical) images with conflicting mag/scale claims."""
    claims = extract_mag_claims(texts)
    by_unit: dict[str, list[MagClaim]] = {}
    for c in claims:
        by_unit.setdefault(c.unit, []).append(c)
    conflicting = {
        unit: vals
        for unit, vals in by_unit.items()
        if len({round(v.value, 6) for v in vals}) >= 2
    }
    if not conflicting:
        return []

    paths = [Path(p) for p in image_paths if Path(p).is_file()]
    if len(paths) < 2:
        return []

    matches = scan_image_duplicates(paths, threshold=similarity_threshold)
    dup_pairs = [m for m in matches if m.likely_duplicate]
    if not dup_pairs:
        return []

    warnings: list[WarningItem] = []
    seen_pair: set[tuple[str, str]] = set()
    for m in dup_pairs:
        key = tuple(sorted((str(m.path_a.resolve()), str(m.path_b.resolve()))))
        if key in seen_pair:
            continue
        seen_pair.add(key)
        # Prefer magnification conflict; else scale bar
        unit = "magnification" if "magnification" in conflicting else next(iter(conflicting))
        vals = conflicting[unit]
        a, b = vals[0], vals[1]
        warnings.append(
            WarningItem(
                tag=WarningTag.CONFIG_MISMATCH,
                title="同一画像なのに倍率／スケール記載が矛盾",
                location=f"{m.path_a.name} ↔ {m.path_b.name}",
                reason=(
                    f"画像類似度 {m.cosine_similarity:.4f} なのに、"
                    f"記載「{a.span}」と「{b.span}」が不一致（{unit}）。"
                ),
                sources=[str(m.path_a), str(m.path_b)],
                metadata={
                    "pattern_id": "P-SCALE-MAG-INCONSISTENT",
                    "similarity": m.cosine_similarity,
                    "unit": unit,
                    "values": [a.value, b.value],
                    "spans": [a.span, b.span],
                },
            )
        )
    return warnings
