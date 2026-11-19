"""Bambu Studio .3mf writer (one object, one part per filament slot).

Layout follows the public Bambu 3MF conventions (core model + Metadata/model_settings.config),
re-implemented from scratch. Each merged box is written as its own 8 vertices /
12 triangles with NO vertex sharing across boxes, so every box is a closed
manifold shell and no T-junctions can occur.

How Bambu Studio (2.9) reads these files, and why they are written this way:
- Each part's filament slot is stored as "extruder" metadata in model_settings.config.
  Bambu Studio applies part metadata from any 3MF, so the slots survive.
- No Metadata/project_settings.config: for 3MF files from other programs, Bambu Studio
  turns that file into new printer / process / filament presets built from its generic
  defaults and selects them, replacing the user's own presets.
- No per-object colors (basematerials pid/pindex): for files from other programs, any
  colored triangle makes Bambu Studio erase every part's slot and run its color import
  dialog instead. Slot colors therefore come from the user's filaments or AMS sync.
"""
from __future__ import annotations

import zipfile
from io import BytesIO
from xml.sax.saxutils import quoteattr

from .geometry import Part

_CORNERS = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
            (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
# 12 triangles, counter-clockwise seen from outside.
_TRIS = [(0, 3, 1), (1, 3, 2), (0, 4, 7), (0, 7, 3), (4, 5, 6), (4, 6, 7),
         (5, 1, 2), (5, 2, 6), (2, 3, 6), (3, 7, 6), (0, 1, 5), (0, 5, 4)]

CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                 '</Types>')
RELS = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')


def _num(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


def _mesh(boxes) -> str:
    verts, tris = [], []
    for i, (x0, y0, z0, x1, y1, z1) in enumerate(boxes):
        xs, ys, zs = (x0, x1), (y0, y1), (z0, z1)
        for cx, cy, cz in _CORNERS:
            verts.append(f'<vertex x="{_num(xs[cx])}" y="{_num(ys[cy])}" z="{_num(zs[cz])}"/>')
        b = i * 8
        for a, c, d in _TRIS:
            tris.append(f'<triangle v1="{b + a}" v2="{b + c}" v3="{b + d}"/>')
    return f'<mesh><vertices>{"".join(verts)}</vertices><triangles>{"".join(tris)}</triangles></mesh>'


def write_3mf(parts: list[Part], object_name: str, n_slots: int) -> bytes:
    """Write one multi-part object; each part prints with its 1-based filament slot (1..n_slots)."""
    parts = [p for p in parts if p.boxes]
    if not parts:
        raise ValueError("No geometry to export")
    for p in parts:
        if not 1 <= p.extruder <= n_slots:
            raise ValueError(f"{p.name}: extruder {p.extruder} outside 1..{n_slots}")

    objs = "".join(
        f'<object id="{i + 1}" name={quoteattr(p.name)} type="model">{_mesh(p.boxes)}</object>'
        for i, p in enumerate(parts))
    asm = len(parts) + 1
    comps = "".join(f'<component objectid="{i + 1}"/>' for i in range(len(parts)))
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             '<metadata name="Application">PixelTile</metadata>'
             f'<resources>{objs}'
             f'<object id="{asm}" name={quoteattr(object_name)} type="model"><components>{comps}</components></object>'
             f'</resources><build><item objectid="{asm}"/></build></model>')

    settings_parts = "".join(
        f'<part id="{i + 1}" subtype="normal_part"><metadata key="name" value={quoteattr(p.name)}/>'
        f'<metadata key="extruder" value="{p.extruder}"/></part>' for i, p in enumerate(parts))
    model_settings = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>'
                      f'<object id="{asm}"><metadata key="name" value={quoteattr(object_name)}/>'
                      f'<metadata key="extruder" value="1"/>{settings_parts}</object></config>')

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/model_settings.config", model_settings)
    return buf.getvalue()
