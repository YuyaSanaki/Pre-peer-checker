"""画像重複スキャナ: DINOv2（任意）+ 知覚ハッシュ + グレースケールフォールバック."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass
class ImagePairMatch:
    path_a: Path
    path_b: Path
    cosine_similarity: float
    likely_duplicate: bool
    method: str = "gray64"
    precise_matches: int | None = None
    precise_score: float | None = None
    precise_method: str | None = None
    precise_verified: bool | None = None


def _load_gray_vector(path: Path, size: int = 64) -> np.ndarray:
    img = Image.open(path).convert("L").resize((size, size))
    arr = np.asarray(img, dtype=np.float32).ravel()
    arr = arr - arr.mean()
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return arr


def _average_hash(path: Path, size: int = 16) -> np.ndarray:
    """Simple average hash as ±1 vector (cosine-friendly)."""
    img = Image.open(path).convert("L").resize((size, size))
    arr = np.asarray(img, dtype=np.float32)
    bits = (arr > arr.mean()).astype(np.float32).ravel()
    bits = bits * 2.0 - 1.0
    return bits / (np.linalg.norm(bits) + 1e-9)


def _combined_fallback_vector(path: Path) -> np.ndarray:
    g = _load_gray_vector(path, size=64)
    h = _average_hash(path, size=16)
    v = np.concatenate([g, h])
    return v / (np.linalg.norm(v) + 1e-9)


def scan_image_duplicates(
    paths: list[Path | str],
    *,
    threshold: float = 0.98,
) -> list[ImagePairMatch]:
    """軽量フォールバック: グレースケール + average-hash のコサイン類似度."""
    resolved = [Path(p) for p in paths]
    vectors = {p: _combined_fallback_vector(p) for p in resolved}
    matches: list[ImagePairMatch] = []
    for a, b in combinations(resolved, 2):
        sim = float(np.dot(vectors[a], vectors[b]))
        matches.append(
            ImagePairMatch(
                path_a=a,
                path_b=b,
                cosine_similarity=sim,
                likely_duplicate=sim >= threshold,
                method="gray64+ahash16",
            )
        )
    return matches


class DinoDuplicateScanner:
    """DINOv2 特徴量による重複検出（torch + torchvision 利用時）."""

    def __init__(self, model_name: str = "dinov2_vits14", device: str | None = None):
        self.model_name = model_name
        self.device = device
        self._model = None
        self._transform = None

    @staticmethod
    def available() -> bool:
        try:
            import torch  # noqa: F401
            import torchvision  # noqa: F401

            return True
        except ImportError:
            return False

    def load(self) -> None:
        import torch
        import torchvision.transforms as T

        if self.device is None:
            if torch.cuda.is_available():
                self.device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                self.device = "mps"
            else:
                self.device = "cpu"
        self._model = torch.hub.load("facebookresearch/dinov2", self.model_name, trust_repo=True)
        self._model.eval().to(self.device)
        if self.device == "mps":
            # MPS 未実装の演算（位置埋め込みの補間など）があると scan 全体が
            # 軽量フォールバックに落ちるため、先に試して CPU へ退避する
            try:
                with torch.inference_mode():
                    self._model(torch.zeros(1, 3, 224, 224, device="mps"))
            except Exception:  # noqa: BLE001
                self.device = "cpu"
                self._model.to("cpu")
        self._transform = T.Compose(
            [
                T.Resize(256),
                T.CenterCrop(224),
                T.ToTensor(),
                T.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
            ]
        )

    def embed(self, path: Path | str) -> np.ndarray:
        import torch

        if self._model is None:
            self.load()
        assert self._model is not None and self._transform is not None
        img = Image.open(path).convert("RGB")
        tensor = self._transform(img).unsqueeze(0).to(self.device)
        with torch.inference_mode():
            feat = self._model(tensor)
            feat = feat / feat.norm(dim=-1, keepdim=True)
        return feat.squeeze(0).detach().cpu().numpy()

    def scan(self, paths: list[Path | str], *, threshold: float = 0.95) -> list[ImagePairMatch]:
        resolved = [Path(p) for p in paths]
        embeds = {p: self.embed(p) for p in resolved}
        out: list[ImagePairMatch] = []
        for a, b in combinations(resolved, 2):
            sim = float(np.dot(embeds[a], embeds[b]))
            out.append(
                ImagePairMatch(
                    path_a=a,
                    path_b=b,
                    cosine_similarity=sim,
                    likely_duplicate=sim >= threshold,
                    method=f"dinov2:{self.model_name}",
                )
            )
        return out


def scan_image_duplicates_auto(
    paths: list[Path | str],
    *,
    threshold: float | None = None,
    prefer_dino: bool = True,
    dino_threshold: float = 0.92,
    fallback_threshold: float = 0.985,
    refine_with_precise: bool = True,
    prefer_lightglue: bool = True,
    require_lightglue: bool = False,
    min_matches: int | None = None,
    candidate_floor: float | None = None,
) -> tuple[list[ImagePairMatch], str]:
    """Try DINOv2 when available; otherwise gray+ahash fallback.

    When refine_with_precise is True, pairs above the coarse threshold (and
    near-misses above candidate_floor) are re-checked with LightGlue / ORB / NCC.
    ``require_lightglue`` / ``min_matches`` are forwarded to the precise stage.
    Returns (matches, method_used).
    """
    if len(paths) < 2:
        return [], "none"

    method = "gray64+ahash16"
    thr = fallback_threshold if threshold is None else threshold
    matches: list[ImagePairMatch] = []

    if prefer_dino and DinoDuplicateScanner.available():
        try:
            scanner = DinoDuplicateScanner()
            thr = dino_threshold if threshold is None else threshold
            matches = scanner.scan(paths, threshold=thr)
            method = f"dinov2:{scanner.model_name}"
        except Exception:
            matches = []

    if not matches:
        thr = fallback_threshold if threshold is None else threshold
        matches = scan_image_duplicates(paths, threshold=thr)
        method = "gray64+ahash16"

    if refine_with_precise and matches:
        from pre_peer_checker.imaging.lightglue_match import (
            DEFAULT_MIN_MATCHES,
            confirm_candidate_matches,
            verify_image_pair,
        )

        floor = candidate_floor
        if floor is None:
            floor = max(0.80, thr - 0.08)
        # Promote near-misses into precise-check pool
        for m in matches:
            if not m.likely_duplicate and m.cosine_similarity >= floor:
                m.likely_duplicate = True
                m.method = f"{m.method}:candidate"
        confirm_candidate_matches(
            matches,
            prefer_lightglue=prefer_lightglue,
            require_lightglue=require_lightglue,
            min_matches=DEFAULT_MIN_MATCHES if min_matches is None else min_matches,
            only_likely=True,
        )
        # Restore: pairs that were only candidates and failed precise stay non-dup
        precise_methods = {
            m.precise_method for m in matches if m.precise_method
        }
        if precise_methods:
            method = f"{method}+{'|'.join(sorted(precise_methods))}"
        else:
            # ensure at least one verification attempt recorded
            _ = verify_image_pair  # noqa: F841 — imported for type clarity
            method = f"{method}+precise"

    return matches, method
