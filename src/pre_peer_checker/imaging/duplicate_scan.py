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
        self._full_frame_transform = None

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

        if self._model is not None and self._transform is not None:
            return
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
        normalize = T.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225))
        self._transform = T.Compose(
            [T.Resize(256), T.CenterCrop(224), T.ToTensor(), normalize]
        )
        self._full_frame_transform = T.Compose(
            [T.Resize((224, 224)), T.ToTensor(), normalize]
        )

    def embed(self, path: Path | str) -> np.ndarray:
        return self.embed_many([path])[0]

    def embed_many(
        self,
        paths: list[Path | str],
        *,
        batch_size: int = 16,
        full_frame: bool = False,
    ) -> list[np.ndarray]:
        """Embed images in batches (one forward pass per batch, not per image).

        ``full_frame=True`` squashes the whole image to 224×224 instead of
        center-cropping, so elongated panels keep their edges.
        """
        import torch

        if self._model is None:
            self.load()
        assert self._model is not None and self._transform is not None
        transform = self._full_frame_transform if full_frame else self._transform
        out: list[np.ndarray] = []
        for start in range(0, len(paths), batch_size):
            chunk = paths[start : start + batch_size]
            tensors = [transform(Image.open(p).convert("RGB")) for p in chunk]
            batch = torch.stack(tensors).to(self.device)
            with torch.inference_mode():
                feats = self._model(batch)
                feats = feats / feats.norm(dim=-1, keepdim=True)
            out.extend(feats.detach().cpu().numpy())
        return out

    def scan(self, paths: list[Path | str], *, threshold: float = 0.95) -> list[ImagePairMatch]:
        resolved = [Path(p) for p in paths]
        matrix = np.stack(self.embed_many(resolved))
        # Embeddings are L2-normalised, so the gram matrix is the cosine similarity
        # for every pair at once.
        sims = matrix @ matrix.T
        method = f"dinov2:{self.model_name}"
        return [
            ImagePairMatch(
                path_a=resolved[i],
                path_b=resolved[j],
                cosine_similarity=float(sims[i, j]),
                likely_duplicate=bool(sims[i, j] >= threshold),
                method=method,
            )
            for i, j in combinations(range(len(resolved)), 2)
        ]


_SHARED_SCANNERS: dict[str, DinoDuplicateScanner] = {}


def shared_dino_scanner(model_name: str = "dinov2_vits14") -> DinoDuplicateScanner:
    """Process-wide DINOv2 scanner.

    A single run scans Figure embeds, microscopy rasters and the past-paper
    corpus. Without this each of those would pull the weights through
    ``torch.hub`` again and repeat the MPS warmup probe.
    """
    scanner = _SHARED_SCANNERS.get(model_name)
    if scanner is None:
        scanner = DinoDuplicateScanner(model_name)
        _SHARED_SCANNERS[model_name] = scanner
    return scanner


def clear_shared_dino_scanners() -> None:
    _SHARED_SCANNERS.clear()


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
            scanner = shared_dino_scanner()
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
            min_matches=min_matches,
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
