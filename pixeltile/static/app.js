const FIELDS = window.PIXELTILE_CONFIG.fields;
let allSpools = [];
let activeSpools = new Set();
let colorOverrides = {};   // spool name -> corrected #RRGGBB (measured from a printed swatch)
let pickTarget = null;     // spool whose color is being corrected
let activeTab = 'map';
let activeFile = null;
let selectedTile = null;
let timer = null;
let inspectSeq = 0;        // only the newest preview request may update the screen
let inspectCtl = null;
let prefsTimer = null;
let prefsLoaded = false;

// Settings remembered between launches (saved by the local server, see /api/prefs).
const PREF_FIELDS = ['width_mm', 'pixel_mm', 'max_tile_size_mm', 'max_tile_colors', 'auto_colors',
  'backing_color', 'color_depth_mm', 'backing_mm', 'contrast', 'saturation'];
const PREF_CHECKS = ['dither', 'connector_keys', 'wall_mount'];

function escapeHtml(v) {
  return String(v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function spoolHex(s) { return colorOverrides[s.name] || s.hex; }

function formatGrams(g) { return g >= 1000 ? `${(g / 1000).toFixed(2)} kg` : `${Math.round(g)} g`; }

function syncControls() {
  width_rng.value = Math.min(1200, Math.max(80, Number(width_mm.value) || 200));
  dim_live.textContent = `${width_mm.value} mm`;
  col_live.textContent = auto_colors.value === '0' ? 'All Owned Spools' : `${auto_colors.value} Colors`;
  document.querySelectorAll('#pixbtns button').forEach(b =>
    b.classList.toggle('active', Number(b.dataset.px) === Number(pixel_mm.value)));
  document.querySelectorAll('#presets .hw-card').forEach(c => c.classList.toggle('active',
    Number(c.dataset.slots) === Number(max_tile_colors.value) && Number(c.dataset.tile) === Number(max_tile_size_mm.value)));
  const mount = wall_mount.checked ? 'wall' : connector_keys.checked ? 'keys' : 'flat';
  document.querySelectorAll('#mount_mode .mount-opt').forEach(o => o.classList.toggle('active', o.dataset.mount === mount));
}

function applyPrefs(p) {
  if (!p || typeof p !== 'object') return;
  if (Array.isArray(p.spools)) {
    const known = new Set(allSpools.map(s => s.name));
    activeSpools = new Set(p.spools.filter(n => known.has(n)));
  }
  if (p.overrides && typeof p.overrides === 'object') {
    colorOverrides = {};
    for (const [n, h] of Object.entries(p.overrides)) {
      if (typeof h === 'string' && /^#[0-9a-fA-F]{6}$/.test(h)) colorOverrides[n] = h.toUpperCase();
    }
  }
  if (typeof p.custom_palette === 'string') custom_pal.value = p.custom_palette;
  const f = (p.fields && typeof p.fields === 'object') ? p.fields : {};
  for (const id of PREF_FIELDS) if (id in f) document.getElementById(id).value = f[id];
  for (const id of PREF_CHECKS) if (id in f) document.getElementById(id).checked = !!f[id];
  syncControls();
}

function savePrefs() {
  if (!prefsLoaded) return;
  clearTimeout(prefsTimer);
  prefsTimer = setTimeout(() => {
    const fields = {};
    for (const id of PREF_FIELDS) fields[id] = document.getElementById(id).value;
    for (const id of PREF_CHECKS) fields[id] = document.getElementById(id).checked;
    const body = JSON.stringify({
      version: 1, spools: Array.from(activeSpools), overrides: colorOverrides,
      custom_palette: custom_pal.value, fields,
    });
    fetch('/api/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body }).catch(() => {});
  }, 400);
}

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
  try {
    applyPrefs(await (await fetch('/api/prefs')).json());
  } catch (e) { /* first launch or unreadable preferences: keep the defaults */ }
  prefsLoaded = true;
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
      const fixed = !!colorOverrides[s.name];
      el.className = 'chip' + (activeSpools.has(s.name) ? ' on' : '') + (fixed ? ' fixed' : '');
      el.title = fixed ? `${s.name}: corrected to ${colorOverrides[s.name]} (built-in ${s.hex})` : `${s.name} ${s.hex}`;
      el.innerHTML = `<span class="dot" style="background:${spoolHex(s)}"></span><span>${escapeHtml(s.name)}</span>`;
      el.onclick = () => {
        if (sp_edit.checked) { pickSpoolColor(s); return; }
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
spnone.onclick = () => { activeSpools.clear(); renderSpools(); savePrefs(); };

function pickSpoolColor(s) {
  pickTarget = s;
  spool_color_pick.value = spoolHex(s).toLowerCase();
  if (typeof spool_color_pick.showPicker === 'function') {
    try { spool_color_pick.showPicker(); return; } catch (e) { /* fall back to click() */ }
  }
  spool_color_pick.click();
}
spool_color_pick.oninput = () => {
  if (!pickTarget) return;
  const v = spool_color_pick.value.toUpperCase();
  if (v === pickTarget.hex.toUpperCase()) delete colorOverrides[pickTarget.name];
  else colorOverrides[pickTarget.name] = v;
  renderSpools();
};
spool_color_pick.onchange = () => { spool_color_pick.oninput(); scheduleInspect(); };
sp_reset.onclick = () => { colorOverrides = {}; renderSpools(); scheduleInspect(); };

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
  syncControls();
  savePrefs();
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
  const corrected = Array.from(activeSpools).some(n => colorOverrides[n]);
  if (allSpools.length > 0 && (activeSpools.size < allSpools.length || extra || corrected)) {
    const tokens = Array.from(activeSpools).map(n => colorOverrides[n] ? `${n}=${colorOverrides[n]}` : n);
    if (extra) tokens.push(...extra.split(',').map(x => x.trim()).filter(Boolean));
    p.set('palette', tokens.join(','));
  }
  const bk = allSpools.find(s => s.name.toLowerCase() === backing_color.value.trim().toLowerCase());
  if (bk && colorOverrides[bk.name]) p.set('backing_color', `${bk.name}=${colorOverrides[bk.name]}`);
  return p;
}

async function runInspect() {
  if (!activeFile) return;
  let q;
  try { q = buildQuery(); } catch (e) { err.textContent = e.message; return; }
  const seq = ++inspectSeq;
  if (inspectCtl) inspectCtl.abort();
  inspectCtl = new AbortController();
  status_txt.textContent = 'Updating preview & schedule...';
  let data;
  try {
    const r = await fetch('/api/inspect?' + q.toString(), { method: 'POST', body: activeFile, signal: inspectCtl.signal });
    if (!r.ok) {
      const msg = await r.text();
      if (seq === inspectSeq) { err.textContent = msg; status_txt.textContent = ''; }
      return;
    }
    data = await r.json();
  } catch (e) {
    if (e.name !== 'AbortError' && seq === inspectSeq) {
      err.textContent = `Preview failed: ${e.message}`;
      status_txt.textContent = '';
    }
    return;
  }
  if (seq !== inspectSeq) return;  // a newer request replaced this one while it was running
  status_txt.textContent = '';
  err.textContent = '';
  warn_box.innerHTML = (data.warnings || []).map(w => `<div>⚠ ${escapeHtml(w)}</div>`).join('');
  ph.style.display = 'none';
  img_map.src = data.tile_map_b64;
  img_prev.src = data.preview_b64;
  guide_box.textContent = data.readme;
  showTab(activeTab);

  const [tx, ty] = data.grid;
  const totalSwaps = data.schedule.reduce((a, b) => a + b.swaps_from_prev, 0);
  const totalGrams = (data.filament || []).reduce((a, f) => a + f.grams, 0);
  const totalW = Math.round(tx * data.tile_size_mm[0]);
  const totalH = Math.round(ty * data.tile_size_mm[1]);
  dim_live.textContent = `${totalW} × ${totalH} mm (${tx}×${ty})`;
  telemetry.innerHTML =
    `<span><strong>${totalW} × ${totalH} mm</strong></span><span>•</span>` +
    `<span><strong>${tx}×${ty}</strong> (${tx * ty} ${tx * ty === 1 ? 'Tile' : 'Tiles'})</span><span>•</span>` +
    `<span><strong>${totalSwaps}</strong> swaps</span><span>•</span>` +
    `<span title="Tiles only; purge and prime tower waste not included"><strong>${formatGrams(totalGrams)}</strong> filament</span>`;

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

  swatch_btns.innerHTML = '';
  for (const name of data.swatches || []) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn-table';
    b.title = 'Calibration swatch: print it to check your spools match the preview';
    b.textContent = `⬇ ${name}`;
    b.onclick = () => downloadFile(name);
    swatch_btns.appendChild(b);
  }
  dl_conn.style.display = data.has_connectors ? 'inline-flex' : 'none';
  sched.innerHTML = data.schedule.map((row, idx) => {
    const badges = row.slots.map(s =>
      `<span class="slot-chip" title="${escapeHtml(s.name)}: ${s.grams} g"><i style="background:${s.hex}"></i>T${s.slot} ${escapeHtml(s.name)}</span>`
    ).join('');
    const sw = idx === 0 ? 'Initial' : `${row.swaps_from_prev}`;
    const fname = `${row.name}.3mf`;
    const loss = row.recolored_pct || 0;
    const lossCls = loss > 5 ? 'loss-high' : loss > 0 ? 'loss-low' : 'loss-none';
    const lossTitle = row.dropped && row.dropped.length ? `Colors dropped from this tile: ${row.dropped.join(', ')}` : 'No colors lost';
    return `<tr data-tile="${row.name}"><td>${idx + 1}</td><td><strong>${row.name}</strong></td><td>${sw}</td>` +
           `<td class="${lossCls}" title="${escapeHtml(lossTitle)}">${loss ? loss + '%' : '—'}</td><td>${badges}</td>` +
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
