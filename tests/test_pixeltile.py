import io
import json
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter

import numpy as np
import pytest
from PIL import Image

from pixeltile.bambu3mf import write_3mf
from pixeltile.geometry import Part, merge_rects, tile_parts
from pixeltile.palette import BAMBU_PLA, auto_pick, palette_labs, parse_palette, rgb_to_lab
from pixeltile.pipeline import Settings, run, sample_artwork_png
from pixeltile.process import grid_size, quantize
from pixeltile.tiling import Tile, limit_colors, schedule_tiles, tile_name

NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}


def sample_png(w=60, h=40, seed=1) -> bytes:
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    b = io.BytesIO()
    Image.fromarray(arr).save(b, "PNG")
    return b.getvalue()


def test_palette_size_and_exact_match():
    assert len(BAMBU_PLA) >= 48
    pal = np.array([[255, 0, 0], [0, 255, 0]])
    out = quantize(np.array([[[250, 5, 5], [3, 240, 4]]], dtype=np.uint8), pal)
    assert out.tolist() == [[0, 1]]


def test_custom_palette_names_and_hex():
    p = parse_palette("Black, #ff0000")
    assert p[0][1] == "#000000" and p[1][1] == "#FF0000"
    with pytest.raises(ValueError):
        parse_palette("NotAColor")


def test_auto_pick_returns_requested_distinct():
    rgb = np.asarray(Image.open(io.BytesIO(sample_png())).convert("RGB"))
    picks = auto_pick(rgb, 6, BAMBU_PLA)
    assert len(picks) == 6 and len({h for _, h in picks}) == 6


def test_limit_colors_invariant():
    rng = np.random.default_rng(0)
    idx = rng.integers(0, 20, (30, 30))
    lab = palette_labs(BAMBU_PLA[:20])
    for k in (1, 3, 7):
        assert len(np.unique(limit_colors(idx, lab, k))) <= k


def test_merge_rects_covers_exactly():
    rng = np.random.default_rng(3)
    mask = rng.random((25, 31)) > 0.5
    cover = np.zeros_like(mask, dtype=int)
    for r0, c0, r1, c1 in merge_rects(mask):
        cover[r0:r1, c0:c1] += 1
    assert (cover == mask.astype(int)).all()  # exact, no overlap


def test_grid_uniform_and_limits():
    cols, rows, tx, ty = grid_size(1000, 500, 500, 2.0, 200)
    assert cols % tx == 0 and rows % ty == 0
    assert cols // tx * 2.0 <= 200 and rows // ty * 2.0 <= 200
    assert tx == 3 and ty == 2 or tx >= 3


def test_tile_names():
    assert tile_name(0, 0) == "A1" and tile_name(26, 2) == "AA3"


def _open(b):
    z = zipfile.ZipFile(io.BytesIO(b))
    return z, ET.fromstring(z.read("3D/3dmodel.model"))


def test_3mf_structure_and_manifold_boxes():
    idx = np.array([[0, 0, 1], [0, 2, 1]])
    pal = [("A", "#FF0000"), ("B", "#00FF00"), ("C", "#0000FF")]
    parts = tile_parts(idx, pal, 2.0, 1.2, 1.6, ("Black", "#000000"))
    slots = ["#000000", "#FF0000", "#00FF00", "#0000FF"] + ["#FFFFFF"] * 4
    z, root = _open(write_3mf(parts, "t", slots))
    assert set(z.namelist()) >= {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model",
                                 "Metadata/model_settings.config", "Metadata/project_settings.config"}
    proj = json.loads(z.read("Metadata/project_settings.config"))
    assert len(proj["filament_colour"]) == 8 and len(proj["filament_type"]) == 8
    ms = ET.fromstring(z.read("Metadata/model_settings.config"))
    ext = sorted(int(m.get("value")) for m in ms.iter("metadata") if m.get("key") == "extruder"
                 and m.get("value") != "1" or False)
    part_ext = [int(p.find("metadata[@key='extruder']").get("value")) for p in ms.iter("part")]
    assert part_ext == [1, 2, 3, 4] and all(1 <= e <= 8 for e in part_ext)
    # Each box = own 8 verts / 12 tris, every edge shared by exactly 2 triangles.
    for obj in root.findall(".//m:object", NS):
        mesh = obj.find("m:mesh", NS)
        if mesh is None:
            continue
        nv = len(mesh.findall(".//m:vertex", NS))
        tris = [(int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))) for t in mesh.findall(".//m:triangle", NS)]
        assert nv % 8 == 0 and len(tris) == nv // 8 * 12
        edges = Counter()
        for a, b, c in tris:
            for e in ((a, b), (b, c), (c, a)):
                edges[tuple(sorted(e))] += 1
        assert set(edges.values()) == {2}


def test_3mf_volume_matches_pixels():
    idx = np.zeros((4, 5), dtype=int)
    parts = tile_parts(idx, [("A", "#FF0000")], 2.0, 1.2, 1.6, ("Black", "#000000"))
    vol = sum((b[3] - b[0]) * (b[4] - b[1]) * (b[5] - b[2]) for p in parts for b in p.boxes)
    assert vol == pytest.approx(10 * 8 * 1.2 + 10 * 8 * 1.6)


def test_extruder_range_enforced():
    with pytest.raises(ValueError):
        write_3mf([Part("x", "#FFFFFF", 9, [(0, 0, 0, 1, 1, 1)])], "t", ["#FFFFFF"] * 8)


def test_pipeline_multi_tile_default_h3c():
    s = Settings(width_mm=100, pixel_mm=2.0, max_tile_size_mm=50, auto_colors=12)
    r = run(sample_png(), s)
    assert len(r.tiles) == r.grid[0] * r.grid[1] > 1
    assert r.tile_size_mm[0] <= 50
    for b in r.tiles.values():
        z, root = _open(b)
        ms = ET.fromstring(z.read("Metadata/model_settings.config"))
        exts = {int(p.find("metadata[@key='extruder']").get("value")) for p in ms.iter("part")}
        assert max(exts) <= 8 and 1 in exts
    zipfile.ZipFile(io.BytesIO(r.zip_bytes())).testzip()


def test_pipeline_four_slot_ams():
    r = run(sample_png(), Settings(width_mm=60, max_tile_colors=4, auto_colors=10))
    for b in r.tiles.values():
        z, _ = _open(b)
        assert len(json.loads(z.read("Metadata/project_settings.config"))["filament_colour"]) == 4
        ms = ET.fromstring(z.read("Metadata/model_settings.config"))
        assert len(list(ms.iter("part"))) <= 4


def test_validation():
    with pytest.raises(ValueError):
        run(sample_png(), Settings(max_tile_size_mm=301))


def test_dither_runs():
    r = run(sample_png(20, 20), Settings(width_mm=40, dither=True, auto_colors=6))
    assert r.tiles


def _assert_manifold_3mf(raw: bytes) -> None:
    _, root = _open(raw)
    meshes = [obj.find("m:mesh", NS) for obj in root.findall(".//m:object", NS)]
    meshes = [m for m in meshes if m is not None]
    assert meshes
    for mesh in meshes:
        nv = len(mesh.findall(".//m:vertex", NS))
        tris = [(int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))) for t in mesh.findall(".//m:triangle", NS)]
        assert nv % 8 == 0 and len(tris) == nv // 8 * 12
        edges = Counter()
        for a, b, c in tris:
            for e in ((a, b), (b, c), (c, a)):
                edges[tuple(sorted(e))] += 1
        assert set(edges.values()) == {2}


def test_schedule_tiles_preserves_slots_and_reduces_swaps():
    t0 = Tile("A1", 0, 0, np.array([[0, 1, 2], [3, 4, 4]]))
    t1 = Tile("A2", 0, 1, np.array([[5, 6, 7], [8, 8, 8]]))
    t2 = Tile("B1", 1, 0, np.array([[0, 1, 2], [3, 9, 9]]))
    ordered = schedule_tiles([t0, t1, t2], max_colors=7)
    assert ordered[0].name == "A1"
    assert ordered[1].name == "B1"
    for shared_cid in (0, 1, 2, 3):
        assert ordered[0].slot_map[shared_cid] == ordered[1].slot_map[shared_cid]


def test_connector_keys_manifold_and_connectors_3mf():
    s = Settings(width_mm=100, pixel_mm=2.0, max_tile_size_mm=50, auto_colors=8, connector_keys=True)
    r = run(sample_png(80, 80), s)
    assert r.grid == (2, 2)
    assert r.connectors_3mf
    _assert_manifold_3mf(r.connectors_3mf)
    for tile_bytes in r.tiles.values():
        _assert_manifold_3mf(tile_bytes)
    # Pocketed backing should contain multiple boxes on the backing part (lower solid + upper pocketed).
    z, root = _open(next(iter(r.tiles.values())))
    backing_mesh = root.findall(".//m:object", NS)[0].find("m:mesh", NS)
    assert len(backing_mesh.findall(".//m:vertex", NS)) > 8


def test_swatch_3mf_generated():
    r = run(sample_png(40, 40), Settings(width_mm=60, auto_colors=6))
    assert r.swatch_3mf
    _assert_manifold_3mf(r.swatch_3mf)
    with zipfile.ZipFile(io.BytesIO(r.zip_bytes())) as z:
        assert "swatch.3mf" in z.namelist()
        assert z.testzip() is None


def test_wall_mount_rim_and_keyholes_manifold():
    s = Settings(width_mm=100, pixel_mm=2.0, max_tile_size_mm=50, auto_colors=8, wall_mount=True)
    r = run(sample_png(80, 80), s)
    assert r.grid == (2, 2)
    assert r.connectors_3mf
    _assert_manifold_3mf(r.connectors_3mf)
    for tile_bytes in r.tiles.values():
        _assert_manifold_3mf(tile_bytes)
    _, root = _open(r.tiles["A1.3mf"])
    backing_mesh = root.findall(".//m:object", NS)[0].find("m:mesh", NS)
    zs = [float(v.get("z")) for v in backing_mesh.findall(".//m:vertex", NS)]
    assert max(zs) == pytest.approx(7.7)
    assert "Top-row screw keyhole centers" in r.readme
    assert "(A1)" in r.readme and "(A2)" in r.readme


def test_sample_artworks():
    for name in ("wave", "starry", "fuji", "grid"):
        raw = sample_artwork_png(name)
        im = Image.open(io.BytesIO(raw))
        assert im.format == "PNG" and im.size == (240, 160)
    with pytest.raises(ValueError):
        sample_artwork_png("unknown")


def test_bambu_palette_unique_names():
    names = [n for n, _ in BAMBU_PLA]
    assert len(names) == len(set(names)) == 51


def test_parse_palette_blocks_file_when_disallowed(tmp_path):
    p = tmp_path / "pal.txt"
    p.write_text("#FF0000\n", encoding="utf-8")
    assert parse_palette(str(p), allow_file=True) == [("#FF0000", "#FF0000")]
    with pytest.raises(ValueError):
        parse_palette(str(p), allow_file=False)


def test_coarse_pocket_fits_connector_key():
    from pixeltile.geometry import _cut_edge_pockets

    mask = np.ones((20, 20), dtype=bool)
    _cut_edge_pockets(mask, 3.0, (True, False, False, False))
    removed_cols = int((~mask[0, :]).sum())
    assert removed_cols * 3.0 >= 16.0


def test_desktop_start_server_ephemeral_port():
    import urllib.request
    from pixeltile.desktop import start_server

    srv, port = start_server()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8")
            assert "PixelTile Studio" in html
    finally:
        srv.shutdown()
        srv.server_close()
