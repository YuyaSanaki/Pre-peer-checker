"""DINOv2 候補ペアの精密マッチ再検証.

優先: LightGlue — `pip install 'pre-peer-checker[imaging]'` かつ lightglue
      特徴点抽出器は利用区分で切替（academic: SuperPoint / commercial: ALIKED）
次点: OpenCV ORB + RANSAC ホモグラフィ
常時: マルチスケール正規化相互相関（依存なし）

どの照合器でも片方を明暗反転した版とも比べ、反転版の方がよく一致すれば
``inverted=True``（反転して流用）とする。反転への強さは照合器ごとに違うため
（SuperPoint はほぼ不変、ALIKED はまちまち、ORB / NCC は一致しない）、偶然に任せない。
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps


@dataclass
class PreciseMatchResult:
    verified: bool
    num_matches: int
    score: float
    method: str
    detail: str = ""
    inliers: int | None = None
    inverted: bool = False


INVERTED_METHOD_SUFFIX = "+inverted"

_INVERTED_DIR: Path | None = None
_INVERTED_COPIES: dict[tuple[str, int, int], Path] = {}


def inverted_copy(path: Path | str) -> Path:
    """明暗反転した PNG（プロセス内キャッシュ・終了時に削除）."""
    global _INVERTED_DIR
    src = Path(path).resolve()
    st = src.stat()
    key = (str(src), st.st_mtime_ns, st.st_size)
    hit = _INVERTED_COPIES.get(key)
    if hit is not None and hit.exists():
        return hit
    if _INVERTED_DIR is None:
        _INVERTED_DIR = Path(tempfile.mkdtemp(prefix="mc_inverted_"))
        atexit.register(shutil.rmtree, _INVERTED_DIR, ignore_errors=True)
    img = Image.open(src)
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    out = _INVERTED_DIR / f"{len(_INVERTED_COPIES)}_{src.stem}.png"
    ImageOps.invert(img).save(out)
    _INVERTED_COPIES[key] = out
    return out


def pick_orientation(
    normal: PreciseMatchResult, inverted: PreciseMatchResult | None
) -> PreciseMatchResult:
    """通常比較と反転比較のうち、よく一致した方を返す（反転側は ``inverted=True``）."""
    if inverted is None or not inverted.verified:
        return normal
    if normal.verified and (normal.num_matches, normal.score) >= (
        inverted.num_matches,
        inverted.score,
    ):
        return normal
    return replace(inverted, inverted=True, method=inverted.method + INVERTED_METHOD_SUFFIX)


def with_inversion(
    verify: Callable[[Path, Path], PreciseMatchResult],
    path_a: Path,
    path_b: Path,
    *,
    verify_inverted: Callable[[Path, Path], PreciseMatchResult] | None = None,
) -> PreciseMatchResult:
    """``verify(a, b)`` と ``verify_inverted(a, 反転 b)``（既定は ``verify``）の良い方."""
    normal = verify(path_a, path_b)
    try:
        flipped = (verify_inverted or verify)(path_a, inverted_copy(path_b))
    except Exception:  # noqa: BLE001
        flipped = None
    return pick_orientation(normal, flipped)


def inversion_note(inverted: bool | None) -> str:
    if not inverted:
        return ""
    return "片方の明暗（白黒）を反転すると一致します。反転して流用していないか確認してください。"


def lightglue_available() -> bool:
    try:
        import torch  # noqa: F401
        from lightglue import LightGlue  # noqa: F401

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


_LIGHTGLUE_CACHE: dict[str, tuple[object, object, str]] = {}

LIGHTGLUE_MAX_KEYPOINTS = 1024

# Per-extractor min_matches, calibrated on CUDA (1024 kpts), 2026-09-28:
#   superpoint: must_pos_min=153, must_neg_max=17, rot90=47  → 35
#   aliked:     must_pos_min=386, must_neg_max=32, rot90=32  → 50 (same ≥18 margin
#               above negatives; rot90 is not separable from negatives with ALIKED)
# Re-run: scripts/dev_lightglue_threshold_calib.py --features <name>
# Record: fixtures/gold/lightglue_calib/calib_summary.json
MIN_MATCHES_BY_FEATURES: dict[str, int] = {
    "superpoint": 35,
    "aliked": 50,
}
DEFAULT_MIN_MATCH_RATIO = 0.0  # absolute count gate is primary for LightGlue

# Cross-set panel scans (H3 corpus, thousands of pairs) gate on RANSAC homography
# inliers instead of raw matches. 2026-09-28, Supp docx panels × 4 PubPeer extracts
# (~4200 pairs each):
#   aliked:     same-photo 68..207 inliers at ratio >= 0.97; unrelated raw <= 79,
#               ratio <= 0.72
#   superpoint: same-photo 199..324 inliers; unrelated glyph/label matches reach
#               ratio 1.0 but <= 37 inliers
PANEL_MIN_INLIERS_BY_FEATURES: dict[str, int] = {
    "aliked": 50,
    "superpoint": 60,
}
PANEL_MIN_INLIER_RATIO = 0.85

# The inverted pass sees more unrelated pairs (the coarse stage also compares against
# inverted images), so LightGlue must also agree on one homography there.
# 2026-09-29, 14 figure images (CUDA, 1024 kpts), a vs inverted(b):
#   true inverted copy (JPEG q70): superpoint >= 671 matches, aliked >= 824, ratio >= 0.998
#   unrelated (182 pairs): raw gate alone passes 15 (superpoint) / 9 (aliked);
#                          inlier ratio <= 0.714 / 0.397 → none pass at 0.85
INVERTED_MIN_INLIER_RATIO = PANEL_MIN_INLIER_RATIO


def active_lightglue_features() -> str:
    from pre_peer_checker.usage_profile import lightglue_features

    return lightglue_features()


def default_min_matches(features: str | None = None) -> int:
    return MIN_MATCHES_BY_FEATURES[features or active_lightglue_features()]


def _load_lightglue(features: str, device: str) -> tuple[object, object]:
    from lightglue import ALIKED, LightGlue, SuperPoint

    extractors = {"superpoint": SuperPoint, "aliked": ALIKED}
    extractor = extractors[features](max_num_keypoints=LIGHTGLUE_MAX_KEYPOINTS)
    matcher = LightGlue(features=features)
    return extractor.eval().to(device), matcher.eval().to(device)


def _lightglue_models(features: str, device: str | None = None) -> tuple[object, object, str]:
    if features not in _LIGHTGLUE_CACHE:
        if device is None:
            from pre_peer_checker.accel import torch_device

            device = torch_device()
        extractor, matcher = _load_lightglue(features, device)
        _LIGHTGLUE_CACHE[features] = (extractor, matcher, device)
    return _LIGHTGLUE_CACHE[features]


def _verify_lightglue(
    path_a: Path,
    path_b: Path,
    *,
    features: str | None = None,
    min_matches: int | None = None,
    device: str | None = None,
    min_inlier_ratio: float = 0.0,
) -> PreciseMatchResult:
    """``min_inlier_ratio`` > 0: also require ``min_matches`` homography inliers at that
    ratio (raw gate only when OpenCV is missing)."""
    import torch
    from lightglue.utils import load_image, rbd

    features = features or active_lightglue_features()
    if min_matches is None:
        min_matches = default_min_matches(features)
    extractor, matcher, device = _lightglue_models(features, device)

    img0 = load_image(str(path_a)).to(device)
    img1 = load_image(str(path_b)).to(device)
    with torch.inference_mode():
        feats0 = extractor.extract(img0)
        feats1 = extractor.extract(img1)
        matches01 = matcher({"image0": feats0, "image1": feats1})
        feats0, feats1, matches01 = [rbd(x) for x in (feats0, feats1, matches01)]
        idx = matches01["matches"]
        n = int(idx.shape[0])
    verified = n >= min_matches
    inliers = None
    detail = f"device={device}; min_matches={min_matches}"
    if min_inlier_ratio > 0 and verified:
        size1 = feats1["image_size"].detach().cpu().numpy()
        inliers = homography_inliers(
            feats0["keypoints"][idx[:, 0]].detach().cpu().numpy(),
            feats1["keypoints"][idx[:, 1]].detach().cpu().numpy(),
            dst_size=(float(size1[0]), float(size1[1])),
        )
        if inliers is not None:
            verified = inliers >= min_matches and inliers >= min_inlier_ratio * n
        detail = f"{detail}; inliers={inliers}"
    return PreciseMatchResult(
        verified=verified,
        num_matches=n,
        score=float(n),
        method=f"lightglue+{features}",
        detail=detail,
        inliers=inliers,
    )


class LightGlueFeatureCache:
    """Extract LightGlue features once per image, then match many pairs.

    Same extractor / matcher / thresholds as ``verify_image_pair``, but cross-set
    scans (N queries × M corpus images) pay N+M extractions instead of 2·N·M.
    """

    def __init__(
        self,
        *,
        features: str | None = None,
        min_matches: int | None = None,
        min_inliers: int | None = None,
        min_inlier_ratio: float = 0.0,
    ):
        """``min_inliers`` set: verify on homography inliers (and inlier ratio);
        raw matches below ``min_matches`` are rejected without RANSAC. Without
        OpenCV the calibrated raw-match gate applies instead."""
        self.features = features or active_lightglue_features()
        self.min_matches = (
            default_min_matches(self.features) if min_matches is None else min_matches
        )
        self.min_inliers = min_inliers
        self.min_inlier_ratio = min_inlier_ratio
        self._feats: dict[str, dict] = {}

    def _get(self, path: Path | str) -> dict:
        import torch
        from lightglue.utils import load_image

        key = str(path)
        feats = self._feats.get(key)
        if feats is None:
            extractor, _matcher, device = _lightglue_models(self.features)
            img = load_image(key).to(device)
            with torch.inference_mode():
                feats = extractor.extract(img)
            self._feats[key] = feats
        return feats

    def match(self, path_a: Path | str, path_b: Path | str) -> PreciseMatchResult:
        return with_inversion(
            self._match_once,
            Path(path_a),
            Path(path_b),
            verify_inverted=lambda a, b: self._match_once(a, b, inverted=True),
        )

    def _match_once(
        self, path_a: Path, path_b: Path, *, inverted: bool = False
    ) -> PreciseMatchResult:
        import torch

        _extractor, matcher, device = _lightglue_models(self.features)
        f0, f1 = self._get(path_a), self._get(path_b)
        with torch.inference_mode():
            out = matcher({"image0": f0, "image1": f1})
        idx = out["matches"][0]
        n = int(idx.shape[0])
        inliers = None
        if n >= self.min_matches:
            k0 = f0["keypoints"][0][idx[:, 0]].detach().cpu().numpy()
            k1 = f1["keypoints"][0][idx[:, 1]].detach().cpu().numpy()
            size1 = f1["image_size"][0].detach().cpu().numpy()
            inliers = homography_inliers(k0, k1, dst_size=(float(size1[0]), float(size1[1])))
        min_inliers = self.min_inliers
        min_ratio = self.min_inlier_ratio
        if inverted:
            min_inliers = self.min_matches if min_inliers is None else min_inliers
            min_ratio = max(min_ratio, INVERTED_MIN_INLIER_RATIO)
        if min_inliers is None or n < self.min_matches:
            verified = n >= self.min_matches
        elif inliers is None:
            verified = n >= default_min_matches(self.features)
        else:
            verified = inliers >= min_inliers and inliers >= min_ratio * n
        return PreciseMatchResult(
            verified=verified,
            num_matches=n,
            score=float(n),
            method=f"lightglue+{self.features}",
            detail=f"device={device}; min_matches={self.min_matches}; inliers={inliers}",
            inliers=inliers,
        )


def homography_inliers(
    pts0: np.ndarray,
    pts1: np.ndarray,
    *,
    dst_size: tuple[float, float],
    reproj_frac: float = 0.01,
) -> int | None:
    """RANSAC homography inlier count for matched keypoints (None without OpenCV).

    The same photo reused at another scale / crop maps by a single homography;
    look-alike texture (fluorescence speckle, glyphs) matches scatter instead.
    """
    if len(pts0) < 4:
        return 0
    try:
        import cv2
    except ImportError:
        return None
    thr = max(2.0, reproj_frac * max(dst_size))
    _h, mask = cv2.findHomography(
        pts0.astype(np.float32).reshape(-1, 1, 2),
        pts1.astype(np.float32).reshape(-1, 1, 2),
        cv2.RANSAC,
        thr,
    )
    return int(mask.sum()) if mask is not None else 0


def verify_image_pair(
    path_a: Path | str,
    path_b: Path | str,
    *,
    prefer_lightglue: bool = True,
    require_lightglue: bool = False,
    min_matches: int | None = None,
    features: str | None = None,
    strict_geometry: bool = False,
) -> PreciseMatchResult:
    """Re-verify a candidate duplicate pair with the best available matcher.

    ``require_lightglue=True``: do not silently fall back to ORB/NCC when LightGlue
    is unavailable or fails (returns verified=False with method marker).
    ``min_matches=None`` / ``features=None``: calibrated default for the usage profile.
    Also compares against the intensity-inverted ``path_b`` (``inverted=True`` when that wins).
    ``strict_geometry=True``: the homography gate of the inverted pass also applies to the
    normal pass (for pairs shortlisted only through their inverted similarity).
    """
    normal_ratio = INVERTED_MIN_INLIER_RATIO if strict_geometry else 0.0

    def _once(a: Path, b: Path, inlier_ratio: float = normal_ratio) -> PreciseMatchResult:
        return _verify_pair_once(
            a,
            b,
            prefer_lightglue=prefer_lightglue,
            require_lightglue=require_lightglue,
            min_matches=min_matches,
            features=features,
            min_inlier_ratio=inlier_ratio,
        )

    return with_inversion(
        _once,
        Path(path_a),
        Path(path_b),
        verify_inverted=lambda a, b: _once(a, b, INVERTED_MIN_INLIER_RATIO),
    )


def _verify_pair_once(
    a: Path,
    b: Path,
    *,
    prefer_lightglue: bool,
    require_lightglue: bool,
    min_matches: int | None,
    features: str | None,
    min_inlier_ratio: float = 0.0,
) -> PreciseMatchResult:
    fallback_note = ""
    if prefer_lightglue and lightglue_available():
        try:
            return _verify_lightglue(
                a,
                b,
                features=features,
                min_matches=min_matches,
                min_inlier_ratio=min_inlier_ratio,
            )
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
    min_matches: int | None = None,
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
            strict_geometry=bool(getattr(m, "coarse_inverted", False)),
        )
        m.precise_matches = precise.num_matches
        m.precise_score = precise.score
        m.precise_method = precise.method
        m.precise_verified = precise.verified
        m.precise_inverted = precise.inverted
        m.likely_duplicate = bool(precise.verified)
        m.method = f"{m.method}+{precise.method}"
    return candidates
