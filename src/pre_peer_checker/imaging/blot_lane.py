"""ゲル／ブロット帯レーン流用 — P-BLOT-LANE-REUSE の「比べる」."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

import numpy as np
from PIL import Image

from pre_peer_checker.imaging.partial_match import _ncc_at
from pre_peer_checker.warnings import WarningItem, WarningTag


@dataclass
class LaneMatch:
    path_a: Path
    path_b: Path
    lane_a: int
    lane_b: int
    score: float


@dataclass
class BlotLaneScanResult:
    warnings: list[WarningItem] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)


def _gray(path: Path, max_side: int = 320) -> np.ndarray:
    img = Image.open(path).convert("L")
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.BILINEAR,
        )
    return np.asarray(img, dtype=np.float32)


def _lane_strips(arr: np.ndarray, n_lanes: int = 4) -> list[np.ndarray]:
    h, w = arr.shape
    strips: list[np.ndarray] = []
    for i in range(n_lanes):
        x0 = int(i * w / n_lanes)
        x1 = int((i + 1) * w / n_lanes)
        if x1 - x0 < 4:
            continue
        # trim 10% margins to avoid gel edges
        y0, y1 = int(h * 0.08), int(h * 0.92)
        strips.append(arr[y0:y1, x0:x1])
    return strips


def _resize_match(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ha, wa = a.shape
    hb, wb = b.shape
    th, tw = min(ha, hb), min(wa, wb)
    aa = np.asarray(
        Image.fromarray(a.astype(np.uint8)).resize((tw, th), Image.Resampling.BILINEAR),
        dtype=np.float32,
    )
    bb = np.asarray(
        Image.fromarray(b.astype(np.uint8)).resize((tw, th), Image.Resampling.BILINEAR),
        dtype=np.float32,
    )
    return aa, bb


def compare_lane_banks(
    path_a: Path | str,
    path_b: Path | str,
    *,
    n_lanes: int = 4,
    threshold: float = 0.94,
) -> list[LaneMatch]:
    """Compare vertical lane strips between two blot-like images."""
    a, b = Path(path_a), Path(path_b)
    sa = _lane_strips(_gray(a), n_lanes=n_lanes)
    sb = _lane_strips(_gray(b), n_lanes=n_lanes)
    hits: list[LaneMatch] = []
    for i, la in enumerate(sa):
        for j, lb in enumerate(sb):
            aa, bb = _resize_match(la, lb)
            score = _ncc_at(aa, bb)
            if score >= threshold:
                hits.append(LaneMatch(a, b, i, j, score))
    return hits


def _full_image_similar(path_a: Path, path_b: Path, *, threshold: float = 0.97) -> bool:
    ga, gb = _gray(path_a), _gray(path_b)
    aa, bb = _resize_match(ga, gb)
    return _ncc_at(aa, bb) >= threshold


def scan_blot_lane_reuse(
    image_paths: list[Path | str],
    *,
    n_lanes: int | None = None,
    threshold: float = 0.94,
    legend_has_reuse_note: bool = False,
) -> BlotLaneScanResult:
    """Emit P-BLOT-LANE-REUSE when a lane strip matches but whole images differ."""
    result = BlotLaneScanResult()
    paths = [Path(p) for p in image_paths if Path(p).is_file()]
    result.artifacts["n_images"] = len(paths)
    if legend_has_reuse_note:
        result.artifacts["note"] = "reuse/same-gel note present — blot-lane scan skipped"
        return result
    if len(paths) < 2:
        result.artifacts["note"] = "need ≥2 images"
        return result

    lane_counts = [n_lanes] if n_lanes else [3, 4, 5]
    seen_pairs: set[tuple[str, str]] = set()
    matches: list[LaneMatch] = []
    for a, b in combinations(paths, 2):
        pair_key = tuple(sorted((str(a.resolve()), str(b.resolve()))))
        if pair_key in seen_pairs:
            continue
        if _full_image_similar(a, b):
            continue  # full duplicate → leave to IMAGE-REUSE / DATA-SWAP
        best: LaneMatch | None = None
        for nl in lane_counts:
            for m in compare_lane_banks(a, b, n_lanes=nl, threshold=threshold):
                if best is None or m.score > best.score:
                    best = m
        if best is not None:
            seen_pairs.add(pair_key)
            matches.append(best)

    result.artifacts["lane_matches"] = len(matches)
    for m in matches:
        result.warnings.append(
            WarningItem(
                tag=WarningTag.IMAGE_REUSE,
                title="ブロット／ゲル帯レーンの流用疑い",
                location=f"{m.path_a.name}[lane{m.lane_a}] ↔ {m.path_b.name}[lane{m.lane_b}]",
                reason=(
                    f"レーン帯 NCC={m.score:.4f}。全体画像は非同一だが帯領域が一致。"
                    "同一ゲルの正当な再表示なら明示を確認してください。"
                ),
                sources=[str(m.path_a), str(m.path_b)],
                metadata={
                    "pattern_id": "P-BLOT-LANE-REUSE",
                    "score": m.score,
                    "lane_a": m.lane_a,
                    "lane_b": m.lane_b,
                    "method": "lane_ncc",
                },
            )
        )
    return result
