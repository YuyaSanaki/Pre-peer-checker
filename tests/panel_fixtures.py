"""Synthetic photo / composite-page builders shared by the panel-matching tests."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw


def photo(seed: int, size: tuple[int, int] = (360, 360)) -> Image.Image:
    """Stand-in micrograph: 1/f colour wash plus punctate detail.

    The puncta matter: a pure 1/f gradient has no true corners, so JPEG smoothing
    drops every ALIKED keypoint below threshold and even identical crops stop
    matching. Real photos carry this kind of high-frequency structure.
    """
    rng = np.random.default_rng(seed)
    w, h = size
    base = np.zeros((h, w, 3), dtype=np.float32)
    for cells in (3, 6, 12, 24, 48):
        noise = (rng.random((cells, cells, 3)) * 255).astype(np.uint8)
        layer = Image.fromarray(noise).resize((w, h), Image.Resampling.BICUBIC)
        base += np.asarray(layer, dtype=np.float32) / cells**0.5
    base = (base - base.min()) / (base.max() - base.min()) * 255
    img = Image.fromarray(base.astype(np.uint8))
    draw = ImageDraw.Draw(img)
    for _ in range(int(0.0025 * w * h)):
        cx, cy = rng.integers(0, w), rng.integers(0, h)
        r = int(rng.integers(2, 5))
        tone = 245 if rng.random() < 0.5 else 15
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(tone, tone, tone))
    return img


def page(photos: list[Image.Image], *, gap: int = 40, size=(1400, 1000)) -> Image.Image:
    p = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(p)
    draw.text((60, 20), "Figure S1. Composite with plots and text", fill="black")
    x = 60
    for ph in photos:
        p.paste(ph, (x, 120))
        x += ph.size[0] + gap
    # bar-chart-like region: mostly white, must not become a panel
    for i in range(5):
        draw.rectangle((80 + i * 60, 900 - 40 * (i + 1), 110 + i * 60, 900), outline="black")
    return p
