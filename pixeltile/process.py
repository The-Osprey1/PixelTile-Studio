"""Image loading, adjustment, resizing and palette quantization."""
from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

from .palette import nearest, palette_labs, rgb_to_lab


def load_image(src) -> Image.Image:
    img = Image.open(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    return img.convert("RGB")


def grid_size(img_w: int, img_h: int, width_mm: float, pixel_mm: float,
              max_tile_mm: float) -> tuple[int, int, int, int]:
    """Return (cols, rows, tiles_x, tiles_y); every tile has identical pixel dimensions."""
    cols0 = max(1, round(width_mm / pixel_mm))
    rows0 = max(1, round(cols0 * img_h / img_w))

    def split(n: int) -> tuple[int, int]:
        t = max(1, math.ceil(n * pixel_mm / max_tile_mm))
        while math.ceil(n / t) * pixel_mm > max_tile_mm + 1e-9:
            t += 1
        return t, math.ceil(n / t) * t

    tx, cols = split(cols0)
    ty, rows = split(rows0)
    return cols, rows, tx, ty


def adjust(img: Image.Image, contrast: float = 1.0, saturation: float = 1.0) -> Image.Image:
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if saturation != 1.0:
        img = ImageEnhance.Color(img).enhance(saturation)
    return img


def resize(img: Image.Image, cols: int, rows: int) -> np.ndarray:
    return np.asarray(img.resize((cols, rows), Image.Resampling.BOX if img.width >= cols
                                 else Image.Resampling.LANCZOS), dtype=np.uint8)


def quantize(rgb: np.ndarray, pal_rgb: np.ndarray, dither: bool = False) -> np.ndarray:
    """Map pixels to nearest palette index (CIELAB). Optional Floyd-Steinberg dithering."""
    pal_lab = rgb_to_lab(pal_rgb)
    lab = rgb_to_lab(rgb)
    if not dither:
        return nearest(lab, pal_lab)
    h, w, _ = lab.shape
    work = lab.copy()
    out = np.zeros((h, w), dtype=np.int32)
    for y in range(h):
        for x in range(w):
            old = work[y, x]
            k = int(((pal_lab - old) ** 2).sum(1).argmin())
            out[y, x] = k
            err = old - pal_lab[k]
            if x + 1 < w:
                work[y, x + 1] += err * 7 / 16
            if y + 1 < h:
                if x > 0:
                    work[y + 1, x - 1] += err * 3 / 16
                work[y + 1, x] += err * 5 / 16
                if x + 1 < w:
                    work[y + 1, x + 1] += err * 1 / 16
    return out


__all__ = ["load_image", "grid_size", "adjust", "resize", "quantize", "palette_labs"]
