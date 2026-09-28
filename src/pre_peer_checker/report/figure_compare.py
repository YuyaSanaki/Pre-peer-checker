"""画像重複・再利用 Warning の左右比較プレビュー（HTML レポート埋め込み用）.

原稿側の画像は PDF から一時フォルダに書き出したものが多く、レポートを開く頃には
消えている。一時フォルダを消す前に両側の画像を data URI に焼き込み、一致した
パネル領域を割合座標で持たせておく。

同じ画像が数百件の Warning に現れるため、data URI は画像ファイルごとに 1 回だけ作り
（同一の str オブジェクトを共有）、HTML では CSS クラスとして 1 度だけ埋め込むので、
Warning 件数で HTML は膨らまない。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from pre_peer_checker.warnings import WarningItem

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".lif", ".czi"}

FULL_MAX_SIDE = 1400
MAX_BOX_PAIRS = 6

# path -> (data URI, original size, encoded size), or None if unreadable
_ImageCache = dict[str, tuple[str, tuple[int, int], tuple[int, int]] | None]


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


def _jpeg_data_uri(img: Image.Image, max_side: int, quality: int = 82) -> tuple[str, tuple[int, int]]:
    w, h = img.size
    scale = min(1.0, max_side / max(w, h, 1))
    if scale < 1.0:
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS
        )
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    return uri, img.size


def _encoded_image(path: Path, cache: _ImageCache | None):
    key = str(path)
    if cache is not None and key in cache:
        return cache[key]
    img = _load_rgb(path)
    entry = None
    if img is not None:
        uri, shown = _jpeg_data_uri(img, FULL_MAX_SIDE)
        entry = (uri, img.size, shown)
    if cache is not None:
        cache[key] = entry
    return entry


def _box_fraction(box: list[int] | None, size: tuple[int, int]) -> tuple[float, float, float, float] | None:
    w, h = size
    if not box or len(box) != 4 or w <= 0 or h <= 0:
        return None
    left, top, right, bottom = (int(v) for v in box)
    left, right = max(0, min(left, w)), max(0, min(right, w))
    top, bottom = max(0, min(top, h)), max(0, min(bottom, h))
    if right <= left or bottom <= top:
        return None
    return left / w, top / h, (right - left) / w, (bottom - top) / h


def _box_percent(box: list[int] | None, size: tuple[int, int]) -> dict[str, float] | None:
    frac = _box_fraction(box, size)
    if frac is None:
        return None
    left, top, width, height = frac
    return {
        "left_pct": round(100.0 * left, 3),
        "top_pct": round(100.0 * top, 3),
        "width_pct": round(100.0 * width, 3),
        "height_pct": round(100.0 * height, 3),
    }


def _box_text(box: list[int] | None, label: str | None = None) -> str:
    if not box:
        return "画像全体"
    if label:
        return label
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


def build_figure_compare(
    warning: WarningItem, *, image_cache: _ImageCache | None = None
) -> dict | None:
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

    enc_a = _encoded_image(paths[0], image_cache)
    enc_b = enc_a if same_file else _encoded_image(paths[1], image_cache)
    if enc_a is None or enc_b is None:
        return None
    uri_a, size_a, shown_a = enc_a
    uri_b, size_b, shown_b = enc_b

    meta = warning.metadata or {}
    role_a, role_b = _roles(meta, same_file)

    boxes_a: list[dict] = []
    boxes_b: list[dict] = []
    pairs_out: list[dict] = []
    for k, pm in enumerate(_panel_pairs(meta), start=1):
        box_a, box_b = pm.get("panel_a"), pm.get("panel_b")
        pct_a = _box_percent(box_a, size_a)
        pct_b = _box_percent(box_b, size_b)
        if pct_a:
            boxes_a.append({"pair": k, "key": f"{k}A", "primary": k == 1, **pct_a})
        if pct_b:
            boxes_b.append({"pair": k, "key": f"{k}B", "primary": k == 1, **pct_b})
        pairs_out.append(
            {
                "pair": k,
                "primary": k == 1,
                "matches": pm.get("matches"),
                "inliers": pm.get("inliers"),
                "key_a": f"{k}A",
                "key_b": f"{k}B",
                "box_a": _box_text(box_a, pm.get("label_a")),
                "box_b": _box_text(box_b, pm.get("label_b")),
            }
        )

    return {
        "same_file": same_file,
        "sides": [
            {
                "role": role_a,
                "name": paths[0].name,
                "width": size_a[0],
                "height": size_a[1],
                "shown_width": shown_a[0],
                "image_data_uri": uri_a,
                "boxes": boxes_a,
            },
            {
                "role": role_b,
                "name": paths[1].name,
                "width": size_b[0],
                "height": size_b[1],
                "shown_width": shown_b[0],
                "image_data_uri": uri_b,
                "boxes": boxes_b,
            },
        ],
        "pairs": pairs_out,
    }


def attach_figure_compares(warnings: list[WarningItem]) -> int:
    """Embed compare previews on warnings while their source images still exist."""
    cache: _ImageCache = {}
    attached = 0
    for w in warnings:
        if w.figure_compare:
            attached += 1
            continue
        try:
            cmp = build_figure_compare(w, image_cache=cache)
        except Exception:  # noqa: BLE001
            cmp = None
        if cmp:
            w.figure_compare = cmp
            attached += 1
    return attached
