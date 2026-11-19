"""Command line: python -m pixeltile image.jpg --width 200 --out out/"""
from __future__ import annotations

import argparse
import os
import sys

from .pipeline import MAX_TILE_LIMIT_MM, Settings, run


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="pixeltile", description="Image -> multicolor Bambu Studio 3MF tiles")
    p.add_argument("image", nargs="?", help="input image (omit with --web)")
    p.add_argument("--out", default="out", help="output directory")
    p.add_argument("--width", type=float, default=200.0, help="total art width in mm")
    p.add_argument("--pixel", type=float, default=2.0, help="pixel (tile cell) size in mm")
    p.add_argument("--max-tile-colors", type=int, default=8,
                   help="filament slots per tile incl. 1 backing slot (default 8 = H3C; use 4 for a standard AMS)")
    p.add_argument("--max-tile-size", type=float, default=200.0,
                   help=f"max tile edge in mm (default 200, up to {MAX_TILE_LIMIT_MM:g})")
    p.add_argument("--palette", help="custom palette: 'hex,hex,Spool Name,Spool Name=#hex' list or a file path "
                                     "(default: built-in Bambu PLA; 'Name=#hex' corrects a spool's color)")
    p.add_argument("--colors", type=int, default=16, help="auto-pick N colors from the image (CIELAB k-means); 0 = use full palette")
    p.add_argument("--backing-color", default="Black", help="backing filament (Bambu name or hex)")
    p.add_argument("--color-depth", type=float, default=1.2)
    p.add_argument("--backing", type=float, default=1.6, help="backing thickness in mm")
    p.add_argument("--layer-height", type=float, default=0.2)
    p.add_argument("--nozzle", type=float, default=0.4)
    p.add_argument("--contrast", type=float, default=1.0)
    p.add_argument("--saturation", type=float, default=1.0)
    p.add_argument("--dither", action="store_true", help="Floyd-Steinberg dithering")
    p.add_argument("--connector-keys", action="store_true",
                   help="add recessed back connector pockets on shared tile edges and export connectors.3mf")
    p.add_argument("--wall-mount", action="store_true",
                   help="add 12 mm back rim (7.7 mm total depth), top screw keyholes, and seam connector keys")
    p.add_argument("--web", action="store_true", help="start the local web UI")
    p.add_argument("--desktop", action="store_true", help="launch the native desktop window (pywebview)")
    p.add_argument("--port", type=int, default=8765)
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    if a.desktop:
        from .desktop import launch
        launch()
        return 0
    if a.web:
        from .webapp import serve
        serve(a.port)
        return 0
    if not a.image:
        print("error: image required (or use --web)", file=sys.stderr)
        return 2
    s = Settings(width_mm=a.width, pixel_mm=a.pixel, max_tile_colors=a.max_tile_colors,
                 max_tile_size_mm=a.max_tile_size, palette=a.palette, auto_colors=a.colors,
                 backing_color=a.backing_color, color_depth_mm=a.color_depth, backing_mm=a.backing,
                 layer_height=a.layer_height, nozzle=a.nozzle, contrast=a.contrast,
                 saturation=a.saturation, dither=a.dither, connector_keys=a.connector_keys,
                 wall_mount=a.wall_mount)
    try:
        r = run(a.image, s)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    os.makedirs(os.path.join(a.out, "tiles"), exist_ok=True)
    for n, b in r.tiles.items():
        with open(os.path.join(a.out, "tiles", n), "wb") as f:
            f.write(b)
    for n, b in r.swatches.items():
        with open(os.path.join(a.out, n), "wb") as f:
            f.write(b)
    if r.connectors_3mf:
        with open(os.path.join(a.out, "connectors.3mf"), "wb") as f:
            f.write(r.connectors_3mf)
    for n, b in (("preview.png", r.preview_png), ("tile_map.png", r.tile_map_png)):
        with open(os.path.join(a.out, n), "wb") as f:
            f.write(b)
    with open(os.path.join(a.out, "README.txt"), "w", encoding="utf-8") as f:
        f.write(r.readme)
    print(r.readme)
    for w in r.warnings:
        print(f"warning: {w}", file=sys.stderr)
    print(f"Wrote {len(r.tiles)} tile(s) to {os.path.abspath(a.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
