"""End-to-end pipeline: image bytes -> tiles (.3mf) + previews + readme."""
from __future__ import annotations

import io
import math
import zipfile
from dataclasses import dataclass, field, fields

import numpy as np
from PIL import Image, ImageDraw

from . import process
from .bambu3mf import write_3mf
from .geometry import (MIN_HARDWARE_TILE_MM, MIN_POCKET_BACKING_MM, WALL_DEPTH_MM,
                       connector_key_parts, flat_pockets_fit, rim_fits, swatch_parts, tile_parts)
from .palette import auto_pick, hex_to_rgb, palette_labs, parse_palette, parse_spool
from .tiling import schedule_tiles, split_tiles, tile_name

MAX_TILE_LIMIT_MM = 300.0
MAX_GRID_CELLS = 6_000_000      # about 1.2 x 1.2 m at 0.5 mm pixels; keeps memory and time bounded
PLA_DENSITY_G_CM3 = 1.24
COLOR_LOSS_WARN = 0.05          # warn when a tile had to recolor more than 5% of its pixels


def _check_layers(label: str, mm: float, layer: float) -> None:
    """Thicknesses must be a whole number (>= 1) of layers, or the slicer silently rounds them."""
    n = mm / layer
    if n < 1 - 1e-6:
        raise ValueError(f"{label} must be at least one {layer:g} mm layer (got {mm:g} mm)")
    if abs(n - round(n)) > 1e-6:
        lo, hi = math.floor(n) * layer, math.ceil(n) * layer
        raise ValueError(f"{label} {mm:g} mm is not a whole number of {layer:g} mm layers; "
                         f"use {round(lo, 4):g} or {round(hi, 4):g} mm")


@dataclass
class Settings:
    width_mm: float = 200.0
    pixel_mm: float = 2.0
    max_tile_colors: int = 8        # total filament slots per tile (incl. 1 backing slot)
    max_tile_size_mm: float = 200.0
    palette: str | None = None      # None = built-in Bambu list; or "hex,hex,Name,Name=#hex" / file path
    auto_colors: int = 16           # k-means picks this many palette colors from the image (0 = use whole palette)
    backing_color: str = "Black"
    color_depth_mm: float = 1.2     # 6 x 0.2 mm
    backing_mm: float = 1.6
    layer_height: float = 0.2
    nozzle: float = 0.4
    contrast: float = 1.0
    saturation: float = 1.0
    dither: bool = False
    connector_keys: bool = False
    wall_mount: bool = False

    def validate(self) -> None:
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v, float) and not math.isfinite(v):
                raise ValueError(f"{f.name} must be a finite number")
        if not 2 <= self.max_tile_colors <= 9:
            raise ValueError("max_tile_colors must be 2..9 (1 slot is reserved for the backing)")
        if not 10 <= self.max_tile_size_mm <= MAX_TILE_LIMIT_MM:
            raise ValueError(f"max_tile_size_mm must be 10..{MAX_TILE_LIMIT_MM:g}")
        if self.layer_height <= 0 or self.nozzle <= 0:
            raise ValueError("layer height and nozzle must be greater than 0")
        if self.pixel_mm <= 0 or self.width_mm < self.pixel_mm:
            raise ValueError("invalid width/pixel size")
        if self.pixel_mm < self.nozzle:
            raise ValueError(f"pixel size {self.pixel_mm:g} mm is smaller than the "
                             f"{self.nozzle:g} mm nozzle and cannot be printed")
        _check_layers("color depth", self.color_depth_mm, self.layer_height)
        _check_layers("backing", self.backing_mm, self.layer_height)
        if self.auto_colors < 0:
            raise ValueError("auto colors must be 0 (use the whole palette) or more")
        if self.contrast < 0 or self.saturation < 0:
            raise ValueError("contrast and saturation must be 0 or more")


@dataclass
class Result:
    tiles: dict[str, bytes] = field(default_factory=dict)     # "A1.3mf" -> bytes (empty in preview mode)
    swatches: dict[str, bytes] = field(default_factory=dict)  # "swatch.3mf" / "swatch_1.3mf" -> bytes
    swatch_names: list[str] = field(default_factory=list)
    connectors_3mf: bytes = b""
    has_connectors: bool = False
    preview_png: bytes = b""
    tile_map_png: bytes = b""
    readme: str = ""
    schedule: list[dict] = field(default_factory=list)
    grid: tuple[int, int] = (0, 0)
    tile_size_mm: tuple[float, float] = (0, 0)
    warnings: list[str] = field(default_factory=list)
    filament: list[dict] = field(default_factory=list)       # per color totals over all tiles, heaviest first
    files_built: bool = True

    def zip_bytes(self) -> bytes:
        if not self.files_built:
            raise ValueError("print files were not generated (preview-only run)")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for n, b in self.tiles.items():
                z.writestr(f"tiles/{n}", b)
            for n, b in self.swatches.items():
                z.writestr(n, b)
            if self.connectors_3mf:
                z.writestr("connectors.3mf", self.connectors_3mf)
            z.writestr("preview.png", self.preview_png)
            z.writestr("tile_map.png", self.tile_map_png)
            z.writestr("README.txt", self.readme)
        return buf.getvalue()


def _resolve_backing(spec: str) -> tuple[str, str]:
    try:
        return parse_spool(spec)
    except ValueError:
        raise ValueError(f"Unknown backing color: {spec!r} (use a spool name or #RRGGBB)") from None


def _grams(volume_mm3: float) -> float:
    return volume_mm3 / 1000.0 * PLA_DENSITY_G_CM3


def _render(idx: np.ndarray, pal_rgb: np.ndarray, scale: int) -> Image.Image:
    img = Image.fromarray(pal_rgb[idx].astype(np.uint8), "RGB")
    return img.resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST)


def sample_artwork_png(name: str = "wave", w: int = 240, h: int = 160) -> bytes:
    """Procedurally generate recognizable sample artworks for testing and the web studio."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float64)
    nx, ny = x / w, y / h
    arr = np.zeros((h, w, 3), dtype=np.uint8)

    if name == "wave":
        # Warm cream sky -> slate horizon -> Prussian blue swells & curling foam crest
        sky_t = np.clip(ny / 0.65, 0.0, 1.0)
        arr[:, :] = np.stack([
            240 - 30 * sky_t,
            227 - 32 * sky_t,
            200 - 25 * sky_t,
        ], axis=-1)
        # Distant Fuji silhouette
        fuji_top = 0.56 + 0.48 * np.abs(nx - 0.72)
        fuji = (ny >= fuji_top) & (ny < 0.74)
        arr[fuji] = [12, 42, 118]
        arr[fuji & (ny < fuji_top + 0.045)] = [245, 245, 242]
        # Main curling wave and lower sea
        crest_y = 0.44 + 0.17 * np.sin((nx - 0.12) * 5.2) + 0.08 * np.cos(nx * 11.0)
        deep = ny >= crest_y
        arr[deep] = [10, 41, 137]
        mid_band = (ny >= crest_y) & (ny < crest_y + 0.14)
        arr[mid_band] = [0, 108, 190]
        sky_band = (ny >= crest_y + 0.14) & (ny < crest_y + 0.24)
        arr[sky_band] = [115, 178, 230]
        # Foam fingers along crest & foreground curl
        foam = (ny >= crest_y - 0.03) & (ny < crest_y + 0.035 + 0.025 * np.sin(nx * 45.0))
        curl = ((nx - 0.36) ** 2 + (ny - 0.38) ** 2) < 0.015
        arr[foam | curl] = [255, 255, 255]
        # Wooden boat streak
        boat = (np.abs(ny - (0.72 + 0.15 * (nx - 0.5) ** 2)) < 0.022) & (nx > 0.15) & (nx < 0.85)
        arr[boat] = [211, 183, 167]
    elif name == "starry":
        # Deep indigo night sky with swirling cobalt/cyan bands, golden stars & dark cypress
        arr[:, :] = [16, 32, 90]
        swirl = np.sin(nx * 9.0 + np.cos(ny * 10.0) * 1.4) + np.cos(ny * 12.0 - nx * 4.0)
        arr[swirl > 0.35] = [0, 86, 184]
        arr[swirl > 1.05] = [115, 178, 230]
        arr[ny > 0.72] = [24, 34, 68]
        # Golden stars and moon
        stars = [(0.82, 0.22, 0.085), (0.34, 0.20, 0.045), (0.56, 0.30, 0.04), (0.20, 0.35, 0.035)]
        for sx, sy, sr in stars:
            dist = np.hypot(nx - sx, (ny - sy) * (h / w))
            arr[dist < sr * 1.6] = [228, 189, 104]
            arr[dist < sr] = [254, 198, 0]
        # Moon crescent bite
        bite = np.hypot(nx - 0.79, (ny - 0.20) * (h / w)) < 0.055
        arr[bite] = [0, 86, 184]
        # Cypress silhouette on left
        cypress = (np.abs(nx - 0.17 - 0.02 * np.sin(ny * 20.0)) < (0.065 * np.clip(ny, 0.1, 1.0))) & (ny > 0.12)
        arr[cypress] = [28, 28, 28]
        # Village lights
        for lx in (0.42, 0.52, 0.64, 0.74):
            win = (np.abs(nx - lx) < 0.015) & (np.abs(ny - 0.82) < 0.025)
            arr[win] = [244, 238, 42]
    elif name == "fuji":
        # Sky blue top -> white clouds -> red/terracotta Fuji -> pine green foothills
        arr[:, :] = [115, 178, 230]
        arr[ny < 0.28] = [0, 134, 214]
        clouds = (ny < 0.36) & (np.sin(nx * 14.0 + ny * 8.0) > 0.45)
        arr[clouds] = [255, 255, 255]
        slope = 0.20 + 0.92 * np.abs(nx - 0.50)
        cone = ny >= slope
        arr[cone] = [193, 46, 31]
        arr[cone & (ny > slope + 0.16)] = [177, 85, 51]
        snow = cone & (ny < slope + 0.08 + 0.025 * np.sin(nx * 36.0)) & (ny < 0.34)
        arr[snow] = [255, 255, 255]
        arr[ny > 0.74 + 0.03 * np.sin(nx * 18.0)] = [11, 106, 60]
    elif name == "grid":
        palette_blocks = [
            [193, 46, 31], [255, 106, 19], [244, 238, 42], [0, 174, 66],
            [0, 134, 214], [94, 67, 183], [236, 0, 140], [255, 255, 255],
        ]
        gx = np.clip((nx * 4).astype(int), 0, 3)
        gy = np.clip((ny * 2).astype(int), 0, 1)
        for j in range(2):
            for i in range(4):
                arr[(gy == j) & (gx == i)] = palette_blocks[j * 4 + i]
    else:
        raise ValueError(f"Unknown sample artwork: {name!r}")

    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, "PNG")
    return buf.getvalue()


def run(src, s: Settings, allow_palette_file: bool = True, build_files: bool = True) -> Result:
    """Convert an image into scheduled tiles.

    With build_files=False the .3mf files are not written (used for fast studio previews);
    everything else, including the print guide, is still computed."""
    s.validate()
    img = process.adjust(process.load_image(src), s.contrast, s.saturation)
    cols, rows, tx, ty = process.grid_size(img.width, img.height, s.width_mm, s.pixel_mm, s.max_tile_size_mm)
    if cols * rows > MAX_GRID_CELLS:
        raise ValueError(f"{cols} x {rows} pixels is too large (limit {MAX_GRID_CELLS / 1e6:g} million); "
                         "reduce the width or use a larger pixel size")
    rgb = process.resize(img, cols, rows)

    full = parse_palette(s.palette, allow_file=allow_palette_file)
    palette = auto_pick(rgb, s.auto_colors, full) if s.auto_colors and s.auto_colors < len(full) else full
    pal_rgb = np.array([hex_to_rgb(h) for _, h in palette])
    pal_lab = palette_labs(palette)

    idx = process.quantize(rgb, pal_rgb, s.dither)
    budget = s.max_tile_colors - 1  # slot 1 = backing
    tiles = split_tiles(idx, tx, ty, pal_lab, budget)
    scheduled = schedule_tiles(tiles, budget)
    backing = _resolve_backing(s.backing_color)

    th, tw = rows // ty, cols // tx
    res = Result(grid=(tx, ty), tile_size_mm=(tw * s.pixel_mm, th * s.pixel_mm), files_built=build_files)
    tile_w, tile_h = res.tile_size_mm

    # Decide which hardware can actually be built at this tile size / thickness.
    z_base = s.color_depth_mm + s.backing_mm
    wall = s.wall_mount
    if wall and not rim_fits(tile_w, tile_h, z_base):
        wall = False
        res.warnings.append(
            f"Wall-mount rim skipped: it needs tiles of at least {MIN_HARDWARE_TILE_MM:g} mm and color depth "
            f"+ backing under {WALL_DEPTH_MM - 0.4:g} mm. Tiles are flat-backed with no keyholes.")
    want_keys = tx * ty > 1 and (s.connector_keys or s.wall_mount)
    pockets_ok = rim_fits(tile_w, tile_h, z_base) if wall else flat_pockets_fit(tile_w, tile_h, s.backing_mm)
    use_connectors = want_keys and pockets_ok
    if want_keys and not pockets_ok:
        res.warnings.append(
            f"Connector keys skipped: seam pockets need tiles of at least {MIN_HARDWARE_TILE_MM:g} mm and a "
            f"backing of at least {MIN_POCKET_BACKING_MM:g} mm. Join the tiles with glue or tape instead.")

    lines = [
        f"PixelTile output: {tx} x {ty} tiles, each {tile_w:g} x {tile_h:g} mm",
        f"Pixel {s.pixel_mm:g} mm, color layer {s.color_depth_mm:g} mm, backing {s.backing_mm:g} mm",
        f"Printed FACE DOWN. Filament slot 1 = backing ({backing[0]}).",
        "",
        "Optimized Print Queue & Spool Swap Schedule:",
    ]
    full_idx = np.zeros_like(idx)
    active_slot_hex = [backing[1]] + ["#FFFFFF"] * budget
    loaded_cid: dict[int, int] = {}
    total_w = cols * s.pixel_mm
    if not wall:
        keyhole_cols: set[int] = set()
    elif tx == 1:
        keyhole_cols = {0}
    elif total_w <= 800.0 or tx == 2:
        keyhole_cols = {0, tx - 1}
    else:
        keyhole_cols = {0, tx // 2, tx - 1}

    grams_by_color: dict[tuple[str, str], float] = {}
    for step, t in enumerate(scheduled):
        full_idx[t.row * th:(t.row + 1) * th, t.col * tw:(t.col + 1) * tw] = t.idx
        shared_edges = (
            (t.row > 0, t.row + 1 < ty, t.col > 0, t.col + 1 < tx)
            if use_connectors else (False, False, False, False)
        )
        parts = tile_parts(
            t.idx, palette, s.pixel_mm, s.color_depth_mm, s.backing_mm, backing,
            slot_map=t.slot_map,
            shared_edges=shared_edges,
            wall_mount=wall,
            is_top_row=(t.row == 0),
            is_bottom_row=(t.row == ty - 1),
            has_keyhole=(wall and t.row == 0 and t.col in keyhole_cols),
        )
        swaps = 0
        for cid, slot in t.slot_map.items():
            if step > 0 and loaded_cid.get(slot) != cid:
                swaps += 1
            loaded_cid[slot] = cid
        for p in parts:
            active_slot_hex[p.extruder - 1] = p.hex

        if build_files:
            res.tiles[f"{t.name}.3mf"] = write_3mf(
                parts, f"PixelTile {t.name}", list(active_slot_hex), s.layer_height, s.nozzle
            )
        slots = []
        for p in parts:
            g = _grams(p.volume_mm3)
            key = (backing[0] if p.extruder == 1 else p.name, p.hex)
            grams_by_color[key] = grams_by_color.get(key, 0.0) + g
            slots.append({"slot": p.extruder, "name": p.name, "hex": p.hex, "grams": round(g, 1)})
        dropped = [palette[c][0] for c in t.dropped]
        res.schedule.append({
            "name": t.name,
            "row": t.row,
            "col": t.col,
            "slots": slots,
            "swaps_from_prev": swaps,
            "recolored_pct": round(100 * t.recolored_frac, 1),
            "dropped": dropped,
        })
        swap_note = "initial load" if step == 0 else f"{swaps} swap(s)"
        slot_desc = ", ".join(f"T{p.extruder}={p.name}" for p in parts)
        lines.append(f"{step + 1}. {t.name} ({swap_note}): {slot_desc}")
        if t.recolored:
            shown = ", ".join(dropped[:6]) + (f" and {len(dropped) - 6} more" if len(dropped) > 6 else "")
            lines.append(f"   ! {100 * t.recolored_frac:.1f}% of pixels recolored to fit {budget} colors "
                         f"(dropped: {shown})")

    lossy = sorted((t for t in tiles if t.recolored_frac > COLOR_LOSS_WARN), key=lambda t: -t.recolored_frac)
    if lossy:
        worst = ", ".join(f"{t.name} ({100 * t.recolored_frac:.0f}%)" for t in lossy[:5])
        more = f" and {len(lossy) - 5} more" if len(lossy) > 5 else ""
        res.warnings.append(
            f"Color loss: tiles {worst}{more} use more colors than the {budget} color slots per tile, so some "
            "pixels were recolored. Lower the color limit, allow more slots per tile, or use smaller tiles.")

    used_ids, used_counts = np.unique(full_idx, return_counts=True)
    used_order = [int(i) for i in used_ids[np.argsort(-used_counts, kind="stable")]]
    groups = [used_order[i:i + budget] for i in range(0, len(used_order), budget)]
    if groups:
        lines.append("")
        lines.append("Color swatches (print these first to check your spools match the preview):")
    for gi, group in enumerate(groups):
        name = "swatch.3mf" if len(groups) == 1 else f"swatch_{gi + 1}.3mf"
        sw_parts, sw_slots = swatch_parts(
            group, palette, backing, s.max_tile_colors, s.color_depth_mm, s.backing_mm
        )
        res.swatch_names.append(name)
        if build_files:
            res.swatches[name] = write_3mf(sw_parts, "PixelTile Swatch", sw_slots, s.layer_height, s.nozzle)
        lines.append(f"  {name}: " + ", ".join(f"T{p.extruder}={p.name}" for p in sw_parts[1:]))

    if use_connectors:
        n_edges = (tx - 1) * ty + tx * (ty - 1)
        pocket_z = (WALL_DEPTH_MM - z_base) * 0.5 if wall else s.backing_mm * 0.5
        key_t = max(0.4, min(1.2 if wall else 0.8, round(pocket_z, 2)))
        res.has_connectors = True
        if build_files:
            conn_parts = connector_key_parts(n_edges + 2, backing, thickness_mm=key_t)
            conn_slots = [backing[1]] + ["#FFFFFF"] * budget
            res.connectors_3mf = write_3mf(
                conn_parts, "PixelTile Connector Keys", conn_slots, s.layer_height, s.nozzle
            )
        lines.append("")
        lines.append(f"Connector keys: {n_edges + 2} keys exported to connectors.3mf ({n_edges} seams + 2 spares).")
        if wall:
            lines.append("Keys sit recessed in the back rim; glue them into one side of each seam.")

    if wall:
        lines.append("")
        lines.append("Wall-Mount Hardware & Hanging Guide:")
        lines.append(f"Depth: {WALL_DEPTH_MM:g} mm total (12 mm back rim, flush wall standoffs).")
        xs = [f"{(c + 0.5) * tile_w:g} mm ({tile_name(0, c)})" for c in sorted(keyhole_cols)]
        lines.append("Top-row screw keyhole centers (from left edge): " + ", ".join(xs))

    res.filament = [{"name": n, "hex": h, "grams": round(g, 1)}
                    for (n, h), g in sorted(grams_by_color.items(), key=lambda kv: -kv[1])]
    total_g = sum(grams_by_color.values())
    lines.append("")
    lines.append(f"Filament estimate for all tiles (PLA at {PLA_DENSITY_G_CM3:g} g/cm3; "
                 "purge and prime tower waste not included):")
    for f in res.filament:
        lines.append(f"  {f['name']}: {f['grams']:.0f} g")
    lines.append(f"  Total: {total_g:.0f} g")

    if res.warnings:
        lines = ["WARNINGS:"] + [f"- {w}" for w in res.warnings] + [""] + lines

    def png(im: Image.Image) -> bytes:
        b = io.BytesIO()
        im.save(b, "PNG")
        return b.getvalue()

    scale = max(1, 800 // max(cols, rows))
    prev = _render(full_idx, pal_rgb, scale)
    res.preview_png = png(prev)
    tm = prev.copy()
    d = ImageDraw.Draw(tm)
    for t in tiles:
        x0, y0 = t.col * tw * scale, t.row * th * scale
        d.rectangle([x0, y0, x0 + tw * scale - 1, y0 + th * scale - 1], outline=(255, 0, 0))
        d.text((x0 + 4, y0 + 4), t.name, fill=(255, 255, 255))
    res.tile_map_png = png(tm)
    res.readme = "\n".join(lines) + "\n"
    return res
