# PixelTile Studio

Convert images or custom pixel drawings into multicolor, face-down 3D-printable wall-art tiles exported as **Bambu Studio `.3mf`** files with every part already assigned to its filament slot.

## Features
- **Bambu Lab H3C & AMS Ready**: Defaults to 8 filament slots per tile (`T1` backing + up to 7 colors) and uniform tiles up to `300 × 300 mm`, with full support for 4-slot AMS (`--max-tile-colors 4`).
- **CIELAB Quantization & Built-in Palette**: 51 built-in Bambu PLA colors, custom hex/spool-name lists, CIELAB k-means auto-selection, and optional Floyd-Steinberg dithering.
- **Spool-Swap Scheduler**: Orders tiles to maximize color overlap with already-loaded tools and preserves slot numbers across consecutive prints.
- **Color-Loss Report**: When a tile needs more colors than it has slots, the Print Guide and studio show how much of each tile was recolored and which colors were dropped, with a warning when it is noticeable.
- **Manifold 3MF Geometry**: Greedy rectangle merging where every box is emitted as an independent closed 8-vertex / 12-triangle shell (zero T-junctions), with each part's filament slot stored in `Metadata/model_settings.config`.
- **Hardware & Calibration**: Exports color swatches covering every printed color (`swatch.3mf`, or `swatch_1.3mf`, `swatch_2.3mf`… when there are more colors than slots), optional edge connector pockets + `connectors.3mf` (`--connector-keys`), and an optional `12 mm` wall-mount back rim (`7.7 mm` total depth) with top-row screw keyholes (`--wall-mount`). Hardware is only exported when the tiles are big and thick enough to hold it; otherwise the guide says why it was skipped.
- **Filament Estimate**: Grams per color for each tile and for the whole mural (PLA, excluding purge and prime tower waste).
- **3-Zone Desktop & Web Studio**: Interactive Tile Map, Mosaic Preview, Print Guide, hue-grouped Owned Spools modal, and a built-in **Custom Image Tool** (draw, fill, eyedropper, rotate, flip, undo). Your owned spools, color corrections and settings are remembered between launches.

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
Every pull request also builds the Windows app in GitHub Actions; download it from the run's **Artifacts**.

## Opening Tiles in Bambu Studio
Click **▶ Bambu Studio** next to a tile, swatch or connector file in the print queue. PixelTile saves the file and opens it in Bambu Studio. It looks for Bambu Studio in the usual install locations (on Windows it also checks which program opens `.3mf` files). If it isn't found, enter its location under **Advanced Slicer & Geometry Tuning**. To open every file in the same Bambu Studio window, turn on Bambu Studio's single-instance preference.

What to expect when a tile opens:
- Your own printer, process and filament presets stay selected.
- Every part is already on its filament slot (T1 = backing, then the colors listed in the Print Guide).
- Bambu Studio shows a notice that the file is not from Bambu Lab. It can be turned off in Bambu Studio's settings.
- Set each slot's filament color to match the Print Guide, or sync your AMS, then slice with the layer height the guide gives (0.2 mm by default, first layer included).

Why the files carry no colors or print settings: for 3MF files made by other programs, Bambu Studio 2.9 turns a settings file into new presets built from its generic defaults (replacing yours), and any colored part makes it erase every part's slot and run its color import dialog instead. Leaving both out is what keeps your presets and the slot assignments intact.

## Correcting Spool Colors
The built-in colors are approximations. After printing a swatch, open **Owned Spools**, turn on **Correct colors** and click a spool to set the color it really prints. On the command line, write the correction as `Name=#hex`:
```bash
python -m pixeltile photo.jpg --palette "Black,Jade White,Red=#B02A1C,Sky Blue=#5599CC"
```

## Print Settings That Are Checked
Color depth and backing must be whole numbers of layers (for example `1.2 mm` = 6 × `0.2 mm`), the pixel size cannot be smaller than the nozzle, and very large grids (over 6 million pixels) are refused with a suggestion instead of running out of memory.

## Saved Settings
The studio saves your spools, corrections and last settings to:
- Windows: `%APPDATA%\PixelTile Studio\prefs.json`
- macOS / Linux: `~/.config/pixeltile/prefs.json`

Delete the file to reset. Set `PIXELTILE_CONFIG_DIR` to store it somewhere else.

## Development
```bash
pip install -e ".[test,desktop]"
python -m pytest -v
```
The studio page lives in `pixeltile/static/` (`index.html`, `app.css`, `app.js`) and is served by `pixeltile/webapp.py`.
