"""Bambu Studio / OrcaSlicer .3mf writer (one object, one part per filament).

Layout follows the public Bambu 3MF conventions (core model + Metadata/*.config),
re-implemented from scratch. Each merged box is written as its own 8 vertices /
12 triangles with NO vertex sharing across boxes, so every box is a closed
manifold shell and no T-junctions can occur.
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


def write_3mf(parts: list[Part], object_name: str, slot_colors: list[str],
              layer_height: float = 0.2, nozzle: float = 0.4, slot_types: list[str] | None = None) -> bytes:
    """`slot_colors` lists ALL filament slots (1..N, e.g. 8); unused ones are still written."""
    parts = [p for p in parts if p.boxes]
    if not parts:
        raise ValueError("No geometry to export")
    n_slots = len(slot_colors)
    for p in parts:
        if not 1 <= p.extruder <= n_slots:
            raise ValueError(f"{p.name}: extruder {p.extruder} outside 1..{n_slots}")
    types = slot_types or ["PLA"] * n_slots

    base = "".join(f'<base name={quoteattr("Filament %d" % (i + 1))} displaycolor="{c.upper()}FF"/>'
                   for i, c in enumerate(h.lstrip("#") for h in slot_colors))
    objs = "".join(
        f'<object id="{i + 2}" name={quoteattr(p.name)} type="model" pid="1" pindex="{p.extruder - 1}">{_mesh(p.boxes)}</object>'
        for i, p in enumerate(parts))
    asm = len(parts) + 2
    comps = "".join(f'<component objectid="{i + 2}"/>' for i in range(len(parts)))
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
             'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021">'
             '<metadata name="Application">PixelTile</metadata><metadata name="BambuStudio:3mfVersion">1</metadata>'
             f'<resources><basematerials id="1">{base}</basematerials>{objs}'
             f'<object id="{asm}" name={quoteattr(object_name)} type="model"><components>{comps}</components></object>'
             f'</resources><build><item objectid="{asm}"/></build></model>')

    settings_parts = "".join(
        f'<part id="{i + 2}" subtype="normal_part"><metadata key="name" value={quoteattr(p.name)}/>'
        f'<metadata key="extruder" value="{p.extruder}"/></part>' for i, p in enumerate(parts))
    model_settings = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>'
                      f'<object id="{asm}"><metadata key="name" value={quoteattr(object_name)}/>'
                      f'<metadata key="extruder" value="1"/>{settings_parts}</object></config>')

    import json
    project = json.dumps({
        "filament_colour": [c.upper() if c.startswith("#") else "#" + c.upper() for c in slot_colors],
        "filament_type": types,
        "nozzle_diameter": [_num(nozzle)],
        "layer_height": _num(layer_height),
        "initial_layer_print_height": _num(layer_height),
    }, indent=4) + "\n"

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/project_settings.config", project)
        z.writestr("Metadata/model_settings.config", model_settings)
    return buf.getvalue()
