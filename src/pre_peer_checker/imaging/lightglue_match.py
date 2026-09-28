"""DINOv2 候補ペアの精密マッチ再検証.

優先: LightGlue (+ SuperPoint) — `pip install 'pre-peer-checker[imaging]'` かつ lightglue
次点: OpenCV ORB + RANSAC ホモグラフィ
常時: マルチスケール正規化相互相関（依存なし）
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass
class PreciseMatchResult:
    verified: bool
    num_matches: int
    score: float
    method: str
    detail: str = ""


def lightglue_available() -> bool:
    try:
        import torch  # noqa: F401
        from lightglue import LightGlue, SuperPoint  # noqa: F401

        return True
    except Exception:
        return False


def opencv_available() -> bool:
    try:
        import cv2  # noqa: F401

        return True
    except Exception:
        return False


def _load_gray_u8(path: Path, max_side: int = 512) -> np.ndarray:
    img = Image.open(path).convert("L")
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.BILINEAR)
    return np.asarray(img, dtype=np.uint8)


def _verify_ncc(path_a: Path, path_b: Path, *, min_score: float = 0.92) -> PreciseMatchResult:
    """Multi-scale normalized cross-correlation (no extra deps)."""
    a = _load_gray_u8(path_a, max_side=256).astype(np.float32)
    b = _load_gray_u8(path_b, max_side=256).astype(np.float32)
    best = -1.0
    best_scale = 1.0

    def _ncc(x: np.ndarray, y: np.ndarray) -> float:
        if x.shape != y.shape:
            y_img = Image.fromarray(y.astype(np.uint8)).resize(
                (x.shape[1], x.shape[0]), Image.Resampling.BILINEAR
            )
            y = np.asarray(y_img, dtype=np.float32)
        if np.array_equal(x, y):
            return 1.0
        x = x - x.mean()
        y = y - y.mean()
        nx = float(np.linalg.norm(x))
        ny = float(np.linalg.norm(y))
        if nx < 1e-6 and ny < 1e-6:
            # both nearly constant — treat as match if means close
            return 1.0 if abs(float(x.mean()) - float(y.mean())) < 1e-3 else 0.0
        if nx < 1e-6 or ny < 1e-6:
            return 0.0
        return float(np.dot(x.ravel(), y.ravel()) / (nx * ny))

    for scale in (0.7, 0.85, 1.0, 1.15, 1.3):
        bh, bw = b.shape
        nh, nw = max(8, int(bh * scale)), max(8, int(bw * scale))
        b_s = np.asarray(
            Image.fromarray(b.astype(np.uint8)).resize((nw, nh), Image.Resampling.BILINEAR),
            dtype=np.float32,
        )
        # Compare centered crops of equal size
        side = min(a.shape[0], a.shape[1], b_s.shape[0], b_s.shape[1])
        if side < 16:
            continue
        ac = a[
            (a.shape[0] - side) // 2 : (a.shape[0] - side) // 2 + side,
            (a.shape[1] - side) // 2 : (a.shape[1] - side) // 2 + side,
        ]
        bc = b_s[
            (b_s.shape[0] - side) // 2 : (b_s.shape[0] - side) // 2 + side,
            (b_s.shape[1] - side) // 2 : (b_s.shape[1] - side) // 2 + side,
        ]
        score = _ncc(ac, bc)
        if score > best:
            best, best_scale = score, scale

    verified = best >= min_score
    return PreciseMatchResult(
        verified=verified,
        num_matches=1 if verified else 0,
        score=best,
        method="ncc-multiscale",
        detail=f"best_scale={best_scale}",
    )


def _verify_orb(path_a: Path, path_b: Path, *, min_inliers: int = 12) -> PreciseMatchResult:
    import cv2

    a = _load_gray_u8(path_a)
    b = _load_gray_u8(path_b)
    orb = cv2.ORB_create(nfeatures=1500)
    kp1, des1 = orb.detectAndCompute(a, None)
    kp2, des2 = orb.detectAndCompute(b, None)
    if des1 is None or des2 is None or len(kp1) < 8 or len(kp2) < 8:
        return PreciseMatchResult(False, 0, 0.0, "orb-ransac", "insufficient keypoints")

    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    raw = bf.knnMatch(des1, des2, k=2)
    good = []
    for pair in raw:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < 0.75 * n.distance:
            good.append(m)
    if len(good) < min_inliers:
        return PreciseMatchResult(False, len(good), 0.0, "orb-ransac", "few good matches")

    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    _H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    inliers = int(mask.sum()) if mask is not None else 0
    ratio = inliers / max(len(good), 1)
    verified = inliers >= min_inliers and ratio >= 0.25
    return PreciseMatchResult(
        verified=verified,
        num_matches=inliers,
        score=ratio,
        method="orb-ransac",
        detail=f"good={len(good)} inliers={inliers}",
    )


_LIGHTGLUE_CACHE: dict[str, object] = {}


# Calibrated on CUDA SuperPoint+LightGlue (1024 kpts), 2026-09-26:
#   must_pos_min≈153 (identical/crop/noisy), must_neg_max≈17 (noise/shapes),
#   hard_pos rot90≈47 (informational; rotation also covered by NCC).
# Valid band ≈18–153; 35 keeps margin above negatives and passes rot90.
# Re-run: scripts/dev_lightglue_threshold_calib.py --write outputs/lightglue_calib.json
DEFAULT_MIN_MATCHES = 35
DEFAULT_MIN_MATCH_RATIO = 0.0  # absolute count gate is primary for LightGlue


def _verify_lightglue(
    path_a: Path,
    path_b: Path,
    *,
    min_matches: int = DEFAULT_MIN_MATCHES,
    device: str | None = None,
) -> PreciseMatchResult:
    import torch
    from lightglue import LightGlue, SuperPoint
    from lightglue.utils import load_image, rbd

    if device is None:
        if torch.cuda.is_available():
            device = "cuda"
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"

    if "extractor" not in _LIGHTGLUE_CACHE:
        extractor = SuperPoint(max_num_keypoints=1024).eval().to(device)
        matcher = LightGlue(features="superpoint").eval().to(device)
        _LIGHTGLUE_CACHE["extractor"] = extractor
        _LIGHTGLUE_CACHE["matcher"] = matcher
        _LIGHTGLUE_CACHE["device"] = device
    extractor = _LIGHTGLUE_CACHE["extractor"]  # type: ignore[assignment]
    matcher = _LIGHTGLUE_CACHE["matcher"]  # type: ignore[assignment]
    device = str(_LIGHTGLUE_CACHE["device"])

    img0 = load_image(str(path_a)).to(device)
    img1 = load_image(str(path_b)).to(device)
    with torch.inference_mode():
        feats0 = extractor.extract(img0)
        feats1 = extractor.extract(img1)
        matches01 = matcher({"image0": feats0, "image1": feats1})
        feats0, feats1, matches01 = [rbd(x) for x in (feats0, feats1, matches01)]
        n = int(matches01["matches"].shape[0])
    verified = n >= min_matches
    return PreciseMatchResult(
        verified=verified,
        num_matches=n,
        score=float(n),
        method="lightglue+superpoint",
        detail=f"device={device}; min_matches={min_matches}",
    )


def verify_image_pair(
    path_a: Path | str,
    path_b: Path | str,
    *,
    prefer_lightglue: bool = True,
    require_lightglue: bool = False,
    min_matches: int = DEFAULT_MIN_MATCHES,
) -> PreciseMatchResult:
    """Re-verify a candidate duplicate pair with the best available matcher.

    ``require_lightglue=True``: do not silently fall back to ORB/NCC when LightGlue
    is unavailable or fails (returns verified=False with method marker).
    """
    a, b = Path(path_a), Path(path_b)
    if prefer_lightglue and lightglue_available():
        try:
            return _verify_lightglue(a, b, min_matches=min_matches)
        except Exception as exc:  # noqa: BLE001
            if require_lightglue:
                return PreciseMatchResult(
                    verified=False,
                    num_matches=0,
                    score=0.0,
                    method="lightglue-required-failed",
                    detail=str(exc),
                )
            fallback_note = f"lightglue_failed:{exc}"
    elif prefer_lightglue and require_lightglue:
        return PreciseMatchResult(
            verified=False,
            num_matches=0,
            score=0.0,
            method="lightglue-required-missing",
            detail="lightglue package not available",
        )
    else:
        fallback_note = ""

    if opencv_available():
        try:
            result = _verify_orb(a, b)
            if fallback_note:
                result.detail = f"{result.detail}; {fallback_note}"
            return result
        except Exception as exc:  # noqa: BLE001
            fallback_note = f"{fallback_note}; orb_failed:{exc}".strip("; ")

    result = _verify_ncc(a, b)
    if fallback_note:
        result.detail = f"{result.detail}; {fallback_note}"
    return result


def confirm_candidate_matches(
    candidates: list,
    *,
    prefer_lightglue: bool = True,
    require_lightglue: bool = False,
    min_matches: int = DEFAULT_MIN_MATCHES,
    only_likely: bool = True,
) -> list:
    """Attach precise verification onto ImagePairMatch-like objects.

    Mutates and returns the same list: sets likely_duplicate from precise result
    when a candidate was above the coarse threshold (or all if only_likely=False).
    """
    for m in candidates:
        if only_likely and not getattr(m, "likely_duplicate", False):
            continue
        precise = verify_image_pair(
            m.path_a,
            m.path_b,
            prefer_lightglue=prefer_lightglue,
            require_lightglue=require_lightglue,
            min_matches=min_matches,
        )
        m.precise_matches = precise.num_matches
        m.precise_score = precise.score
        m.precise_method = precise.method
        m.precise_verified = precise.verified
        m.likely_duplicate = bool(precise.verified)
        m.method = f"{m.method}+{precise.method}"
    return candidates
