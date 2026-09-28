"""画像重複・再利用 Warning の左右比較プレビュー（HTML レポート埋め込み用）.

原稿側の画像は PDF から一時フォルダに書き出したものが多く、レポートを開く頃には
消えている。一時フォルダを消す前に両側の画像を data URI に焼き込み、一致した
パネル領域を割合座標で持たせておく。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from pre_peer_checker.warnings import WarningItem

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".lif", ".czi"}

MAX_COMPARE_WARNINGS = 40
FULL_MAX_SIDE = 1000
CROP_MAX_SIDE = 480
MAX_BOX_PAIRS = 6
MAX_CROP_PAIRS = 3


def _load_rgb(path: Path) -> Image.Image | None:
    # Same loader as panel_units, so panel boxes share its pixel coordinates
    from pre_peer_checker.imaging.microscopy import frame_to_uint8_rgb, try_load_frames

    frames, err = try_load_frames(path, max_series=1)
    if err or not frames:
        return None
    try:
        return Image.fromarray(frame_to_uint8_rgb(frames[0].data)).convert("RGB")
    except Exception:  # noqa: BLE001
        return None


def _jpeg_data_uri(img: Image.Image, max_side: int, quality: int = 82) -> str:
    w, h = img.size
    scale = min(1.0, max_side / max(w, h, 1))
    if scale < 1.0:
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS
        )
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _box_percent(box: list[int], size: tuple[int, int]) -> dict[str, float] | None:
    w, h = size
    if not box or len(box) != 4 or w <= 0 or h <= 0:
        return None
    left, top, right, bottom = (int(v) for v in box)
    left, right = max(0, min(left, w)), max(0, min(right, w))
    top, bottom = max(0, min(top, h)), max(0, min(bottom, h))
    if right <= left or bottom <= top:
        return None
    return {
        "left_pct": round(100.0 * left / w, 3),
        "top_pct": round(100.0 * top / h, 3),
        "width_pct": round(100.0 * (right - left) / w, 3),
        "height_pct": round(100.0 * (bottom - top) / h, 3),
    }


def _crop(img: Image.Image, box: list[int] | None) -> Image.Image:
    if not box:
        return img
    left, top, right, bottom = (int(v) for v in box)
    return img.crop((left, top, right, bottom))


def _box_text(box: list[int] | None) -> str:
    if not box:
        return "画像全体"
    left, top, right, bottom = box
    return f"[{left},{top}–{right},{bottom}]"


def _roles(meta: dict, same_file: bool) -> tuple[str, str]:
    if meta.get("corpus_match"):
        return "入力（原稿）", "参照（過去論文コーパス）"
    if same_file:
        return "原稿 A（同一図内）", "原稿 B（同一図内）"
    if meta.get("internal_reuse"):
        return "原稿 A", "原稿 B"
    return "画像 A", "画像 B"


def _panel_pairs(meta: dict) -> list[dict]:
    pairs = [p for p in (meta.get("panel_matches") or []) if isinstance(p, dict)]
    if not pairs and (meta.get("panel_a") is not None or meta.get("panel_b") is not None):
        pairs = [
            {
                "panel_a": meta.get("panel_a"),
                "panel_b": meta.get("panel_b"),
                "matches": meta.get("precise_matches"),
                "inliers": meta.get("precise_inliers"),
            }
        ]
    return pairs[:MAX_BOX_PAIRS]


def _is_image_path(s: str) -> bool:
    try:
        return Path(s).suffix.lower() in _IMAGE_EXTS
    except Exception:  # noqa: BLE001
        return False


def build_figure_compare(warning: WarningItem) -> dict | None:
    """Two-image side-by-side view with matched panel boxes, or None if not applicable."""
    sources = [s for s in (warning.sources or []) if isinstance(s, str) and s]
    if not sources or len(sources) > 2 or not all(_is_image_path(s) for s in sources):
        return None
    same_file = len(sources) == 1
    if same_file and not (warning.metadata or {}).get("panel"):
        return None
    paths = [Path(sources[0]), Path(sources[-1])]
    if not all(p.is_file() for p in paths):
        return None

    img_a = _load_rgb(paths[0])
    img_b = img_a if same_file else _load_rgb(paths[1])
    if img_a is None or img_b is None:
        return None

    meta = warning.metadata or {}
    role_a, role_b = _roles(meta, same_file)
    uri_a = _jpeg_data_uri(img_a, FULL_MAX_SIDE)
    uri_b = uri_a if same_file else _jpeg_data_uri(img_b, FULL_MAX_SIDE)

    boxes_a: list[dict] = []
    boxes_b: list[dict] = []
    pairs_out: list[dict] = []
    for k, pm in enumerate(_panel_pairs(meta), start=1):
        box_a, box_b = pm.get("panel_a"), pm.get("panel_b")
        pct_a = _box_percent(box_a, img_a.size) if box_a else None
        pct_b = _box_percent(box_b, img_b.size) if box_b else None
        if pct_a:
            boxes_a.append({"pair": k, "primary": k == 1, **pct_a})
        if pct_b:
            boxes_b.append({"pair": k, "primary": k == 1, **pct_b})
        entry = {
            "pair": k,
            "primary": k == 1,
            "matches": pm.get("matches"),
            "inliers": pm.get("inliers"),
            "box_a": _box_text(box_a),
            "box_b": _box_text(box_b),
        }
        if k <= MAX_CROP_PAIRS and (box_a or box_b):
            entry["crop_a"] = _jpeg_data_uri(_crop(img_a, box_a), CROP_MAX_SIDE)
            entry["crop_b"] = _jpeg_data_uri(_crop(img_b, box_b), CROP_MAX_SIDE)
        pairs_out.append(entry)

    return {
        "same_file": same_file,
        "sides": [
            {
                "role": role_a,
                "name": paths[0].name,
                "width": img_a.size[0],
                "height": img_a.size[1],
                "image_data_uri": uri_a,
                "boxes": boxes_a,
            },
            {
                "role": role_b,
                "name": paths[1].name,
                "width": img_b.size[0],
                "height": img_b.size[1],
                "image_data_uri": uri_b,
                "boxes": boxes_b,
            },
        ],
        "pairs": pairs_out,
    }


def attach_figure_compares(
    warnings: list[WarningItem], *, limit: int = MAX_COMPARE_WARNINGS
) -> int:
    """Embed compare previews on warnings while their source images still exist."""
    attached = sum(1 for w in warnings if w.figure_compare)
    for w in warnings:
        if attached >= limit:
            break
        if w.figure_compare:
            continue
        try:
            cmp = build_figure_compare(w)
        except Exception:  # noqa: BLE001
            cmp = None
        if cmp:
            w.figure_compare = cmp
            attached += 1
    return attached
