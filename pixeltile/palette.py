"""Filament palette + CIELAB helpers."""
from __future__ import annotations

import os
import re

import numpy as np

# Approximate Bambu Lab PLA Basic / Matte colors (hex values are approximations
# for matching; the slicer color is only a display hint, spools decide reality).
BAMBU_PLA: list[tuple[str, str]] = [
    ("Jade White", "#FFFFFF"), ("Black", "#000000"), ("Gray", "#8E9089"),
    ("Silver", "#A6A9AA"), ("Light Gray", "#D1D3D5"), ("Dark Gray", "#545454"),
    ("Red", "#C12E1F"), ("Scarlet Red", "#DE4343"), ("Maroon Red", "#9D2235"),
    ("Pink", "#F55A74"), ("Hot Pink", "#F5547C"), ("Magenta", "#EC008C"),
    ("Orange", "#FF6A13"), ("Pumpkin Orange", "#FF9016"), ("Peach", "#FFA07A"),
    ("Yellow", "#F4EE2A"), ("Gold", "#E4BD68"), ("Sunflower Yellow", "#FEC600"),
    ("Beige", "#F7E6DE"), ("Bronze", "#847D48"), ("Brown", "#9D432C"),
    ("Cocoa Brown", "#6F5034"), ("Latte Brown", "#D3B7A7"), ("Dark Brown", "#7D6556"),
    ("Bright Green", "#4EAF53"), ("Bambu Green", "#00AE42"), ("Mistletoe Green", "#3F8E43"),
    ("Pine Green", "#0B6A3C"), ("Lime", "#C2E189"), ("Grass Green", "#61C680"),
    ("Dark Green", "#68724D"), ("Mint", "#A3D8C1"), ("Turquoise", "#00B1B7"),
    ("Cyan", "#0086D6"), ("Blue", "#0A2989"), ("Cobalt Blue", "#0056B8"),
    ("Sky Blue", "#73B2E6"), ("Navy Blue", "#042F56"), ("Ice Blue", "#A3D8E1"),
    ("Blue Gray", "#5B6579"), ("Purple", "#5E43B7"), ("Indigo Purple", "#482960"),
    ("Lavender", "#B8ACD6"), ("Lilac Purple", "#AE96D4"), ("Sakura Pink", "#E8AFCF"),
    ("Cream", "#F0E3C8"), ("Terracotta", "#B15533"), ("Ash Gray", "#9B9EA0"),
    ("Charcoal", "#1C1C1C"), ("Apple Green", "#C6E188"), ("Caramel", "#AE835B"),
]

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")
# Spool names may carry a corrected color: "Red=#B02A1C" (e.g. measured from a printed swatch).
_NAME_OK = re.compile(r"^[^\x00-\x1f<>\"&=,]{1,48}$")
_NEAREST_CHUNK = 32768  # pixels per block in nearest(); bounds temporary memory to ~40 MB


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    m = _HEX.match(h.strip())
    if not m:
        raise ValueError(f"Bad hex color: {h!r}")
    v = m.group(1)
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def rgb_to_hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(x) for x in rgb)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB (0..255, shape (...,3)) -> CIELAB D65."""
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], axis=-1)


def palette_labs(entries: list[tuple[str, str]]) -> np.ndarray:
    return rgb_to_lab(np.array([hex_to_rgb(h) for _, h in entries]))


def nearest(lab: np.ndarray, pal_lab: np.ndarray) -> np.ndarray:
    """Index of the closest palette color for each Lab pixel (shape (...,3)).

    Works in blocks so a large mural never builds a pixels x colors x 3 array all at once."""
    flat = lab.reshape(-1, 3)
    out = np.empty(len(flat), dtype=np.int64)
    for i in range(0, len(flat), _NEAREST_CHUNK):
        block = flat[i:i + _NEAREST_CHUNK]
        out[i:i + len(block)] = ((block[:, None, :] - pal_lab[None, :, :]) ** 2).sum(-1).argmin(1)
    return out.reshape(lab.shape[:-1])


def parse_spool(token: str) -> tuple[str, str]:
    """Resolve one palette token: '#hex', a built-in spool name, or 'Name=#hex' (color override)."""
    t = token.strip()
    if "=" in t:
        name, hx = (x.strip() for x in t.split("=", 1))
        if not _NAME_OK.match(name):
            raise ValueError(f"Bad spool name: {name!r}")
        known = {n.lower(): n for n, _ in BAMBU_PLA}
        return known.get(name.lower(), name), rgb_to_hex(hex_to_rgb(hx))
    if _HEX.match(t):
        h = rgb_to_hex(hex_to_rgb(t))
        return h, h
    for n, h in BAMBU_PLA:
        if n.lower() == t.lower():
            return n, h
    raise ValueError(f"Unknown palette entry: {token!r}")


def parse_palette(spec: str | None, allow_file: bool = True) -> list[tuple[str, str]]:
    """Custom palette: comma-separated hex/names or a file (one hex or 'Name,#hex' per line).

    Names are matched against the built-in Bambu list ("owned spools"); 'Name=#hex' keeps the
    name but uses a corrected color."""
    if not spec:
        return list(BAMBU_PLA)
    if allow_file and os.path.isfile(spec):
        with open(spec, encoding="utf-8") as f:
            tokens = [t.strip() for line in f for t in [line.strip()] if t and not t.startswith("#!")]
        items = []
        for t in tokens:
            if "," in t and _HEX.match(t.split(",")[-1].strip()):
                n, h = t.rsplit(",", 1)
                items.append((n.strip(), h.strip()))
            else:
                items.append(t)
    else:
        items = [t.strip() for t in spec.split(",") if t.strip()]
    out: list[tuple[str, str]] = []
    for it in items:
        if isinstance(it, tuple):
            out.append((it[0], rgb_to_hex(hex_to_rgb(it[1]))))
        else:
            out.append(parse_spool(it))
    if not out:
        raise ValueError("Palette is empty")
    return out


def auto_pick(rgb: np.ndarray, n: int, palette: list[tuple[str, str]], seed: int = 0,
              iters: int = 20) -> list[tuple[str, str]]:
    """k-means in CIELAB over the image, each center snapped to the nearest palette color."""
    if n < 1:
        raise ValueError("auto color count must be >= 1")
    pal_lab = palette_labs(palette)
    n = min(n, len(palette))
    flat = rgb.reshape(-1, 3)
    rng = np.random.default_rng(seed)
    if len(flat) > 20000:  # sample first, then convert only the sampled pixels
        flat = flat[rng.choice(len(flat), 20000, replace=False)]
    lab = rgb_to_lab(flat)
    # k-means++ init
    centers = [lab[rng.integers(len(lab))]]
    for _ in range(1, n):
        d = np.min([((lab - c) ** 2).sum(1) for c in centers], axis=0)
        p = d / d.sum() if d.sum() > 0 else None
        centers.append(lab[rng.choice(len(lab), p=p)])
    centers = np.array(centers)
    for _ in range(iters):
        assign = ((lab[:, None] - centers[None]) ** 2).sum(-1).argmin(1)
        for k in range(n):
            pts = lab[assign == k]
            if len(pts):
                centers[k] = pts.mean(0)
    counts = np.bincount(((lab[:, None] - centers[None]) ** 2).sum(-1).argmin(1), minlength=n)
    picked: list[int] = []
    for k in np.argsort(-counts):
        d = ((pal_lab - centers[k]) ** 2).sum(1)
        for idx in np.argsort(d):  # nearest not-yet-used palette color
            if int(idx) not in picked:
                picked.append(int(idx))
                break
    return [palette[i] for i in picked]
