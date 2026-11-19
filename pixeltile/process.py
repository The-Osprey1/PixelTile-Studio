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
    if dither:
        return _floyd_steinberg(rgb_to_lab(rgb), pal_lab)
    rgb = np.asarray(rgb)
    if rgb.dtype != np.uint8:
        return nearest(rgb_to_lab(rgb), pal_lab)
    # Match each distinct color once: artwork repeats colors heavily, photos still repeat some.
    flat = rgb.reshape(-1, 3).astype(np.int32)
    uniq, inv = np.unique((flat[:, 0] << 16) | (flat[:, 1] << 8) | flat[:, 2], return_inverse=True)
    urgb = np.stack([(uniq >> 16) & 255, (uniq >> 8) & 255, uniq & 255], axis=-1)
    return nearest(rgb_to_lab(urgb), pal_lab)[inv.ravel()].reshape(rgb.shape[:-1])


def _floyd_steinberg(lab: np.ndarray, pal_lab: np.ndarray) -> np.ndarray:
    """Floyd-Steinberg error diffusion, processed along anti-diagonal wavefronts.

    Pixel (y, x) only receives error from (y, x-1) and (y-1, x-1..x+1), all of which lie on
    earlier wavefronts x + 2y = t. Every pixel on one wavefront can therefore be handled in a
    single vectorized step. Error is added in the same order as a row-by-row scan, so the
    result is identical to the classic sequential algorithm, just much faster.
    """
    h, w, _ = lab.shape
    work = np.array(lab, dtype=np.float64, copy=True)
    out = np.zeros((h, w), dtype=np.int32)
    for t in range(w + 2 * (h - 1)):
        y0 = max(0, -((w - 1 - t) // 2))  # ceil((t - w + 1) / 2), clamped at 0
        y1 = min(h - 1, t // 2)
        if y0 > y1:
            continue
        ys = np.arange(y0, y1 + 1)
        xs = t - 2 * ys
        old = work[ys, xs]
        k = ((old[:, None, :] - pal_lab[None, :, :]) ** 2).sum(-1).argmin(1)
        out[ys, xs] = k
        err = old - pal_lab[k]
        down = ys + 1 < h
        # down-left must land before right: in a row scan, (y-1, x+1) is visited before (y, x-1)
        m = down & (xs > 0)
        work[ys[m] + 1, xs[m] - 1] += err[m] * 3 / 16
        m = xs + 1 < w
        work[ys[m], xs[m] + 1] += err[m] * 7 / 16
        work[ys[down] + 1, xs[down]] += err[down] * 5 / 16
        m = down & (xs + 1 < w)
        work[ys[m] + 1, xs[m] + 1] += err[m] * 1 / 16
    return out


__all__ = ["load_image", "grid_size", "adjust", "resize", "quantize", "palette_labs"]
