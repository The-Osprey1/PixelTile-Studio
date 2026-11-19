"""Face-down tile geometry: merged color boxes + solid/pocketed backing plate + connectors + swatches."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

Box = tuple[float, float, float, float, float, float]  # x0,y0,z0,x1,y1,z1 (mm)

# Hardware dimensions (mm).
WALL_DEPTH_MM = 7.7            # total depth of a wall-mount tile, face to wall
RIM_WIDTH_MM = 12.0            # wall-mount perimeter rim width
MIN_HARDWARE_TILE_MM = 40.0    # smaller tiles get no rim / connector pockets
MIN_POCKET_BACKING_MM = 1.0    # flat backs thinner than this get no connector pockets
KEY_W_MM, KEY_H_MM = 11.6, 15.6  # connector key footprint (spans two pockets across a seam)
POCKET_DEPTH_MM, POCKET_LEN_MM = 6.0, 16.0  # minimum pocket size on each side of a seam


@dataclass
class Part:
    name: str
    hex: str
    extruder: int      # 1-based filament slot
    boxes: list[Box]

    @property
    def volume_mm3(self) -> float:
        return float(sum((x1 - x0) * (y1 - y0) * (z1 - z0) for x0, y0, z0, x1, y1, z1 in self.boxes))


def rim_fits(tile_w_mm: float, tile_h_mm: float, z_base: float) -> bool:
    """True when a wall-mount rim (and its keyholes / seam pockets) can be built on this tile."""
    return (WALL_DEPTH_MM > z_base + 0.4
            and tile_w_mm >= MIN_HARDWARE_TILE_MM and tile_h_mm >= MIN_HARDWARE_TILE_MM)


def flat_pockets_fit(tile_w_mm: float, tile_h_mm: float, backing_mm: float) -> bool:
    """True when connector pockets can be cut into a flat backing plate."""
    return (backing_mm >= MIN_POCKET_BACKING_MM
            and tile_w_mm >= MIN_HARDWARE_TILE_MM and tile_h_mm >= MIN_HARDWARE_TILE_MM)


def _row_runs(mask: np.ndarray) -> list[set[tuple[int, int]]]:
    """Per row, the set of (start, end) column runs of True cells (end-exclusive)."""
    rows, cols = mask.shape
    padded = np.zeros((rows, cols + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    d = np.diff(padded, axis=1)
    sr, sc = np.nonzero(d == 1)   # row-major order, so starts and ends pair up
    _, ec = np.nonzero(d == -1)
    runs: list[set[tuple[int, int]]] = [set() for _ in range(rows)]
    for r, s, e in zip(sr.tolist(), sc.tolist(), ec.tolist()):
        runs[r].add((s, e))
    return runs


def merge_rects(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Greedy cover of a boolean mask by disjoint rectangles (r0, c0, r1, c1), end-exclusive."""
    mask = np.asarray(mask, dtype=bool)
    rects: list[tuple[int, int, int, int]] = []
    active: dict[tuple[int, int], int] = {}  # (c0, c1) -> start row
    row_runs = _row_runs(mask)
    for r in range(mask.shape[0] + 1):
        runs = row_runs[r] if r < mask.shape[0] else set()
        for key in list(active):
            if key not in runs:
                rects.append((active.pop(key), key[0], r, key[1]))
        for key in runs:
            active.setdefault(key, r)
    return rects


def _rects_to_boxes(rects: list[tuple[int, int, int, int]], rows: int, cols: int,
                    pixel_mm: float, z0: float, z1: float) -> list[Box]:
    boxes: list[Box] = []
    for r0, c0, r1, c1 in rects:
        x0 = (cols - c1) * pixel_mm
        x1 = (cols - c0) * pixel_mm
        y0 = (rows - r1) * pixel_mm
        y1 = (rows - r0) * pixel_mm
        boxes.append((x0, y0, z0, x1, y1, z1))
    return boxes


def _cut_edge_pockets(mask: np.ndarray, pixel_mm: float,
                      shared_edges: tuple[bool, bool, bool, bool]) -> None:
    """Subtract centered >=16 x 6 mm connector pockets along each shared interior edge.

    Sizes are rounded UP to whole pixels so a connector key always fits across the seam."""
    rows, cols = mask.shape

    def cells(mm: float) -> int:
        return max(1, math.ceil(mm / pixel_mm - 1e-9))

    len_r = min(rows - 2, max(2, cells(POCKET_LEN_MM)))
    len_c = min(cols - 2, max(2, cells(POCKET_LEN_MM)))
    depth = cells(POCKET_DEPTH_MM)

    top, bottom, left, right = shared_edges
    if top:
        c0 = (cols - len_c) // 2
        mask[:depth, c0:c0 + len_c] = False
    if bottom:
        c0 = (cols - len_c) // 2
        mask[rows - depth:, c0:c0 + len_c] = False
    if left:
        r0 = (rows - len_r) // 2
        mask[r0:r0 + len_r, :depth] = False
    if right:
        r0 = (rows - len_r) // 2
        mask[r0:r0 + len_r, cols - depth:] = False


def _backing_boxes(rows: int, cols: int, pixel_mm: float, color_depth: float, backing_mm: float,
                   shared_edges: tuple[bool, bool, bool, bool]) -> list[Box]:
    w, h = cols * pixel_mm, rows * pixel_mm
    z0, z1 = color_depth, color_depth + backing_mm
    if not (any(shared_edges) and flat_pockets_fit(w, h, backing_mm)):
        return [(0.0, 0.0, z0, w, h, z1)]

    z_mid = color_depth + backing_mm * 0.5
    boxes: list[Box] = [(0.0, 0.0, z0, w, h, z_mid)]

    mask = np.ones((rows, cols), dtype=bool)
    _cut_edge_pockets(mask, pixel_mm, shared_edges)
    boxes.extend(_rects_to_boxes(merge_rects(mask), rows, cols, pixel_mm, z_mid, z1))
    return boxes


def _wall_mount_rim_boxes(
    rows: int,
    cols: int,
    pixel_mm: float,
    z_base: float,
    total_depth_mm: float = WALL_DEPTH_MM,
    rim_width_mm: float = RIM_WIDTH_MM,
    shared_edges: tuple[bool, bool, bool, bool] = (False, False, False, False),
    is_top_row: bool = False,
    is_bottom_row: bool = False,
    has_keyhole: bool = False,
) -> list[Box]:
    """Build a 12 mm perimeter back rim up to 7.7 mm with optional keyholes and seam pockets."""
    if total_depth_mm <= z_base + 0.4 or not rim_fits(cols * pixel_mm, rows * pixel_mm, z_base):
        return []

    rim_c = max(1, min(rows // 3, cols // 3, round(rim_width_mm / pixel_mm)))
    rim = np.zeros((rows, cols), dtype=bool)
    rim[:rim_c, :] = True
    rim[-rim_c:, :] = True
    rim[:, :rim_c] = True
    rim[:, -rim_c:] = True

    z_mid = z_base + (total_depth_mm - z_base) * 0.5
    z_top = total_depth_mm

    lower = rim.copy()
    upper = rim.copy()

    if has_keyhole and is_top_row:
        c_mid = cols // 2
        w_head = max(2, round(10.0 / pixel_mm))
        w_shaft = max(1, round(5.0 / pixel_mm))
        r_start = max(1, rim_c // 4)
        r_end = min(rows - 1, rim_c)
        r_mid = r_start + (r_end - r_start) // 2

        hc0 = max(0, c_mid - w_head // 2)
        hc1 = min(cols, hc0 + w_head)
        sc0 = max(0, c_mid - w_shaft // 2)
        sc1 = min(cols, sc0 + w_shaft)

        lower[r_start:r_end, hc0:hc1] = False
        upper[r_start:r_mid, sc0:sc1] = False
        upper[r_mid:r_end, hc0:hc1] = False

    if any(shared_edges):
        _cut_edge_pockets(upper, pixel_mm, shared_edges)

    boxes = _rects_to_boxes(merge_rects(lower), rows, cols, pixel_mm, z_base, z_mid)
    boxes.extend(_rects_to_boxes(merge_rects(upper), rows, cols, pixel_mm, z_mid, z_top))
    return boxes


def tile_parts(
    idx: np.ndarray,
    palette: list[tuple[str, str]],
    pixel_mm: float,
    color_depth: float,
    backing_mm: float,
    backing: tuple[str, str],
    slot_map: dict[int, int] | None = None,
    shared_edges: tuple[bool, bool, bool, bool] = (False, False, False, False),
    wall_mount: bool = False,
    is_top_row: bool = False,
    is_bottom_row: bool = False,
    has_keyhole: bool = False,
) -> list[Part]:
    """Slot 1 = backing; slots 2.. = this tile's colors (per slot_map or most used first).

    Printed face-down: z=0 is the visible face, so x is mirrored to read correctly from below.
    """
    rows, cols = idx.shape
    w, h = cols * pixel_mm, rows * pixel_mm
    ids, counts = np.unique(idx, return_counts=True)
    order = ids[np.argsort(-counts, kind="stable")]
    if wall_mount:
        backing_boxes: list[Box] = [(0.0, 0.0, color_depth, w, h, color_depth + backing_mm)]
        backing_boxes.extend(
            _wall_mount_rim_boxes(
                rows, cols, pixel_mm, color_depth + backing_mm,
                shared_edges=shared_edges,
                is_top_row=is_top_row,
                is_bottom_row=is_bottom_row,
                has_keyhole=has_keyhole,
            )
        )
    else:
        backing_boxes = _backing_boxes(rows, cols, pixel_mm, color_depth, backing_mm, shared_edges)

    parts: list[Part] = [
        Part(f"Backing ({backing[0]})", backing[1], 1, backing_boxes)
    ]
    color_parts: list[Part] = []
    for i, cid in enumerate(order):
        cid_int = int(cid)
        slot = slot_map[cid_int] if slot_map is not None else i + 2
        boxes = _rects_to_boxes(merge_rects(idx == cid), rows, cols, pixel_mm, 0.0, color_depth)
        color_parts.append(Part(palette[cid_int][0], palette[cid_int][1], slot, boxes))
    if slot_map is not None:
        color_parts.sort(key=lambda p: p.extruder)
    parts.extend(color_parts)
    return parts


def connector_key_parts(n_keys: int, backing: tuple[str, str], thickness_mm: float = 0.8) -> list[Part]:
    """Generate rectangular connector keys (11.6 x 15.6 mm) laid out on the build plate."""
    if n_keys <= 0:
        return []
    key_w, key_h, gap = KEY_W_MM, KEY_H_MM, 4.0
    cols = max(1, math.ceil(math.sqrt(n_keys)))
    boxes: list[Box] = []
    for k in range(n_keys):
        r, c = divmod(k, cols)
        x0 = c * (key_w + gap)
        y0 = r * (key_h + gap)
        boxes.append((x0, y0, 0.0, x0 + key_w, y0 + key_h, thickness_mm))
    return [Part(f"Connector Keys ({backing[0]})", backing[1], 1, boxes)]


def swatch_parts(
    used_cids: list[int],
    palette: list[tuple[str, str]],
    backing: tuple[str, str],
    max_tile_colors: int,
    color_depth: float = 1.2,
    backing_mm: float = 1.6,
) -> tuple[list[Part], list[str]]:
    """Generate a compact calibration swatch bar for up to (max_tile_colors - 1) palette colors."""
    cids = list(dict.fromkeys(int(c) for c in used_cids))[:max(1, max_tile_colors - 1)]
    sq, gap, margin = 12.0, 2.0, 2.0
    w = margin * 2 + len(cids) * sq + max(0, len(cids) - 1) * gap
    h = margin * 2 + sq
    parts: list[Part] = [
        Part(f"Backing ({backing[0]})", backing[1], 1,
             [(0.0, 0.0, color_depth, w, h, color_depth + backing_mm)])
    ]
    slot_colors = [backing[1]]
    for i, cid in enumerate(cids):
        x0 = margin + i * (sq + gap)
        name, hx = palette[cid]
        parts.append(Part(name, hx, i + 2, [(x0, margin, 0.0, x0 + sq, margin + sq, color_depth)]))
        slot_colors.append(hx)
    slot_colors += ["#FFFFFF"] * max(0, max_tile_colors - len(slot_colors))
    return parts, slot_colors
