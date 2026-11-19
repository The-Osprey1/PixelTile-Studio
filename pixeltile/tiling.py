"""Uniform tile grid + per-tile color limiting + spool-swap scheduling."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Tile:
    name: str          # e.g. "A1"
    row: int
    col: int
    idx: np.ndarray    # (rows, cols) palette indices after color limiting
    slot_map: dict[int, int] = field(default_factory=dict)  # palette cid -> 1-based extruder slot (2..)
    recolored: int = 0  # pixels whose color was replaced to fit the per-tile slot limit
    dropped: list[int] = field(default_factory=list)  # palette cids removed from this tile

    @property
    def recolored_frac(self) -> float:
        return self.recolored / self.idx.size if self.idx.size else 0.0


def tile_name(row: int, col: int) -> str:
    letters = ""
    n = row
    while True:
        letters = chr(ord("A") + n % 26) + letters
        n = n // 26 - 1
        if n < 0:
            break
    return f"{letters}{col + 1}"


def limit_colors(tile_idx: np.ndarray, pal_lab: np.ndarray, max_colors: int) -> np.ndarray:
    """Remap rarest colors to the nearest of the `max_colors` most frequent ones."""
    if max_colors < 1:
        raise ValueError("max_colors must be >= 1")
    ids, counts = np.unique(tile_idx, return_counts=True)
    if len(ids) <= max_colors:
        return tile_idx.copy()
    keep = ids[np.argsort(-counts, kind="stable")[:max_colors]]
    lut = np.arange(int(tile_idx.max()) + 1)
    for i in ids:
        if i not in keep:
            d = ((pal_lab[keep] - pal_lab[i]) ** 2).sum(1)
            lut[i] = keep[int(d.argmin())]
    return lut[tile_idx]


def split_tiles(idx: np.ndarray, tiles_x: int, tiles_y: int, pal_lab: np.ndarray,
                max_colors: int) -> list[Tile]:
    rows, cols = idx.shape
    if rows % tiles_y or cols % tiles_x:
        raise ValueError("Grid is not divisible into uniform tiles")
    th, tw = rows // tiles_y, cols // tiles_x
    tiles = []
    for r in range(tiles_y):
        for c in range(tiles_x):
            sub = idx[r * th:(r + 1) * th, c * tw:(c + 1) * tw]
            limited = limit_colors(sub, pal_lab, max_colors)
            changed = sub != limited
            dropped = sorted(set(np.unique(sub).tolist()) - set(np.unique(limited).tolist()))
            tiles.append(Tile(tile_name(r, c), r, c, limited, recolored=int(changed.sum()),
                              dropped=dropped))
    return tiles


def schedule_tiles(tiles: list[Tile], max_colors: int) -> list[Tile]:
    """Order tiles to minimize AMS/tool spool swaps and keep shared colors in consistent slots."""
    if not tiles:
        return []
    if max_colors < 1:
        raise ValueError("max_colors must be >= 1")

    counts_by_tile: list[dict[int, int]] = []
    csets: list[set[int]] = []
    for t in tiles:
        ids, cnts = np.unique(t.idx, return_counts=True)
        counts_by_tile.append({int(i): int(c) for i, c in zip(ids, cnts)})
        csets.append({int(i) for i in ids})

    remaining = list(range(len(tiles)))
    slots: list[int | None] = [None] * max_colors
    ordered: list[Tile] = []

    while remaining:
        current_loaded = {c for c in slots if c is not None}
        if not ordered:
            # Start with the tile using the most distinct colors (tie-break row-major / original order).
            pick_pos = max(
                range(len(remaining)),
                key=lambda k: (len(csets[remaining[k]]), -tiles[remaining[k]].row, -tiles[remaining[k]].col, -remaining[k]),
            )
        else:
            pick_pos = max(
                range(len(remaining)),
                key=lambda k: (len(current_loaded & csets[remaining[k]]), -tiles[remaining[k]].row, -tiles[remaining[k]].col, -remaining[k]),
            )
        ti = remaining.pop(pick_pos)
        t = tiles[ti]
        cset = csets[ti]
        cnts = counts_by_tile[ti]

        needed = sorted((c for c in cset if c not in slots), key=lambda c: (-cnts[c], c))
        if needed:
            future_freq: dict[int, int] = {}
            for ri in remaining:
                for c in csets[ri]:
                    future_freq[c] = future_freq.get(c, 0) + 1
            for c in needed:
                if None in slots:
                    slots[slots.index(None)] = c
                else:
                    evict_candidates = [i for i, s in enumerate(slots) if s not in cset]
                    if not evict_candidates:
                        break
                    evict_idx = min(evict_candidates, key=lambda i: (future_freq.get(slots[i], 0), i))
                    slots[evict_idx] = c

        t.slot_map = {cid: idx + 2 for idx, cid in enumerate(slots) if cid is not None and cid in cset}
        ordered.append(t)

    return ordered
