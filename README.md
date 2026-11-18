# PixelTile Studio

Convert images or custom pixel drawings into multicolor, face-down 3D-printable wall-art tiles exported as pre-colored **Bambu Studio `.3mf`** files.

## Features
- **Bambu Lab H3C & AMS Ready**: Defaults to 8 filament slots per tile (`T1` backing + up to 7 colors) and uniform tiles up to `300 × 300 mm`, with full support for 4-slot AMS (`--max-tile-colors 4`).
- **CIELAB Quantization & Built-in Palette**: 51 built-in Bambu PLA colors, custom hex/spool-name lists, CIELAB k-means auto-selection, and optional Floyd-Steinberg dithering.
- **Spool-Swap Scheduler**: Orders tiles to maximize color overlap with already-loaded tools and preserves slot numbers across consecutive prints.
- **Manifold 3MF Geometry**: Greedy rectangle merging where every box is emitted as an independent closed 8-vertex / 12-triangle shell (zero T-junctions), with `Metadata/model_settings.config` and `Metadata/project_settings.config` pre-populated.
- **Hardware & Calibration**: Exports `swatch.3mf` for color verification, optional edge connector pockets + `connectors.3mf` (`--connector-keys`), and an optional `12 mm` wall-mount back rim (`7.7 mm` total depth) with top-row screw keyholes (`--wall-mount`).
- **3-Zone Desktop & Web Studio**: Interactive Tile Map, Mosaic Preview, Print Guide, hue-grouped Owned Spools modal, and a built-in **Custom Image Tool** (draw, fill, eyedropper, rotate, flip, undo).

## Quick Start

```bash
pip install -r requirements.txt
```

### Desktop App (Native Window)
```bash
python -m pixeltile --desktop
```

### Local Web Studio
```bash
python -m pixeltile --web
# Open http://127.0.0.1:8765
```

### CLI
```bash
python -m pixeltile photo.jpg --width 400 --pixel 2 --wall-mount --out out
```

### Build Windows `.exe`
```powershell
.\build_exe.ps1           # Outputs dist\PixelTileStudio\PixelTileStudio.exe
.\build_exe.ps1 -OneFile  # Outputs single-file dist\PixelTileStudio.exe
```

## Tests
```bash
python -m pytest -v
```
