"""Lightweight local studio web UI (stdlib only): 3-zone workbench + Bambu 3MF export."""
from __future__ import annotations

import base64
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .palette import BAMBU_PLA
from .pipeline import Settings, run, sample_artwork_png

_FIELDS = {
    "width_mm": float,
    "pixel_mm": float,
    "max_tile_colors": int,
    "max_tile_size_mm": float,
    "auto_colors": int,
    "contrast": float,
    "saturation": float,
    "dither": lambda v: v == "1",
    "connector_keys": lambda v: v == "1",
    "wall_mount": lambda v: v == "1",
    "palette": str,
    "backing_color": str,
    "backing_mm": float,
    "color_depth_mm": float,
}

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PixelTile Studio — Bambu 3MF Generator</title>
<style>
  :root {
    --bg-app: #090a0f; --bg-panel: #12141c; --bg-elevated: #1c1f2b;
    --border: #2a2f42; --text-main: #f1f5f9; --text-muted: #94a3b8;
    --bambu-green: #00ae42; --bambu-green-hover: #00c84c;
    --accent-blue: #38bdf8; --err: #f87171; --radius: 8px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
    background: var(--bg-app); color: var(--text-main);
    height: 100vh; display: flex; flex-direction: column; overflow: hidden; font-size: 13px;
  }

  /* TOPBAR */
  .topbar {
    height: 54px; flex-shrink: 0; background: var(--bg-panel); border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between; padding: 0 18px; gap: 12px;
  }
  .brand { display: flex; align-items: center; gap: 10px; }
  .brand h1 { font-size: 15px; font-weight: 600; white-space: nowrap; }
  .brand-badge {
    background: var(--bambu-green); color: #fff; font-weight: 700; font-size: 11px;
    padding: 3px 7px; border-radius: 5px; letter-spacing: .03em;
  }
  .telemetry {
    background: var(--bg-elevated); border: 1px solid var(--border); padding: 5px 14px;
    border-radius: 99px; color: var(--text-muted); display: flex; gap: 8px; align-items: center;
    font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .telemetry strong { color: var(--text-main); }
  .topbar-actions { display: flex; gap: 8px; align-items: center; flex-shrink: 0; }
  .btn {
    border: 1px solid var(--border); padding: 7px 13px; border-radius: var(--radius);
    font-weight: 600; cursor: pointer; display: inline-flex; align-items: center; gap: 7px;
    font-size: 12.5px; color: var(--text-main); background: var(--bg-elevated);
  }
  .btn:hover { border-color: var(--accent-blue); }
  .btn-primary { background: var(--bambu-green); border-color: var(--bambu-green); color: #fff; }
  .btn-primary:hover { background: var(--bambu-green-hover); border-color: var(--bambu-green-hover); }
  .btn-primary:disabled { opacity: .45; cursor: not-allowed; }
  .swatch-stack { display: inline-flex; }
  .swatch-stack i { width: 11px; height: 11px; border-radius: 50%; border: 1px solid #000; margin-left: -4px; display: block; }
  .swatch-stack i:first-child { margin-left: 0; }

  /* WORKSPACE */
  .workspace { display: grid; grid-template-columns: 336px 1fr; flex: 1; min-height: 0; overflow: hidden; }

  /* SIDEBAR */
  .sidebar {
    background: var(--bg-panel); border-right: 1px solid var(--border);
    overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px;
  }
  .panel-group { background: var(--bg-elevated); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; flex-shrink: 0; }
  .group-header {
    padding: 9px 12px; background: rgba(255,255,255,0.02); border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 8px;
  }
  .group-header h2 { font-size: 12.5px; font-weight: 600; flex: 1; }
  .step-num {
    width: 19px; height: 19px; border-radius: 50%; background: var(--border);
    display: grid; place-items: center; font-size: 11px; font-weight: 700; flex-shrink: 0;
  }
  .group-body { padding: 11px 12px; display: flex; flex-direction: column; gap: 10px; }

  .dropzone {
    display: block; border: 1.5px dashed var(--border); border-radius: var(--radius); padding: 13px 10px;
    text-align: center; cursor: pointer; background: rgba(0,0,0,0.22); transition: border-color .15s, background .15s;
  }
  .dropzone:hover, .dropzone.drag { border-color: var(--accent-blue); background: rgba(56,189,248,0.08); }
  .dropzone-content { display: flex; flex-direction: column; gap: 3px; }
  .dropzone-content strong { font-size: 12.5px; color: var(--text-main); }
  .dropzone-content span { font-size: 11px; color: var(--text-muted); }

  .preset-row { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
  .preset-btn {
    border: 1px solid var(--border); background: var(--bg-panel); color: var(--text-muted);
    border-radius: 6px; padding: 4px 8px; font-size: 11.5px; font-weight: 500; cursor: pointer;
  }
  .preset-btn:hover { color: var(--text-main); border-color: var(--accent-blue); }
  .preset-btn.active { border-color: var(--bambu-green); color: var(--text-main); background: rgba(0,174,66,0.12); }

  .hardware-cards { display: grid; gap: 6px; }
  .hw-card {
    display: flex; justify-content: space-between; align-items: center; padding: 7px 10px;
    border: 1px solid var(--border); border-radius: 6px; cursor: pointer; background: var(--bg-panel);
  }
  .hw-card:hover { border-color: var(--accent-blue); }
  .hw-card.active { border-color: var(--bambu-green); background: rgba(0,174,66,0.1); }
  .hw-title { font-weight: 600; font-size: 12.5px; }
  .hw-meta { font-size: 11px; color: var(--text-muted); }

  .field-label { font-size: 11.5px; color: var(--text-muted); display: block; margin-bottom: 4px; }
  .label-split { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
  .value-badge { font-size: 11px; color: var(--accent-blue); font-weight: 600; }
  .slider-row { display: flex; align-items: center; gap: 8px; }
  .slider { flex: 1; accent-color: var(--bambu-green); }
  .num-sm {
    width: 66px; background: var(--bg-panel); color: var(--text-main); border: 1px solid var(--border);
    border-radius: 6px; padding: 4px 6px; font-size: 12px; text-align: right;
  }

  .segmented {
    display: grid; grid-template-columns: repeat(4, 1fr); background: var(--bg-panel);
    padding: 3px; border-radius: 6px; border: 1px solid var(--border); gap: 2px;
  }
  .segmented.pill { grid-template-columns: repeat(3, auto); border-radius: 99px; }
  .segmented button {
    background: transparent; border: none; color: var(--text-muted); padding: 5px 8px;
    font-size: 11.5px; font-weight: 600; border-radius: 4px; cursor: pointer; white-space: nowrap;
  }
  .segmented.pill button { border-radius: 99px; padding: 5px 12px; }
  .segmented button.active { background: var(--bg-elevated); color: var(--text-main); box-shadow: 0 1px 3px rgba(0,0,0,0.4); }

  .mount-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
  .mount-opt {
    border: 1px solid var(--border); background: var(--bg-panel); padding: 8px 4px; border-radius: 6px;
    text-align: center; cursor: pointer; display: flex; flex-direction: column; align-items: center; gap: 3px;
  }
  .mount-opt:hover { border-color: var(--accent-blue); }
  .mount-opt.active { border-color: var(--bambu-green); background: rgba(0,174,66,0.1); }
  .mount-opt svg { width: 34px; height: 14px; stroke: var(--text-muted); fill: none; stroke-width: 1.6; }
  .mount-opt.active svg { stroke: var(--bambu-green); }
  .mount-name { font-weight: 600; font-size: 11px; }
  .mount-desc { font-size: 10px; color: var(--text-muted); }

  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  input[type=text], input[type=number] {
    background: var(--bg-panel); color: var(--text-main); border: 1px solid var(--border);
    border-radius: 6px; padding: 6px 8px; font-size: 12px; width: 100%;
  }
  label.chk { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--text-main); cursor: pointer; }
  .advanced summary { cursor: pointer; color: var(--text-muted); font-size: 11.5px; user-select: none; }
  .advanced[open] summary { color: var(--text-main); margin-bottom: 8px; }
  #status_txt { font-size: 11.5px; color: var(--accent-blue); min-height: 15px; }
  #err { font-size: 11.5px; color: var(--err); min-height: 15px; }

  /* STAGE & DOCKED SCHEDULE */
  .stage { display: grid; grid-template-rows: 1fr 220px; min-height: 0; overflow: hidden; }
  .canvas-viewport {
    position: relative; display: flex; flex-direction: column; align-items: center; justify-content: center;
    background: radial-gradient(circle at center, #171b26 0%, #090a0f 100%);
    padding: 54px 20px 18px; overflow: hidden;
  }
  .canvas-viewport.drag { outline: 2px dashed var(--accent-blue); outline-offset: -8px; }
  .canvas-toolbar {
    position: absolute; top: 12px; left: 16px; right: 16px;
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
  }
  .canvas-hint { font-size: 11.5px; color: var(--text-muted); }
  .img-wrap {
    position: relative; display: none; max-width: 100%; max-height: 100%;
    border-radius: 6px; overflow: hidden; border: 1px solid var(--border);
  }
  .img-wrap.show { display: inline-block; }
  .img-wrap img { display: block; max-width: 100%; max-height: calc(100vh - 360px); image-rendering: pixelated; }
  .tile-overlay { position: absolute; inset: 0; display: grid; pointer-events: none; }
  .tile-hit {
    pointer-events: auto; cursor: pointer; border: 1px solid transparent; transition: background .12s, border-color .12s;
  }
  .tile-hit:hover { background: rgba(56,189,248,0.16); border-color: var(--accent-blue); }
  .tile-hit.hl { background: rgba(0,174,66,0.20); border-color: var(--bambu-green); }

  .guide-box {
    display: none; width: 100%; max-width: 760px; max-height: 100%; overflow: auto;
    background: var(--bg-panel); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 14px 16px; font: 12px/1.6 ui-monospace, Consolas, monospace; white-space: pre-wrap;
  }
  .guide-box.show { display: block; }
  .empty { text-align: center; color: var(--text-muted); max-width: 340px; }
  .empty strong { display: block; color: var(--text-main); font-size: 14px; margin-bottom: 5px; }

  /* DOCKED SCHEDULE */
  .schedule-dock { background: var(--bg-panel); border-top: 1px solid var(--border); display: flex; flex-direction: column; min-height: 0; }
  .dock-header {
    padding: 8px 16px; border-bottom: 1px solid var(--border);
    display: flex; justify-content: space-between; align-items: center; gap: 10px;
  }
  .dock-header h3 { font-size: 12.5px; font-weight: 600; }
  .dock-header span { font-size: 11px; color: var(--text-muted); margin-left: 8px; font-weight: 400; }
  .dock-actions { display: flex; gap: 6px; }
  .table-scroll { overflow-y: auto; flex: 1; }
  .schedule-table { width: 100%; border-collapse: collapse; text-align: left; }
  .schedule-table th {
    position: sticky; top: 0; background: var(--bg-elevated); padding: 7px 12px;
    font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border); z-index: 1;
  }
  .schedule-table td { padding: 7px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }
  .schedule-table tbody tr { cursor: pointer; }
  .schedule-table tbody tr:hover { background: rgba(255,255,255,0.03); }
  .schedule-table tbody tr.active-row { background: rgba(0,174,66,0.10); }
  .slot-chip {
    display: inline-flex; align-items: center; gap: 5px; background: var(--bg-elevated);
    border: 1px solid var(--border); padding: 2px 7px; margin: 1px 4px 1px 0; border-radius: 99px; font-size: 11px;
  }
  .slot-chip i { width: 9px; height: 9px; border-radius: 50%; border: 1px solid rgba(255,255,255,.25); display: block; }
  .btn-table {
    background: var(--bg-elevated); border: 1px solid var(--border); color: var(--text-main);
    padding: 4px 9px; border-radius: 5px; cursor: pointer; font-size: 11px; font-weight: 600;
  }
  .btn-table:hover { border-color: var(--bambu-green); color: var(--bambu-green); }

  /* SPOOLS MODAL */
  dialog {
    margin: auto; background: var(--bg-panel); color: var(--text-main);
    border: 1px solid var(--border); border-radius: 12px; padding: 18px; max-width: 620px; width: 92vw;
  }
  dialog::backdrop { background: rgba(0,0,0,.7); }
  .spool-groups { max-height: 310px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; margin: 10px 0; padding-right: 4px; }
  .spool-group-title { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: .05em; margin-bottom: 5px; }
  .spool-wrap { display: flex; flex-wrap: wrap; gap: 5px; }
  .chip {
    display: inline-flex; align-items: center; gap: 5px; padding: 4px 9px; border-radius: 99px;
    font-size: 11.5px; border: 1px solid var(--border); background: var(--bg-elevated);
    color: var(--text-muted); cursor: pointer; opacity: .42;
  }
  .chip.on { opacity: 1; color: var(--text-main); border-color: var(--bambu-green); background: rgba(0,174,66,0.12); }
  .dot { width: 9px; height: 9px; border-radius: 50%; border: 1px solid rgba(255,255,255,.25); flex-shrink: 0; }

  /* CUSTOM IMAGE EDITOR MODAL */
  #editor_dlg { max-width: 760px; width: 95vw; }
  .ed-bar { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 10px 0; }
  .ed-stage {
    background: var(--bg-app); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 12px; display: flex; justify-content: center; align-items: center; min-height: 280px;
  }
  #ed_canvas {
    image-rendering: pixelated; border: 1px solid var(--border); cursor: crosshair;
    max-width: 100%; max-height: 380px; width: auto; height: auto; touch-action: none;
    background: #fff;
  }
  .ed-pal { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 10px; align-items: center; }
  .ed-sw {
    width: 20px; height: 20px; border-radius: 50%; border: 2px solid transparent; cursor: pointer;
    box-shadow: inset 0 0 0 1px rgba(255,255,255,.25);
  }
  .ed-sw.active { border-color: var(--accent-blue); transform: scale(1.12); }
</style>
</head>
<body>

<header class="topbar">
  <div class="brand">
    <span class="brand-badge">3MF</span>
    <h1>PixelTile Studio</h1>
  </div>
  <div class="telemetry" id="telemetry">
    <span>Load an image or demo artwork to configure your mural</span>
  </div>
  <div class="topbar-actions">
    <button type="button" class="btn" id="open_spools">
      <span class="swatch-stack">
        <i style="background:#C12E1F"></i>
        <i style="background:#0086D6"></i>
        <i style="background:#F4EE2A"></i>
      </span>
      Owned Spools (<span id="spcount">__N__</span>)
    </button>
    <button type="button" class="btn btn-primary" id="dl" disabled>Download All Tiles (.zip)</button>
  </div>
</header>

<div class="workspace">
  <aside class="sidebar">
    <!-- 1. SOURCE -->
    <section class="panel-group">
      <header class="group-header"><span class="step-num">1</span><h2>Source Artwork</h2></header>
      <div class="group-body">
        <label class="dropzone" id="dropzone">
          <input type="file" id="f" accept="image/*" hidden>
          <div class="dropzone-content">
            <strong id="drop_label">Drop image or click to browse</strong>
            <span>PNG, JPG, WebP • Printed face-down</span>
          </div>
        </label>
        <div class="preset-row" id="demos">
          <span style="font-size:11.5px;color:var(--text-muted)">Demos:</span>
          <button type="button" class="preset-btn" data-sample="wave">Great Wave</button>
          <button type="button" class="preset-btn" data-sample="starry">Starry Night</button>
          <button type="button" class="preset-btn" data-sample="fuji">Red Fuji</button>
          <button type="button" class="preset-btn" data-sample="grid">Color Chart</button>
        </div>
        <button type="button" class="btn" id="open_editor" style="justify-content:center;width:100%">Custom Image Tool (Draw / Edit)</button>
      </div>
    </section>

    <!-- 2. PRINTER & SCALE -->
    <section class="panel-group">
      <header class="group-header"><span class="step-num">2</span><h2>Printer &amp; Scale</h2></header>
      <div class="group-body">
        <div class="hardware-cards" id="presets">
          <div class="hw-card active" data-slots="8" data-tile="200">
            <span class="hw-title">H3C 8-Tool</span>
            <span class="hw-meta">8 Slots/Tile • 200 mm bed</span>
          </div>
          <div class="hw-card" data-slots="8" data-tile="300">
            <span class="hw-title">H3C 300mm</span>
            <span class="hw-meta">8 Slots/Tile • Fewer seams</span>
          </div>
          <div class="hw-card" data-slots="4" data-tile="200">
            <span class="hw-title">AMS 4-Slot</span>
            <span class="hw-meta">4 Slots/Tile • Standard AMS</span>
          </div>
        </div>

        <div>
          <div class="label-split">
            <span class="field-label" style="margin:0">Mural Width (mm)</span>
            <span class="value-badge" id="dim_live">200 mm</span>
          </div>
          <div class="slider-row">
            <input type="range" id="width_rng" class="slider" min="80" max="1200" step="10" value="200">
            <input type="number" id="width_mm" class="num-sm" value="200" min="10">
          </div>
        </div>

        <div>
          <span class="field-label">Pixel Detail</span>
          <div class="segmented" id="pixbtns">
            <button type="button" data-px="3">Coarse</button>
            <button type="button" data-px="2" class="active">Medium</button>
            <button type="button" data-px="1.5">Fine</button>
            <button type="button" data-px="1">Ultra</button>
          </div>
        </div>
      </div>
    </section>

    <!-- 3. PALETTE & BACKING -->
    <section class="panel-group">
      <header class="group-header"><span class="step-num">3</span><h2>Palette &amp; Backing</h2></header>
      <div class="group-body">
        <div>
          <div class="label-split">
            <span class="field-label" style="margin:0">Auto-Color Limit</span>
            <span class="value-badge" id="col_live">16 Colors (0 = All)</span>
          </div>
          <input type="range" id="auto_colors" class="slider" min="0" max="32" step="1" value="16">
        </div>
        <div class="grid2" style="align-items:end">
          <label class="fld" style="font-size:11.5px;color:var(--text-muted)">Backing (Slot 1)
            <input type="text" id="backing_color" value="Black" style="margin-top:4px">
          </label>
          <label class="chk" style="padding-bottom:6px"><input type="checkbox" id="dither"> Dithering</label>
        </div>
      </div>
    </section>

    <!-- 4. TILE ARCHITECTURE -->
    <section class="panel-group">
      <header class="group-header"><span class="step-num">4</span><h2>Tile Architecture</h2></header>
      <div class="group-body">
        <div class="mount-grid" id="mount_mode">
          <div class="mount-opt active" data-mount="flat">
            <svg viewBox="0 0 36 14"><rect x="2" y="4" width="32" height="6" rx="1"/></svg>
            <span class="mount-name">Flat Back</span>
            <span class="mount-desc">Tape / Glue</span>
          </div>
          <div class="mount-opt" data-mount="keys">
            <svg viewBox="0 0 36 14"><path d="M2 4 H14 V7 H22 V4 H34 V10 H2 Z"/></svg>
            <span class="mount-name">Connectors</span>
            <span class="mount-desc">Seam keys</span>
          </div>
          <div class="mount-opt" data-mount="wall">
            <svg viewBox="0 0 36 14"><path d="M2 2 H34 V12 H28 V6 H8 V12 H2 Z"/></svg>
            <span class="mount-name">Wall-Mount</span>
            <span class="mount-desc">7.7mm Keyhole</span>
          </div>
        </div>

        <details class="advanced">
          <summary>Advanced Slicer &amp; Geometry Tuning</summary>
          <div class="grid2" style="margin-top:6px">
            <label class="field-label">Pixel Size (mm)<input type="number" id="pixel_mm" value="2" step="0.5" min="0.5"></label>
            <label class="field-label">Max Tile (mm, &le;300)<input type="number" id="max_tile_size_mm" value="200" min="10" max="300"></label>
            <label class="field-label">Slots / Tile (2..9)<input type="number" id="max_tile_colors" value="8" min="2" max="9"></label>
            <label class="field-label">Color Depth (mm)<input type="number" id="color_depth_mm" value="1.2" step="0.2" min="0.2"></label>
            <label class="field-label">Backing (mm)<input type="number" id="backing_mm" value="1.6" step="0.2" min="0.4"></label>
            <label class="field-label">Contrast<input type="number" id="contrast" value="1" step="0.1"></label>
            <label class="field-label">Saturation<input type="number" id="saturation" value="1" step="0.1"></label>
          </div>
          <input type="checkbox" id="connector_keys" style="display:none">
          <input type="checkbox" id="wall_mount" style="display:none">
        </details>
      </div>
    </section>

    <div id="status_txt"></div>
    <div id="err"></div>
  </aside>

  <main class="stage">
    <div class="canvas-viewport" id="stage">
      <div class="canvas-toolbar">
        <div class="segmented pill">
          <button type="button" id="tab_map" class="active">Interactive Tile Map</button>
          <button type="button" id="tab_prev">Mosaic Preview</button>
          <button type="button" id="tab_guide">Print Guide</button>
        </div>
        <span class="canvas-hint" id="canvas_hint">Click any tile or queue row to inspect its tool schedule</span>
      </div>

      <div class="empty" id="ph">
        <strong>Start by picking an artwork</strong>
        Drop any image here or select a built-in demo on the left to generate colored Bambu .3mf tiles.
      </div>

      <div class="img-wrap" id="wrap_map">
        <img id="img_map" alt="Tile map">
        <div class="tile-overlay" id="tile_overlay"></div>
      </div>
      <div class="img-wrap" id="wrap_prev">
        <img id="img_prev" alt="Clean preview">
      </div>
      <pre class="guide-box" id="guide_box"></pre>
    </div>

    <section class="schedule-dock">
      <div class="dock-header">
        <div>
          <h3>Print Queue &amp; Tool Slot Schedule <span>Optimized to minimize spool swaps across tiles</span></h3>
        </div>
        <div class="dock-actions">
          <button type="button" class="btn-table" id="dl_swatch" style="display:none">⬇ Swatch .3mf</button>
          <button type="button" class="btn-table" id="dl_conn" style="display:none">⬇ Connectors .3mf</button>
        </div>
      </div>
      <div class="table-scroll">
        <table class="schedule-table">
          <thead><tr><th>#</th><th>Tile</th><th>Swaps</th><th>Tool Slots (T1 = Backing)</th><th>Export</th></tr></thead>
          <tbody id="sched"><tr><td colspan="5" style="color:var(--text-muted)">Load an artwork to compute the print schedule.</td></tr></tbody>
        </table>
      </div>
    </section>
  </main>
</div>

<dialog id="spool_dlg">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
    <strong>Owned Bambu PLA Spools</strong>
    <div class="preset-row">
      <button type="button" class="preset-btn" id="spall">Select All</button>
      <button type="button" class="preset-btn" id="spnone">Clear All</button>
      <button type="button" class="preset-btn active" id="close_spools">Done</button>
    </div>
  </div>
  <input type="text" id="sp_filter" placeholder="Search filament name (e.g. Jade, Charcoal, Blue)..." style="margin-top:10px">
  <div class="spool-groups" id="spools"></div>
  <label class="field-label">Custom Hex / Extra Spools
    <input type="text" id="custom_pal" placeholder="#FF5500, #123456 (optional)" style="margin-top:4px">
  </label>
</dialog>

<dialog id="editor_dlg">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
    <strong>Custom Image Tool</strong>
    <div class="preset-row">
      <select id="ed_size" style="background:var(--bg-elevated);color:var(--text-main);border:1px solid var(--border);border-radius:6px;padding:4px 8px;font-size:12px">
        <option value="32x32">32 × 32 (Square)</option>
        <option value="48x32" selected>48 × 32 (3:2)</option>
        <option value="64x48">64 × 48 (4:3)</option>
        <option value="96x64">96 × 64 (Detailed)</option>
      </select>
      <button type="button" class="preset-btn" id="ed_cancel">Cancel</button>
      <button type="button" class="btn btn-primary" id="ed_apply" style="padding:5px 12px;font-size:12px">Apply to Studio</button>
    </div>
  </div>
  <div class="ed-bar">
    <div class="preset-row" id="ed_tools">
      <button type="button" class="preset-btn active" data-tool="pen">Pencil</button>
      <button type="button" class="preset-btn" data-tool="fill">Fill</button>
      <button type="button" class="preset-btn" data-tool="pick">Eyedropper</button>
      <button type="button" class="preset-btn" data-tool="erase">Eraser</button>
    </div>
    <div class="preset-row" id="ed_sizes">
      <button type="button" class="preset-btn active" data-sz="1">1px</button>
      <button type="button" class="preset-btn" data-sz="2">2px</button>
      <button type="button" class="preset-btn" data-sz="4">4px</button>
    </div>
    <div class="preset-row">
      <button type="button" class="preset-btn" id="ed_undo">Undo</button>
      <button type="button" class="preset-btn" id="ed_rot">Rotate 90°</button>
      <button type="button" class="preset-btn" id="ed_flip">Flip H</button>
      <button type="button" class="preset-btn" id="ed_clear">Clear</button>
    </div>
  </div>
  <div class="ed-stage">
    <canvas id="ed_canvas" width="48" height="32"></canvas>
  </div>
  <div class="ed-pal" id="ed_pal">
    <input type="color" id="ed_color" value="#00AE42" title="Custom color" style="width:28px;height:24px;border:none;background:none;cursor:pointer">
  </div>
</dialog>

<script>
const FIELDS = __IDS__;
let allSpools = [];
let activeSpools = new Set();
let activeTab = 'map';
let activeFile = null;
let selectedTile = null;
let timer = null;

function hueGroup(hex) {
  const r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
  const mx = Math.max(r,g,b), mn = Math.min(r,g,b);
  if (mx - mn < 32) return 'Neutrals & Monochrome';
  if (r >= g && r >= b) return 'Warm Tones (Red / Orange / Yellow / Brown)';
  if (g >= r && g >= b) return 'Greens & Mint';
  return 'Blues & Purples';
}

async function init() {
  const r = await fetch('/api/palette');
  allSpools = await r.json();
  allSpools.forEach(s => activeSpools.add(s.name));
  renderSpools();
}

function renderSpools() {
  const q = (sp_filter.value || '').toLowerCase();
  const groups = {
    'Neutrals & Monochrome': [],
    'Warm Tones (Red / Orange / Yellow / Brown)': [],
    'Greens & Mint': [],
    'Blues & Purples': [],
  };
  for (const s of allSpools) {
    if (q && !s.name.toLowerCase().includes(q)) continue;
    groups[hueGroup(s.hex)].push(s);
  }
  const box = document.getElementById('spools');
  box.innerHTML = '';
  for (const [title, items] of Object.entries(groups)) {
    if (!items.length) continue;
    const sec = document.createElement('div');
    const h = document.createElement('div');
    h.className = 'spool-group-title';
    h.textContent = title;
    const wrap = document.createElement('div');
    wrap.className = 'spool-wrap';
    for (const s of items) {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = 'chip' + (activeSpools.has(s.name) ? ' on' : '');
      el.innerHTML = `<span class="dot" style="background:${s.hex}"></span><span>${s.name}</span>`;
      el.onclick = () => {
        if (activeSpools.has(s.name)) activeSpools.delete(s.name);
        else activeSpools.add(s.name);
        renderSpools();
        scheduleInspect();
      };
      wrap.appendChild(el);
    }
    sec.appendChild(h);
    sec.appendChild(wrap);
    box.appendChild(sec);
  }
  spcount.textContent = `${activeSpools.size}/${allSpools.length}`;
}

sp_filter.oninput = renderSpools;
open_spools.onclick = () => spool_dlg.showModal();
close_spools.onclick = () => { spool_dlg.close(); scheduleInspect(); };
spall.onclick = () => { allSpools.forEach(s => activeSpools.add(s.name)); renderSpools(); scheduleInspect(); };
spnone.onclick = () => { activeSpools.clear(); renderSpools(); };

function setFile(blob, label) {
  activeFile = blob;
  drop_label.textContent = `Loaded: ${label}`;
  dl.disabled = false;
  runInspect();
}

function wireDrop(el) {
  el.ondragover = e => { e.preventDefault(); el.classList.add('drag'); };
  el.ondragleave = () => el.classList.remove('drag');
  el.ondrop = e => {
    e.preventDefault(); el.classList.remove('drag');
    const file = e.dataTransfer.files[0];
    if (file) {
      document.querySelectorAll('#demos button').forEach(x => x.classList.remove('active'));
      setFile(file, file.name);
    }
  };
}
wireDrop(dropzone);
wireDrop(stage);

f.onchange = () => {
  if (f.files[0]) {
    document.querySelectorAll('#demos button').forEach(x => x.classList.remove('active'));
    setFile(f.files[0], f.files[0].name);
  }
};

document.querySelectorAll('#demos button').forEach(b => b.onclick = async () => {
  document.querySelectorAll('#demos button').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  status_txt.textContent = 'Loading demo artwork...';
  const r = await fetch('/api/sample?name=' + encodeURIComponent(b.dataset.sample));
  if (!r.ok) { err.textContent = await r.text(); status_txt.textContent = ''; return; }
  setFile(await r.blob(), b.textContent);
});

document.querySelectorAll('#presets .hw-card').forEach(c => c.onclick = () => {
  document.querySelectorAll('#presets .hw-card').forEach(x => x.classList.remove('active'));
  c.classList.add('active');
  max_tile_colors.value = c.dataset.slots;
  max_tile_size_mm.value = c.dataset.tile;
  scheduleInspect();
});

document.querySelectorAll('#pixbtns button').forEach(b => b.onclick = () => {
  document.querySelectorAll('#pixbtns button').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  pixel_mm.value = b.dataset.px;
  scheduleInspect();
});

document.querySelectorAll('#mount_mode .mount-opt').forEach(opt => opt.onclick = () => {
  document.querySelectorAll('#mount_mode .mount-opt').forEach(x => x.classList.remove('active'));
  opt.classList.add('active');
  const m = opt.dataset.mount;
  connector_keys.checked = (m === 'keys' || m === 'wall');
  wall_mount.checked = (m === 'wall');
  scheduleInspect();
});

width_rng.oninput = () => { width_mm.value = width_rng.value; dim_live.textContent = `${width_mm.value} mm`; scheduleInspect(); };
width_mm.oninput = () => { width_rng.value = Math.min(1200, Math.max(80, Number(width_mm.value) || 200)); dim_live.textContent = `${width_mm.value} mm`; scheduleInspect(); };
auto_colors.oninput = () => {
  col_live.textContent = auto_colors.value === '0' ? 'All Owned Spools' : `${auto_colors.value} Colors`;
  scheduleInspect();
};
[pixel_mm, max_tile_size_mm, max_tile_colors, color_depth_mm, backing_mm, contrast, saturation, backing_color, dither, custom_pal]
  .forEach(el => el.addEventListener('change', scheduleInspect));

function showTab(tab) {
  activeTab = tab;
  tab_map.classList.toggle('active', tab === 'map');
  tab_prev.classList.toggle('active', tab === 'prev');
  tab_guide.classList.toggle('active', tab === 'guide');
  if (img_map.src) wrap_map.classList.toggle('show', tab === 'map');
  if (img_prev.src) wrap_prev.classList.toggle('show', tab === 'prev');
  guide_box.classList.toggle('show', tab === 'guide' && !!guide_box.textContent);
}
tab_map.onclick = () => showTab('map');
tab_prev.onclick = () => showTab('prev');
tab_guide.onclick = () => showTab('guide');

function selectTile(name) {
  selectedTile = name;
  document.querySelectorAll('.tile-hit').forEach(el => el.classList.toggle('hl', el.dataset.tile === name));
  document.querySelectorAll('#sched tr[data-tile]').forEach(tr => tr.classList.toggle('active-row', tr.dataset.tile === name));
}

function scheduleInspect() {
  if (!activeFile) return;
  clearTimeout(timer);
  timer = setTimeout(runInspect, 160);
}

function buildQuery() {
  const p = new URLSearchParams();
  for (const id of FIELDS) {
    if (id === 'palette') continue;
    const e = document.getElementById(id);
    if (!e) continue;
    p.set(id, e.type === 'checkbox' ? (e.checked ? '1' : '0') : e.value);
  }
  const extra = custom_pal.value.trim();
  if (allSpools.length > 0 && activeSpools.size === 0 && !extra) {
    throw new Error('Select at least one spool or enter a custom hex color.');
  }
  if (allSpools.length > 0 && (activeSpools.size < allSpools.length || extra)) {
    const tokens = Array.from(activeSpools);
    if (extra) tokens.push(...extra.split(',').map(x => x.trim()).filter(Boolean));
    p.set('palette', tokens.join(','));
  }
  return p;
}

async function runInspect() {
  if (!activeFile) return;
  let q;
  try { q = buildQuery(); } catch (e) { err.textContent = e.message; return; }
  status_txt.textContent = 'Updating preview & schedule...';
  const r = await fetch('/api/inspect?' + q.toString(), { method: 'POST', body: activeFile });
  status_txt.textContent = '';
  if (!r.ok) { err.textContent = await r.text(); return; }
  const data = await r.json();
  err.textContent = '';
  ph.style.display = 'none';
  img_map.src = data.tile_map_b64;
  img_prev.src = data.preview_b64;
  guide_box.textContent = data.readme;
  showTab(activeTab);

  const [tx, ty] = data.grid;
  const totalSwaps = data.schedule.reduce((a, b) => a + b.swaps_from_prev, 0);
  const totalW = Math.round(tx * data.tile_size_mm[0]);
  const totalH = Math.round(ty * data.tile_size_mm[1]);
  dim_live.textContent = `${totalW} × ${totalH} mm (${tx}×${ty})`;
  telemetry.innerHTML =
    `<span><strong>${totalW} × ${totalH} mm</strong></span><span>•</span>` +
    `<span><strong>${tx}×${ty}</strong> (${tx * ty} ${tx * ty === 1 ? 'Tile' : 'Tiles'})</span><span>•</span>` +
    `<span><strong>${totalSwaps}</strong> swaps</span>`;

  // Interactive grid overlay matching row/col order on Tile Map
  tile_overlay.style.gridTemplateColumns = `repeat(${tx}, 1fr)`;
  tile_overlay.style.gridTemplateRows = `repeat(${ty}, 1fr)`;
  const byPos = {};
  data.schedule.forEach(s => { byPos[`${s.row},${s.col}`] = s.name; });
  let hits = '';
  for (let rr = 0; rr < ty; rr++) {
    for (let cc = 0; cc < tx; cc++) {
      const nm = byPos[`${rr},${cc}`] || '';
      hits += `<div class="tile-hit" data-tile="${nm}" title="Tile ${nm} — click to highlight"></div>`;
    }
  }
  tile_overlay.innerHTML = hits;
  tile_overlay.querySelectorAll('.tile-hit').forEach(el => {
    el.onclick = () => selectTile(el.dataset.tile);
  });

  dl_swatch.style.display = data.has_swatch ? 'inline-flex' : 'none';
  dl_conn.style.display = data.has_connectors ? 'inline-flex' : 'none';
  sched.innerHTML = data.schedule.map((row, idx) => {
    const badges = row.slots.map(s =>
      `<span class="slot-chip"><i style="background:${s.hex}"></i>T${s.slot} ${s.name}</span>`
    ).join('');
    const sw = idx === 0 ? 'Initial' : `${row.swaps_from_prev}`;
    const fname = `${row.name}.3mf`;
    return `<tr data-tile="${row.name}"><td>${idx + 1}</td><td><strong>${row.name}</strong></td><td>${sw}</td><td>${badges}</td>` +
           `<td><button type="button" class="btn-table" onclick="event.stopPropagation();downloadFile('${fname}')">⬇ ${fname}</button></td></tr>`;
  }).join('');
  sched.querySelectorAll('tr[data-tile]').forEach(tr => {
    tr.onclick = () => selectTile(tr.dataset.tile);
  });
  if (data.schedule.length) {
    const first = selectedTile && data.schedule.some(s => s.name === selectedTile) ? selectedTile : data.schedule[0].name;
    selectTile(first);
  }
}

async function saveBlob(filename, blob) {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.save_bytes) {
    const buf = await blob.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    const res = await window.pywebview.api.save_bytes(filename, btoa(bin));
    if (res && res.saved) status_txt.textContent = `Saved ${res.path}`;
    return;
  }
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
}

async function downloadFile(filename) {
  if (!activeFile) return;
  let q;
  try { q = buildQuery(); } catch (e) { err.textContent = e.message; return; }
  q.set('file', filename);
  status_txt.textContent = `Building ${filename}...`;
  const r = await fetch('/api/file?' + q.toString(), { method: 'POST', body: activeFile });
  status_txt.textContent = '';
  if (!r.ok) { err.textContent = await r.text(); return; }
  await saveBlob(filename, await r.blob());
}

dl.onclick = async () => {
  if (!activeFile) return;
  let q;
  try { q = buildQuery(); } catch (e) { err.textContent = e.message; return; }
  status_txt.textContent = 'Building .3mf print bundle...';
  const r = await fetch('/api/export?' + q.toString(), { method: 'POST', body: activeFile });
  status_txt.textContent = '';
  if (!r.ok) { err.textContent = await r.text(); return; }
  await saveBlob('pixeltile.zip', await r.blob());
};

dl_swatch.onclick = () => downloadFile('swatch.3mf');
dl_conn.onclick = () => downloadFile('connectors.3mf');

// --- Custom Image Tool ---
const edCtx = ed_canvas.getContext('2d', { willReadFrequently: true });
let edTool = 'pen', edBrush = 1, edDrawing = false, edLast = null, edUndoStack = [];

function edPushUndo() {
  edUndoStack.push(edCtx.getImageData(0, 0, ed_canvas.width, ed_canvas.height));
  if (edUndoStack.length > 25) edUndoStack.shift();
}

function edFillWhite(w, h) {
  ed_canvas.width = w; ed_canvas.height = h;
  edCtx.fillStyle = '#FFFFFF';
  edCtx.fillRect(0, 0, w, h);
  edUndoStack = [];
}

function edSyncDisplaySize() {
  const aspect = ed_canvas.width / ed_canvas.height;
  if (aspect >= 1) {
    ed_canvas.style.width = '520px';
    ed_canvas.style.height = Math.round(520 / aspect) + 'px';
  } else {
    ed_canvas.style.height = '360px';
    ed_canvas.style.width = Math.round(360 * aspect) + 'px';
  }
}

function edInitSwatches() {
  const picks = ['#000000','#FFFFFF','#8E9089','#C12E1F','#FF6A13','#F4EE2A','#00AE42','#0086D6','#0A2989','#5E43B7','#EC008C','#9D432C','#73B2E6','#A3D8C1','#E4BD68','#F55A74'];
  ed_pal.querySelectorAll('.ed-sw').forEach(e => e.remove());
  for (const hex of picks) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ed-sw' + (ed_color.value.toUpperCase() === hex ? ' active' : '');
    b.style.background = hex;
    b.title = hex;
    b.onclick = () => {
      ed_color.value = hex;
      ed_pal.querySelectorAll('.ed-sw').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
    };
    ed_pal.appendChild(b);
  }
}
ed_color.oninput = () => ed_pal.querySelectorAll('.ed-sw').forEach(x => x.classList.remove('active'));

open_editor.onclick = () => {
  edInitSwatches();
  if (activeFile) {
    const img = new Image();
    const url = URL.createObjectURL(activeFile);
    img.onload = () => {
      const scale = Math.min(1, 120 / Math.max(img.width, img.height));
      const w = Math.max(8, Math.round(img.width * scale));
      const h = Math.max(8, Math.round(img.height * scale));
      edFillWhite(w, h);
      edCtx.imageSmoothingEnabled = false;
      edCtx.drawImage(img, 0, 0, w, h);
      URL.revokeObjectURL(url);
      edSyncDisplaySize();
      editor_dlg.showModal();
    };
    img.src = url;
  } else {
    const [w, h] = ed_size.value.split('x').map(Number);
    edFillWhite(w, h);
    edSyncDisplaySize();
    editor_dlg.showModal();
  }
};

ed_size.onchange = () => {
  const [w, h] = ed_size.value.split('x').map(Number);
  edFillWhite(w, h);
  edSyncDisplaySize();
};
ed_cancel.onclick = () => editor_dlg.close();

document.querySelectorAll('#ed_tools button').forEach(b => b.onclick = () => {
  document.querySelectorAll('#ed_tools button').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  edTool = b.dataset.tool;
});
document.querySelectorAll('#ed_sizes button').forEach(b => b.onclick = () => {
  document.querySelectorAll('#ed_sizes button').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  edBrush = Number(b.dataset.sz);
});

function edCoords(e) {
  const r = ed_canvas.getBoundingClientRect();
  return [
    Math.max(0, Math.min(ed_canvas.width - 1, Math.floor((e.clientX - r.left) * ed_canvas.width / r.width))),
    Math.max(0, Math.min(ed_canvas.height - 1, Math.floor((e.clientY - r.top) * ed_canvas.height / r.height))),
  ];
}

function edDot(x, y, hex) {
  edCtx.fillStyle = hex;
  const off = Math.floor(edBrush / 2);
  edCtx.fillRect(x - off, y - off, edBrush, edBrush);
}

function edLine(x0, y0, x1, y1, hex) {
  let dx = Math.abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
  let dy = -Math.abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
  let err = dx + dy;
  while (true) {
    edDot(x0, y0, hex);
    if (x0 === x1 && y0 === y1) break;
    const e2 = 2 * err;
    if (e2 >= dy) { err += dy; x0 += sx; }
    if (e2 <= dx) { err += dx; y0 += sy; }
  }
}

function edFlood(sx, sy, fillHex) {
  const w = ed_canvas.width, h = ed_canvas.height;
  const img = edCtx.getImageData(0, 0, w, h);
  const d = img.data;
  const i0 = (sy * w + sx) * 4;
  const tr = d[i0], tg = d[i0 + 1], tb = d[i0 + 2];
  const fr = parseInt(fillHex.slice(1,3),16), fg = parseInt(fillHex.slice(3,5),16), fb = parseInt(fillHex.slice(5,7),16);
  if (tr === fr && tg === fg && tb === fb) return;
  const stack = [[sx, sy]];
  while (stack.length) {
    const [x, y] = stack.pop();
    if (x < 0 || x >= w || y < 0 || y >= h) continue;
    const i = (y * w + x) * 4;
    if (d[i] !== tr || d[i + 1] !== tg || d[i + 2] !== tb) continue;
    d[i] = fr; d[i + 1] = fg; d[i + 2] = fb; d[i + 3] = 255;
    stack.push([x + 1, y], [x - 1, y], [x, y + 1], [x, y - 1]);
  }
  edCtx.putImageData(img, 0, 0);
}

ed_canvas.onpointerdown = e => {
  const [x, y] = edCoords(e);
  if (edTool === 'pick') {
    const p = edCtx.getImageData(x, y, 1, 1).data;
    ed_color.value = '#' + [p[0], p[1], p[2]].map(v => v.toString(16).padStart(2, '0')).join('').toUpperCase();
    ed_pal.querySelectorAll('.ed-sw').forEach(el => el.classList.toggle('active', el.title === ed_color.value));
    return;
  }
  edPushUndo();
  if (edTool === 'fill') {
    edFlood(x, y, ed_color.value);
    return;
  }
  edDrawing = true;
  edLast = [x, y];
  edDot(x, y, edTool === 'erase' ? '#FFFFFF' : ed_color.value);
};
ed_canvas.onpointermove = e => {
  if (!edDrawing) return;
  const [x, y] = edCoords(e);
  edLine(edLast[0], edLast[1], x, y, edTool === 'erase' ? '#FFFFFF' : ed_color.value);
  edLast = [x, y];
};
window.addEventListener('pointerup', () => { edDrawing = false; edLast = null; });

ed_undo.onclick = () => { if (edUndoStack.length) edCtx.putImageData(edUndoStack.pop(), 0, 0); };
ed_clear.onclick = () => { edPushUndo(); edCtx.fillStyle = '#FFFFFF'; edCtx.fillRect(0, 0, ed_canvas.width, ed_canvas.height); };
ed_flip.onclick = () => {
  edPushUndo();
  const tmp = document.createElement('canvas');
  tmp.width = ed_canvas.width; tmp.height = ed_canvas.height;
  tmp.getContext('2d').drawImage(ed_canvas, 0, 0);
  edCtx.save(); edCtx.translate(ed_canvas.width, 0); edCtx.scale(-1, 1);
  edCtx.drawImage(tmp, 0, 0); edCtx.restore();
};
ed_rot.onclick = () => {
  edPushUndo();
  const tmp = document.createElement('canvas');
  tmp.width = ed_canvas.width; tmp.height = ed_canvas.height;
  tmp.getContext('2d').drawImage(ed_canvas, 0, 0);
  ed_canvas.width = tmp.height; ed_canvas.height = tmp.width;
  edCtx.save(); edCtx.translate(ed_canvas.width, 0); edCtx.rotate(Math.PI / 2);
  edCtx.drawImage(tmp, 0, 0); edCtx.restore();
  edSyncDisplaySize();
};
ed_apply.onclick = () => {
  ed_canvas.toBlob(blob => {
    editor_dlg.close();
    document.querySelectorAll('#demos button').forEach(x => x.classList.remove('active'));
    setFile(blob, 'Custom Drawing');
  }, 'image/png');
};

init();
</script>
</body>
</html>"""


def _settings(query: str) -> Settings:
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    kw = {}
    for k, conv in _FIELDS.items():
        if k in q and q[k] != "":
            kw[k] = conv(q[k])
    if "palette" in kw and not kw["palette"].strip():
        del kw["palette"]
    return Settings(**kw)


def _b64_png(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/palette":
            payload = json.dumps([{"name": n, "hex": h} for n, h in BAMBU_PLA]).encode()
            self._send(200, payload, "application/json; charset=utf-8")
            return
        if u.path == "/api/sample":
            q = urllib.parse.parse_qs(u.query)
            name = q.get("name", ["wave"])[0]
            try:
                self._send(200, sample_artwork_png(name), "image/png")
            except ValueError as e:
                self._send(400, str(e).encode("utf-8"), "text/plain")
            return
        page = (
            PAGE
            .replace("__N__", str(len(BAMBU_PLA)))
            .replace("__IDS__", json.dumps(list(_FIELDS)))
        )
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        try:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            r = run(body, _settings(u.query), allow_palette_file=False)
            if u.path == "/api/preview":
                self._send(200, r.tile_map_png, "image/png")
            elif u.path == "/api/export":
                self._send(200, r.zip_bytes(), "application/zip")
            elif u.path == "/api/file":
                q = urllib.parse.parse_qs(u.query)
                target = os.path.basename(q.get("file", [""])[0])
                if target == "swatch.3mf":
                    data = r.swatch_3mf
                elif target == "connectors.3mf":
                    data = r.connectors_3mf
                else:
                    data = r.tiles.get(target, b"")
                if not data:
                    self._send(404, b"file not found", "text/plain")
                    return
                self._send(200, data, "model/3mf",
                           {"Content-Disposition": f'attachment; filename="{target}"'})
            elif u.path == "/api/inspect":
                payload = json.dumps({
                    "grid": list(r.grid),
                    "tile_size_mm": list(r.tile_size_mm),
                    "schedule": r.schedule,
                    "readme": r.readme,
                    "has_connectors": bool(r.connectors_3mf),
                    "has_swatch": bool(r.swatch_3mf),
                    "preview_b64": _b64_png(r.preview_png),
                    "tile_map_b64": _b64_png(r.tile_map_png),
                }).encode("utf-8")
                self._send(200, payload, "application/json; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as e:
            self._send(400, str(e).encode("utf-8"), "text/plain")

    def log_message(self, *a):
        pass


def serve(port: int = 8765) -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"PixelTile UI: http://127.0.0.1:{port}")
    srv.serve_forever()
