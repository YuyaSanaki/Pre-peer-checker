#!/usr/bin/env python3
"""Calibrate LightGlue min_matches on synthetic identical / crop / unrelated pairs.

Usage:
  .venv/bin/python scripts/dev_lightglue_threshold_calib.py --features aliked
  .venv/bin/python scripts/dev_lightglue_threshold_calib.py --features superpoint \\
    --write outputs/lightglue_calib_superpoint.json

Unrelated synthetics must NOT share block layout with the probe image — aligned
dummy geometry yields spurious SuperPoint matches and inflates neg_max.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def _probe_image(size: int = 256) -> np.ndarray:
    """Structured probe with irregular features (not a regular grid)."""
    rng = np.random.default_rng(42)
    base = (rng.random((size, size)) * 40 + 80).astype(np.uint8)
    img = Image.fromarray(base).convert("L")
    draw = ImageDraw.Draw(img)
    # Irregular shapes / text-like bars
    draw.rectangle([30, 40, 110, 120], fill=220)
    draw.ellipse([140, 30, 230, 130], outline=20, width=6)
    draw.line([20, 200, 240, 160], fill=10, width=5)
    draw.polygon([(50, 180), (90, 240), (20, 240)], fill=180)
    draw.rectangle([160, 170, 240, 240], fill=30)
    # Small checker in one corner only
    for y in range(0, 40, 8):
        for x in range(0, 40, 8):
            if (x // 8 + y // 8) % 2 == 0:
                draw.rectangle([x, y, x + 7, y + 7], fill=250)
    return np.asarray(img, dtype=np.uint8)


def _unrelated_noise(size: int = 256, seed: int = 99) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.random((size, size)) * 255).astype(np.uint8)


def _unrelated_shapes(size: int = 256, seed: int = 777) -> np.ndarray:
    """Different geometry from probe (concentric rings + stripes)."""
    rng = np.random.default_rng(seed)
    base = (rng.random((size, size)) * 30 + 100).astype(np.uint8)
    img = Image.fromarray(base).convert("L")
    draw = ImageDraw.Draw(img)
    for r in range(20, 120, 15):
        draw.ellipse([128 - r, 128 - r, 128 + r, 128 + r], outline=int(20 + r) % 255, width=3)
    for y in range(0, size, 12):
        draw.line([0, y, size, y], fill=200, width=2)
    return np.asarray(img, dtype=np.uint8)


def _make_pairs(tmp: Path) -> list[tuple[str, Path, Path, bool, str]]:
    """Return (name, a, b, expect_positive, role)."""
    base = _probe_image(256)
    identical = tmp / "identical.png"
    Image.fromarray(base).save(identical)
    copy = tmp / "identical_copy.png"
    Image.fromarray(base).save(copy)

    crop = tmp / "crop.png"
    Image.fromarray(base[40:200, 40:200]).save(crop)

    # Soft positive: additive noise
    rng = np.random.default_rng(7)
    noisy = np.clip(base.astype(np.float32) + rng.normal(0, 10, base.shape), 0, 255).astype(
        np.uint8
    )
    noisy_p = tmp / "noisy.png"
    Image.fromarray(noisy).save(noisy_p)

    # Hard positive (informational): 90° crop — may score lower; not used for floor
    crop_rot = tmp / "crop_rot90.png"
    Image.fromarray(
        np.asarray(Image.fromarray(base[40:200, 40:200]).rotate(90, expand=True))
    ).save(crop_rot)

    un_noise = tmp / "unrelated_noise.png"
    Image.fromarray(_unrelated_noise(256, 99)).save(un_noise)
    un_shapes = tmp / "unrelated_shapes.png"
    Image.fromarray(_unrelated_shapes(256, 777)).save(un_shapes)
    un_noise2 = tmp / "unrelated_noise2.png"
    Image.fromarray(_unrelated_noise(256, 1234)).save(un_noise2)

    return [
        ("identical", identical, copy, True, "must_pos"),
        ("crop", identical, crop, True, "must_pos"),
        ("noisy", identical, noisy_p, True, "must_pos"),
        ("crop_rot90", identical, crop_rot, True, "hard_pos"),
        ("unrel_noise", identical, un_noise, False, "must_neg"),
        ("unrel_shapes", identical, un_shapes, False, "must_neg"),
        ("unrel_pair", un_noise, un_noise2, False, "must_neg"),
    ]


def _suggest_threshold(rows: list[dict]) -> tuple[int, str]:
    must_pos = [r["n_matches"] for r in rows if r["role"] == "must_pos"]
    must_neg = [r["n_matches"] for r in rows if r["role"] == "must_neg"]
    hard_pos = [r["n_matches"] for r in rows if r["role"] == "hard_pos"]
    if not must_pos or not must_neg:
        return 35, "fallback"
    lo = max(must_neg) + 1
    hi = min(must_pos)
    note = f"must_neg_max+1={lo} must_pos_min={hi}"
    if hard_pos:
        note += f" hard_pos_min={min(hard_pos)}"
    if lo > hi:
        return int(round((max(must_neg) + min(must_pos)) / 2)), f"overlap; {note}"
    for c in (30, 35, 40, 45, 50):
        if lo <= c <= hi:
            # Prefer margin above neg when room
            if c - max(must_neg) >= 5:
                return c, note
    return max(lo, min(hi, 35)), note


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--features",
        choices=["superpoint", "aliked"],
        default=None,
        help="Extractor to calibrate (default: the one selected by the usage profile)",
    )
    parser.add_argument(
        "--min-candidates",
        type=int,
        nargs="+",
        default=[15, 20, 25, 30, 35, 40, 50, 60, 80],
    )
    parser.add_argument(
        "--write",
        type=Path,
        default=None,
        help="Optional JSON report path (e.g. outputs/lightglue_calib.json)",
    )
    args = parser.parse_args()

    from pre_peer_checker.imaging.lightglue_match import (
        active_lightglue_features,
        default_min_matches,
        lightglue_available,
        verify_image_pair,
    )

    if not lightglue_available():
        print("LightGlue not installed — run ./install.sh to install it.")
        return 2
    features = args.features or active_lightglue_features()
    current_default = default_min_matches(features)
    print(f"extractor: {features}")

    with tempfile.TemporaryDirectory(prefix="lg_calib_") as td:
        tmp = Path(td)
        pairs = _make_pairs(tmp)
        print(
            f"{'pair':12} {'role':10} {'n':>6}  "
            + "  ".join(f"≥{t}" for t in args.min_candidates)
        )
        rows: list[dict] = []
        for name, a, b, expect_pos, role in pairs:
            r = verify_image_pair(
                a,
                b,
                prefer_lightglue=True,
                require_lightglue=True,
                min_matches=0,
                features=features,
            )
            n = int(r.num_matches)
            flags = {t: n >= t for t in args.min_candidates}
            print(
                f"{name:12} {role:10} {n:6d}  "
                + "  ".join(f"{('Y' if flags[t] else 'n'):>3}" for t in args.min_candidates)
            )
            print(f"  method={r.method} detail={r.detail}")
            rows.append(
                {
                    "pair": name,
                    "role": role,
                    "n_matches": n,
                    "expect_positive": expect_pos,
                    "method": r.method,
                    "detail": r.detail,
                    "pass_at": {str(k): v for k, v in flags.items()},
                }
            )

        suggested, note = _suggest_threshold(rows)
        must_pos_min = min(r["n_matches"] for r in rows if r["role"] == "must_pos")
        must_neg_max = max(r["n_matches"] for r in rows if r["role"] == "must_neg")
        hard = [r["n_matches"] for r in rows if r["role"] == "hard_pos"]
        print()
        print(f"must_pos_min={must_pos_min}  must_neg_max={must_neg_max}")
        if hard:
            print(f"hard_pos (rot90 etc.) min={min(hard)} — informational; rotation NCC covers many cases")
        print(f"Suggested MIN_MATCHES_BY_FEATURES[{features!r}]: {suggested}  ({note})")
        print(f"Current package default: {current_default}")
        ok_current = must_neg_max < current_default <= must_pos_min
        print(f"Current default separates must_pos/must_neg: {ok_current}")

        if args.write:
            args.write.parent.mkdir(parents=True, exist_ok=True)
            report = {
                "schema_version": "1.2",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "features": features,
                "device_hint": rows[0]["detail"] if rows else "",
                "must_pos_min": must_pos_min,
                "must_neg_max": must_neg_max,
                "hard_pos_min": min(hard) if hard else None,
                "suggested_min_matches": suggested,
                "suggest_note": note,
                "current_default": current_default,
                "current_separates_must": ok_current,
                "thresholds_tested": list(args.min_candidates),
                "pairs": rows,
            }
            args.write.write_text(
                json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"Wrote {args.write}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
