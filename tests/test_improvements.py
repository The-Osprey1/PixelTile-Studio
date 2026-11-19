"""Tests for print-safety checks, color-loss reporting, speedups and the studio server."""
import io
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pixeltile.geometry import (KEY_H_MM, KEY_W_MM, Part, _cut_edge_pockets, merge_rects,
                                tile_parts)
from pixeltile.palette import BAMBU_PLA, hex_to_rgb, nearest, parse_palette, rgb_to_lab
from pixeltile.pipeline import PLA_DENSITY_G_CM3, Settings, run, sample_artwork_png
from pixeltile.process import quantize


def png_bytes(arr: np.ndarray) -> bytes:
    b = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8)).save(b, "PNG")
    return b.getvalue()


def noisy_png(w=120, h=80, seed=3) -> bytes:
    """Smooth multi-hue gradients plus noise: behaves like a photo (many colors per tile)."""
    y, x = np.mgrid[0:h, 0:w] / np.array([h, w])[:, None, None]
    rng = np.random.default_rng(seed)
    arr = np.stack([128 + 110 * np.sin(6 * x + 2 * y),
                    128 + 110 * np.sin(4 * y + 1.5 + 3 * x * y),
                    128 + 110 * np.cos(5 * x - 3 * y)], -1) + rng.normal(0, 8, (h, w, 3))
    return png_bytes(np.clip(arr, 0, 255))


# --- Connector pockets always fit the key ---------------------------------------------------

@pytest.mark.parametrize("pixel_mm", [round(0.5 + 0.1 * i, 1) for i in range(66)])  # 0.5 .. 7.0 mm
def test_connector_key_fits_pockets_at_any_pixel_size(pixel_mm):
    mask = np.ones((400, 400), dtype=bool)
    _cut_edge_pockets(mask, pixel_mm, (True, False, False, False))
    depth = int((~mask[:, 200]).sum()) * pixel_mm
    length = int((~mask[0, :]).sum()) * pixel_mm
    assert 2 * depth >= KEY_W_MM + 0.2  # key spans the seam: one pocket on each tile
    assert length >= KEY_H_MM + 0.2


# --- Input validation ------------------------------------------------------------------------

@pytest.mark.parametrize("kw, msg", [
    (dict(color_depth_mm=0), "color depth must be at least one"),
    (dict(backing_mm=0), "backing must be at least one"),
    (dict(color_depth_mm=1.1), "use 1 or 1.2 mm"),
    (dict(backing_mm=1.5), "not a whole number"),
    (dict(auto_colors=-1), "auto colors"),
    (dict(pixel_mm=0.3), "smaller than the 0.4 mm nozzle"),
    (dict(contrast=-1), "contrast"),
    (dict(width_mm=float("nan")), "finite"),
    (dict(layer_height=0), "layer height"),
])
def test_bad_settings_are_rejected(kw, msg):
    with pytest.raises(ValueError, match=msg):
        Settings(**kw).validate()


def test_finer_layers_accept_matching_thickness():
    Settings(layer_height=0.12, color_depth_mm=1.2, backing_mm=1.68).validate()


def test_oversized_grid_is_rejected_before_heavy_work():
    with pytest.raises(ValueError, match="too large"):
        run(sample_artwork_png("grid"), Settings(width_mm=5000, pixel_mm=0.5, max_tile_size_mm=300))


# --- Hardware is only exported when it is actually built --------------------------------------

def test_no_connector_keys_when_backing_too_thin_for_pockets():
    r = run(sample_artwork_png("grid"), Settings(width_mm=400, backing_mm=0.8, connector_keys=True))
    assert r.grid[0] > 1
    assert not r.has_connectors and not r.connectors_3mf
    assert "connectors.3mf" not in r.readme
    assert any("Connector keys skipped" in w for w in r.warnings)


def test_no_wall_mount_hardware_when_tiles_too_small():
    r = run(sample_artwork_png("grid"), Settings(width_mm=60, pixel_mm=1, max_tile_size_mm=30, wall_mount=True))
    assert r.tile_size_mm[0] < 40
    assert "keyhole centers" not in r.readme
    assert not r.has_connectors
    assert any("Wall-mount rim skipped" in w for w in r.warnings)


def test_wall_mount_still_builds_when_it_fits():
    r = run(sample_artwork_png("grid"), Settings(width_mm=400, wall_mount=True))
    assert not r.warnings
    assert r.has_connectors and "keyhole" in r.readme


# --- Color loss is reported, swatches cover every printed color ------------------------------

def test_color_loss_reported_per_tile_and_as_warning():
    r = run(noisy_png(), Settings(width_mm=240, max_tile_size_mm=80, max_tile_colors=4))
    lossy = [row for row in r.schedule if row["recolored_pct"] > 0]
    assert lossy, "a photo-like image on 3 color slots must lose some colors"
    assert all(row["dropped"] for row in lossy)
    assert any(w.startswith("Color loss") for w in r.warnings)
    assert "% of pixels recolored" in r.readme
    assert r.readme.startswith("WARNINGS:")


def test_flat_art_has_no_color_loss():
    r = run(sample_artwork_png("grid"), Settings(width_mm=200, auto_colors=6))
    assert all(row["recolored_pct"] == 0 for row in r.schedule)
    assert not r.warnings


def test_swatches_cover_every_printed_color():
    s = Settings(width_mm=240, max_tile_size_mm=80, max_tile_colors=4, auto_colors=16)
    r = run(noisy_png(), s)
    printed = {sl["name"] for row in r.schedule for sl in row["slots"] if sl["slot"] > 1}
    swatched = set()
    for name in r.swatch_names:
        data = r.swatches[name]
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            model = z.read("3D/3dmodel.model").decode()
        swatched |= set(re.findall(r'<object id="\d+" name="([^"]+)"', model)) - {"PixelTile Swatch"}
    swatched = {n for n in swatched if not n.startswith("Backing")}
    assert len(r.swatch_names) > 1 and all(n.startswith("swatch_") for n in r.swatch_names)
    assert printed == swatched
    with zipfile.ZipFile(io.BytesIO(r.zip_bytes())) as z:
        assert set(r.swatch_names) <= set(z.namelist())


# --- Filament estimate ----------------------------------------------------------------------

def test_filament_grams_match_geometry():
    s = Settings(width_mm=100, pixel_mm=2, auto_colors=4)
    r = run(sample_artwork_png("grid"), s)
    # Color layers: every pixel is pixel^2 x color depth; the backing is a full plate.
    cells = (r.tile_size_mm[0] / s.pixel_mm) * (r.tile_size_mm[1] / s.pixel_mm)
    color_g = sum(f["grams"] for f in r.filament if f["name"] != "Black")
    black_backing = r.tile_size_mm[0] * r.tile_size_mm[1] * s.backing_mm / 1000 * PLA_DENSITY_G_CM3
    total = cells * s.pixel_mm ** 2 * s.color_depth_mm / 1000 * PLA_DENSITY_G_CM3 + black_backing
    assert sum(f["grams"] for f in r.filament) == pytest.approx(total, abs=0.5)
    assert color_g > 0
    assert "Filament estimate" in r.readme and "Total:" in r.readme


def test_part_volume():
    p = Part("x", "#000000", 1, [(0, 0, 0, 2, 3, 4), (10, 10, 1, 11, 11, 2)])
    assert p.volume_mm3 == pytest.approx(24 + 1)


# --- Spool color corrections --------------------------------------------------------------

def test_spool_color_override_keeps_name_and_uses_new_color():
    pal = parse_palette("Red=#B02A1C,black")
    assert pal == [("Red", "#B02A1C"), ("Black", "#000000")]
    r = run(sample_artwork_png("grid"), Settings(width_mm=100, palette="Red=#B02A1C,Black,Jade White",
                                                 auto_colors=0, backing_color="Black=#101010"))
    names = {sl["name"]: sl["hex"] for row in r.schedule for sl in row["slots"]}
    assert names.get("Red") == "#B02A1C"
    assert names.get("Backing (Black)") == "#101010"


@pytest.mark.parametrize("bad", ["<script>=#000000", "Red=#12", "Nope"])
def test_bad_palette_tokens_rejected(bad):
    with pytest.raises(ValueError):
        parse_palette(bad)


# --- Speedups give the same answers ------------------------------------------------------------

def _reference_dither(rgb, pal_rgb):
    pal_lab, work = rgb_to_lab(pal_rgb), rgb_to_lab(rgb).copy()
    h, w, _ = work.shape
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


@pytest.mark.parametrize("shape", [(1, 1), (1, 9), (9, 1), (2, 2), (17, 23), (40, 31)])
def test_fast_dither_matches_classic_floyd_steinberg(shape):
    rng = np.random.default_rng(sum(shape))
    rgb = rng.integers(0, 256, (*shape, 3)).astype(np.uint8)
    pal_rgb = np.array([hex_to_rgb(h) for _, h in BAMBU_PLA[:10]])
    assert (quantize(rgb, pal_rgb, dither=True) == _reference_dither(rgb, pal_rgb)).all()


def test_nearest_in_blocks_matches_direct(monkeypatch):
    import pixeltile.palette as pal_mod
    rng = np.random.default_rng(0)
    lab = rgb_to_lab(rng.integers(0, 256, (37, 29, 3)))
    pal_lab = rgb_to_lab(np.array([hex_to_rgb(h) for _, h in BAMBU_PLA]))
    direct = ((lab.reshape(-1, 1, 3) - pal_lab[None]) ** 2).sum(-1).argmin(1).reshape(37, 29)
    monkeypatch.setattr(pal_mod, "_NEAREST_CHUNK", 100)  # force many blocks
    assert (nearest(lab, pal_lab) == direct).all()


def _reference_merge_rects(mask):
    rects, active = [], {}
    for r in range(mask.shape[0] + 1):
        runs = set()
        if r < mask.shape[0]:
            row, c, n = mask[r], 0, mask.shape[1]
            while c < n:
                if row[c]:
                    s = c
                    while c < n and row[c]:
                        c += 1
                    runs.add((s, c))
                else:
                    c += 1
        for key in list(active):
            if key not in runs:
                rects.append((active.pop(key), key[0], r, key[1]))
        for key in runs:
            active.setdefault(key, r)
    return rects


@pytest.mark.parametrize("seed", range(6))
def test_fast_merge_rects_matches_original(seed):
    rng = np.random.default_rng(seed)
    mask = rng.random((int(rng.integers(1, 60)), int(rng.integers(1, 60)))) < rng.random()
    assert merge_rects(mask) == _reference_merge_rects(mask)


def test_preview_mode_skips_files_but_matches_full_run():
    s = Settings(width_mm=300, connector_keys=True)
    full = run(sample_artwork_png("starry"), s)
    quick = run(sample_artwork_png("starry"), s, build_files=False)
    assert quick.readme == full.readme and quick.schedule == full.schedule
    assert quick.swatch_names == full.swatch_names and quick.has_connectors == full.has_connectors
    assert not quick.tiles and not quick.swatches and not quick.connectors_3mf
    with pytest.raises(ValueError):
        quick.zip_bytes()


# --- Studio server --------------------------------------------------------------------------

@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("PIXELTILE_CONFIG_DIR", str(tmp_path))
    from pixeltile.desktop import start_server
    srv, port = start_server()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()
    srv.server_close()


def _req(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_server_serves_page_and_static_files(server):
    code, body, _ = _req(server + "/")
    page = body.decode()
    assert code == 200 and "__CONFIG__" not in page and "window.PIXELTILE_CONFIG" in page
    for name in ("app.js", "app.css"):
        code, body, _ = _req(f"{server}/static/{name}")
        assert code == 200 and len(body) > 1000
    assert _req(server + "/static/../webapp.py")[0] == 404
    assert _req(server + "/nope")[0] == 404


def test_server_inspect_export_and_file(server):
    img = sample_artwork_png("starry")
    code, body, _ = _req(server + "/api/inspect?width_mm=400&max_tile_colors=4", img, method="POST")
    data = json.loads(body)
    assert code == 200
    assert {"warnings", "filament", "swatches", "schedule", "has_connectors"} <= set(data)
    code, body, _ = _req(server + "/api/export?width_mm=400&max_tile_colors=4", img, method="POST")
    assert code == 200
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        assert any(n.startswith("tiles/") for n in z.namelist())
    tile = data["schedule"][0]["name"] + ".3mf"
    code, body, headers = _req(f"{server}/api/file?width_mm=400&max_tile_colors=4&file={tile}", img, method="POST")
    assert code == 200 and body[:2] == b"PK" and tile in headers["Content-Disposition"]
    swatch = data["swatches"][0]
    assert _req(f"{server}/api/file?width_mm=400&max_tile_colors=4&file={swatch}", img, method="POST")[0] == 200


def test_server_reports_bad_input_clearly(server):
    code, body, _ = _req(server + "/api/inspect?color_depth_mm=1.1", sample_artwork_png("grid"), method="POST")
    assert code == 400 and b"whole number" in body
    code, body, _ = _req(server + "/api/inspect?width_mm=abc", sample_artwork_png("grid"), method="POST")
    assert code == 400 and b"not a valid number" in body
    code, body, _ = _req(server + "/api/inspect", b"not an image", method="POST")
    assert code == 400


def test_server_rejects_other_sites_and_hosts(server):
    img = sample_artwork_png("grid")
    port = server.rsplit(":", 1)[1]
    assert _req(server + "/api/inspect", img, {"Origin": "https://evil.example"}, "POST")[0] == 403
    assert _req(server + "/api/inspect", img, {"Origin": f"http://127.0.0.1:{port}"}, "POST")[0] == 200
    assert _req(server + "/", headers={"Host": "evil.example"})[0] == 403


def test_server_rejects_oversized_upload(server, monkeypatch):
    import pixeltile.webapp as webapp
    monkeypatch.setattr(webapp, "MAX_UPLOAD_BYTES", 1000)
    assert _req(server + "/api/inspect", b"x" * 2000, method="POST")[0] == 413


def test_server_prefs_round_trip(server, tmp_path):
    assert json.loads(_req(server + "/api/prefs")[1]) == {}
    prefs = {"version": 1, "spools": ["Black", "Red"], "overrides": {"Red": "#B02A1C"}}
    code, _, _ = _req(server + "/api/prefs", json.dumps(prefs).encode(),
                      {"Content-Type": "application/json"}, "POST")
    assert code == 200
    assert json.loads(_req(server + "/api/prefs")[1]) == prefs
    assert json.loads((tmp_path / "prefs.json").read_text(encoding="utf-8")) == prefs
    assert _req(server + "/api/prefs", b"[1,2]", method="POST")[0] == 400
    assert _req(server + "/api/prefs", b"{oops", method="POST")[0] == 400


@pytest.mark.parametrize("seed", range(3))
def test_quantize_unique_color_shortcut_matches_direct(seed):
    rng = np.random.default_rng(seed)
    rgb = rng.integers(0, 6, (50, 70, 3)).astype(np.uint8) * 51  # few distinct colors, many repeats
    rgb[::7] = rng.integers(0, 256, (rgb[::7].shape)).astype(np.uint8)  # plus some unique ones
    pal_rgb = np.array([hex_to_rgb(h) for _, h in BAMBU_PLA])
    pal_lab = rgb_to_lab(pal_rgb)
    direct = ((rgb_to_lab(rgb).reshape(-1, 1, 3) - pal_lab[None]) ** 2).sum(-1).argmin(1).reshape(50, 70)
    assert (quantize(rgb, pal_rgb) == direct).all()


# --- Files open in Bambu Studio without replacing presets or losing slots -------------------

def _bambu_view(data: bytes):
    """Read a 3MF the way Bambu Studio 2.9 does for files from other programs.

    Returns (looks_like_bambu_file, has_project_settings, any_colored_triangles, {part name: slot})."""
    z = zipfile.ZipFile(io.BytesIO(data))
    model = z.read("3D/3dmodel.model").decode()
    app = re.search(r'<metadata name="Application">([^<]*)</metadata>', model)
    is_bambu = bool(app and app.group(1).startswith("BambuStudio-"))
    has_project = "Metadata/project_settings.config" in z.namelist()
    colored = bool(re.search(r'<object[^>]* pid="', model) or re.search(r'<triangle[^>]* pid="', model))
    # Part metadata is matched to the component's object id (bbs_3mf.cpp, _generate_volumes_new).
    names = dict(re.findall(r'<object id="(\d+)" name="([^"]*)" type="model"><mesh>', model))
    comps = re.findall(r'<component objectid="(\d+)"/>', model)
    settings = z.read("Metadata/model_settings.config").decode()
    parts = re.findall(r'<part id="(\d+)" subtype="normal_part">.*?key="extruder" value="(\d+)"', settings)
    slots = {pid: int(ext) for pid, ext in parts}
    assert set(comps) == set(slots) == set(names), "every component needs its own part entry"
    return is_bambu, has_project, colored, {names[c]: slots[c] for c in comps}


def test_tiles_keep_presets_and_slots_in_bambu_studio():
    r = run(sample_artwork_png("starry"), Settings(width_mm=400, wall_mount=True))
    files = list(r.tiles.values()) + list(r.swatches.values()) + [r.connectors_3mf]
    for data in files:
        is_bambu, has_project, colored, _ = _bambu_view(data)
        assert not is_bambu, "must not claim to be a Bambu Studio file"
        assert not has_project, "project settings would replace the user's presets"
        assert not colored, "colors would make Bambu Studio erase the part slots"
    for row in r.schedule:
        _, _, _, slots = _bambu_view(r.tiles[row["name"] + ".3mf"])
        assert slots == {sl["name"]: sl["slot"] for sl in row["slots"]}
    assert "0.2 mm layer height" in r.readme


# --- Open in Bambu Studio -------------------------------------------------------------------

def test_find_bambu_studio_from_configured_path(tmp_path, monkeypatch):
    from pixeltile import bambu_studio as bs
    monkeypatch.delenv("PIXELTILE_BAMBU_STUDIO", raising=False)
    monkeypatch.setattr(bs.sys, "platform", "linux")
    monkeypatch.setattr(bs.shutil, "which", lambda name: None)  # nothing installed system-wide
    exe = tmp_path / "bambu-studio"
    exe.write_text("")
    assert bs.find_bambu_studio(str(exe)).command == [str(exe)]
    app = tmp_path / "BambuStudio.app"
    app.mkdir()
    assert bs.find_bambu_studio(str(app)).command == ["open", "-a", str(app)]
    other = tmp_path / "notepad.exe"  # never launch something that isn't Bambu Studio
    other.write_text("")
    assert bs.find_bambu_studio(str(other)) is None
    assert bs.find_bambu_studio(str(tmp_path / "missing" / "bambu-studio")) is None


def test_bambu_registry_command_parsing():
    from pixeltile.bambu_studio import _exe_from_command
    assert _exe_from_command('"C:\\Program Files\\Bambu Studio\\bambu-studio.exe" "%1"') == \
        "C:\\Program Files\\Bambu Studio\\bambu-studio.exe"
    assert _exe_from_command("C:\\Bambu\\bambu-studio.exe %1") == "C:\\Bambu\\bambu-studio.exe"
    assert _exe_from_command('""') is None


def test_server_opens_tile_in_bambu_studio(server, tmp_path, monkeypatch):
    import pixeltile.webapp as webapp
    exe = tmp_path / "bambu-studio"
    exe.write_text("")
    launched = []
    monkeypatch.setattr(webapp, "open_in_bambu_studio", lambda launcher, files: launched.append((launcher, files)))
    q = urllib.parse.quote(str(exe))
    status = json.loads(_req(f"{server}/api/bambu?path={q}")[1])
    assert status == {"found": True, "label": str(exe)}
    img = sample_artwork_png("grid")
    code, body, _ = _req(f"{server}/api/open_in_bambu?file=A1.3mf&bambu_path={q}", img, method="POST")
    assert code == 200, body
    data = json.loads(body)
    (launcher, files), = launched
    assert launcher.command == [str(exe)] and files == [Path(data["file"])]
    assert Path(data["file"]).parent == tmp_path / "Open in Bambu Studio"
    assert Path(data["file"]).read_bytes()[:2] == b"PK"


def test_server_reports_missing_bambu_studio(server, monkeypatch):
    import pixeltile.webapp as webapp
    monkeypatch.setattr(webapp, "find_bambu_studio", lambda configured=None: None)
    assert json.loads(_req(server + "/api/bambu")[1]) == {"found": False, "label": ""}
    code, body, _ = _req(server + "/api/open_in_bambu?file=A1.3mf", sample_artwork_png("grid"), method="POST")
    assert code == 404 and b"not found" in body
