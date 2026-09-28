"""顕微鏡フォーマット展開とラスタ画像読込."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


@dataclass
class ImageFrame:
    source: Path
    label: str
    data: np.ndarray
    meta: dict[str, Any] = field(default_factory=dict)


def extract_czi_scenes(czi_path: str | Path) -> list[ImageFrame]:
    """Zeiss CZI からマルチシーンを展開（pylibCZIrw 必須）."""
    try:
        from pylibCZIrw import czi as czirw
    except ImportError as e:
        raise ImportError(
            "pylibCZIrw が必要です: pip install 'pre-peer-checker[imaging]'"
        ) from e

    czi_path = Path(czi_path)
    images: list[ImageFrame] = []
    with czirw.open_czi(str(czi_path)) as czidoc:
        n_scenes = 1
        try:
            rects = czidoc.scenes_bounding_rectangle
            if rects is not None:
                n_scenes = len(rects)
        except Exception:
            n_scenes = 1
        for scene_idx in range(n_scenes):
            try:
                frame = czidoc.read(plane={"C": 0, "Z": 0, "T": 0}, scene=scene_idx)
            except TypeError:
                frame = czidoc.read(plane={"C": 0, "Z": 0, "T": 0})
            images.append(
                ImageFrame(
                    source=czi_path,
                    label=f"scene_{scene_idx}",
                    data=np.asarray(frame),
                    meta={"scene": scene_idx, "format": "czi"},
                )
            )
            if n_scenes == 1:
                break
    # Attach acquisition meta (XML) to all scenes once
    try:
        from pre_peer_checker.imaging.czi_meta import parse_czi_acquisition_meta

        acq = parse_czi_acquisition_meta(czi_path)
        if acq is not None:
            extra = acq.as_frame_meta()
            for fr in images:
                fr.meta.update(extra)
    except Exception:
        pass
    return images


def extract_lif_series(lif_path: str | Path, *, max_series: int = 8) -> list[ImageFrame]:
    """Leica LIF から各シリーズを展開（readlif 必須）."""
    try:
        from readlif.reader import LifFile
    except ImportError as e:
        raise ImportError(
            "readlif が必要です: pip install 'pre-peer-checker[imaging]'"
        ) from e

    lif_path = Path(lif_path)
    new_lif = LifFile(str(lif_path))
    series_images: list[ImageFrame] = []
    for idx, img in enumerate(new_lif.get_iter_image()):
        if idx >= max_series:
            break
        frame = np.array(img.get_frame(z=0, t=0, c=0))
        meta: dict[str, Any] = {"series_index": idx, "format": "lif"}
        for attr, key in (
            ("scale", "scale"),
            ("scale_n", "scale_n"),
            ("bit_depth", "bit_depth"),
            ("name", "series_name"),
        ):
            if hasattr(img, attr):
                try:
                    meta[key] = getattr(img, attr)
                except Exception:
                    pass
        for attr in ("settings", "info", "metadata"):
            blob = getattr(img, attr, None)
            if isinstance(blob, dict):
                meta.update({k: v for k, v in blob.items() if isinstance(k, str)})
        # Prefer header XML (Magnification / ObjectiveName / voxel size)
        if idx == 0:
            try:
                from pre_peer_checker.imaging.lif_meta import parse_lif_acquisition_meta

                acq = parse_lif_acquisition_meta(lif_path)
                if acq is not None:
                    meta.update(acq.as_frame_meta())
            except Exception:
                pass
        series_images.append(
            ImageFrame(
                source=lif_path,
                label=getattr(img, "name", f"series_{idx}"),
                data=frame,
                meta=meta,
            )
        )
    return series_images


def load_raster_image(path: Path | str) -> list[ImageFrame]:
    """TIFF / PNG / JPEG 等を 1 フレーム（または TIFF 先頭ページ）として読む."""
    path = Path(path)
    img = Image.open(path)
    # Multi-page TIFF: first page only for Phase 1 duplicate scan
    arr = np.asarray(img.convert("RGB") if img.mode not in {"L", "RGB"} else img)
    if arr.ndim == 2:
        pass
    elif arr.ndim == 3 and arr.shape[2] >= 3:
        arr = arr[:, :, :3]
    return [
        ImageFrame(
            source=path,
            label=path.stem,
            data=arr,
            meta={"format": path.suffix.lower().lstrip("."), "mode": img.mode},
        )
    ]


def load_microscope_image(path: Path | str, *, max_series: int = 8) -> list[ImageFrame]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".czi":
        return extract_czi_scenes(path)
    if suffix == ".lif":
        return extract_lif_series(path, max_series=max_series)
    if suffix in {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
        return load_raster_image(path)
    raise ValueError(f"Unsupported microscope/image format: {suffix}")


def try_load_frames(
    path: Path | str,
    *,
    max_series: int = 4,
) -> tuple[list[ImageFrame], str | None]:
    """Load frames; on missing optional deps return ([], reason)."""
    try:
        return load_microscope_image(path, max_series=max_series), None
    except ImportError as exc:
        return [], str(exc)
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"


def frame_to_uint8_rgb(frame: np.ndarray) -> np.ndarray:
    """Normalize arbitrary array to HxWx3 uint8 for preview / embedding."""
    arr = np.asarray(frame)
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    elif arr.ndim == 3 and arr.shape[-1] == 1:
        arr = np.repeat(arr, 3, axis=-1)
    elif arr.ndim == 3 and arr.shape[-1] > 3:
        arr = arr[:, :, :3]
    if arr.dtype != np.uint8:
        a_min = float(np.nanmin(arr)) if arr.size else 0.0
        a_max = float(np.nanmax(arr)) if arr.size else 1.0
        if a_max <= a_min:
            arr = np.zeros(arr.shape, dtype=np.uint8)
        else:
            scaled = (arr.astype(np.float32) - a_min) / (a_max - a_min)
            arr = (np.clip(scaled, 0, 1) * 255).astype(np.uint8)
    return arr


def export_frames_as_png(
    frames: list[ImageFrame],
    out_dir: Path | str,
    *,
    max_side: int = 512,
) -> list[Path]:
    """Write preview PNGs for duplicate scanning; return written paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for i, fr in enumerate(frames):
        rgb = frame_to_uint8_rgb(fr.data)
        img = Image.fromarray(rgb)
        w, h = img.size
        scale = min(1.0, max_side / max(w, h))
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.BILINEAR)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in fr.label)[:40]
        dest = out_dir / f"{fr.source.stem}_{safe}_{i}.png"
        img.save(dest)
        written.append(dest)
    return written
