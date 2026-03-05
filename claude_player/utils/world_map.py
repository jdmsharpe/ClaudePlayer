"""Persistent per-map tile accumulator.

Stitches together the 10x9 viewport grid each turn into a full explored map
using absolute map coordinates.  Only static terrain is stored (no NPCs/items).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Tile types worth stamping (static terrain only — NPCs/ghosts excluded)
# Items (i) and objects (o) are included: items self-heal when collected
# (next visit overwrites 'i' with '.'), objects are stationary.
_STATIC_TILES = frozenset(".#,=v><TBWio")

# Max rendered dimension before we crop around the player
_MAX_RENDER_SIZE = 30


class WorldMap:
    """Accumulates explored tiles across turns, keyed by map ID."""

    def __init__(self) -> None:
        # map_id → {(abs_x, abs_y): tile_char}
        self.tiles: Dict[int, Dict[Tuple[int, int], str]] = {}
        # map_id → {(abs_x, abs_y): dest_name}
        self.warps: Dict[int, Dict[Tuple[int, int], str]] = {}

    def update(
        self,
        map_id: int,
        player_pos: Tuple[int, int],
        player_screen_pos: Tuple[int, int],
        grid: List[List[str]],
        warp_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Stamp the current viewport grid into the accumulated map."""
        if map_id not in self.tiles:
            self.tiles[map_id] = {}
        tile_map = self.tiles[map_id]

        px_map, py_map = player_pos
        px_screen, py_screen = player_screen_pos

        for gy, row in enumerate(grid):
            for gx, cell in enumerate(row):
                if cell in _STATIC_TILES:
                    abs_x = px_map + (gx - px_screen)
                    abs_y = py_map + (gy - py_screen)
                    tile_map[(abs_x, abs_y)] = cell

        # Record warps with destination names
        if warp_data:
            if map_id not in self.warps:
                self.warps[map_id] = {}
            warp_map = self.warps[map_id]
            for w in warp_data.get("warps", []):
                wx = px_map + w["dx"]
                wy = py_map + w["dy"]
                warp_map[(wx, wy)] = w.get("dest_name", "?")

    def save(self, path: str) -> None:
        """Serialize explored map to JSON."""
        data = {
            "tiles": {
                str(mid): {f"{x},{y}": ch for (x, y), ch in tmap.items()}
                for mid, tmap in self.tiles.items()
            },
            "warps": {
                str(mid): {f"{x},{y}": name for (x, y), name in wmap.items()}
                for mid, wmap in self.warps.items()
            },
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        logger.info(f"WorldMap saved: {sum(len(t) for t in self.tiles.values())} tiles across {len(self.tiles)} maps")

    def load(self, path: str) -> None:
        """Load explored map from JSON, merging into current state."""
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                data = json.load(f)
            for mid_str, tmap in data.get("tiles", {}).items():
                mid = int(mid_str)
                if mid not in self.tiles:
                    self.tiles[mid] = {}
                for key, ch in tmap.items():
                    x, y = map(int, key.split(","))
                    self.tiles[mid][(x, y)] = ch
            for mid_str, wmap in data.get("warps", {}).items():
                mid = int(mid_str)
                if mid not in self.warps:
                    self.warps[mid] = {}
                for key, name in wmap.items():
                    x, y = map(int, key.split(","))
                    self.warps[mid][(x, y)] = name
            logger.info(f"WorldMap loaded: {sum(len(t) for t in self.tiles.values())} tiles across {len(self.tiles)} maps")
        except Exception as e:
            logger.warning(f"WorldMap load failed: {e}")

    def render(
        self,
        map_id: int,
        player_pos: Tuple[int, int],
        dead_end_zones: Optional[List[Tuple[int, int]]] = None,
    ) -> Optional[str]:
        """Render the explored map for the given map ID.

        dead_end_zones: list of (x, y) centre points that were detected as
            stuck/dead-end areas. Shown as 'X' on the map so Claude can see
            visited traps relative to unexplored ('?') territory.

        Returns None if fewer than 15 tiles explored (viewport already covers it).
        """
        tile_map = self.tiles.get(map_id)
        if not tile_map or len(tile_map) < 15:
            return None

        # Build a set of tiles within radius 2 of any dead-end zone centre
        dead_end_tiles: set = set()
        if dead_end_zones:
            for dz_x, dz_y in dead_end_zones:
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        dead_end_tiles.add((dz_x + dx, dz_y + dy))

        # Always center on player
        px, py = player_pos
        half = _MAX_RENDER_SIZE // 2
        min_x = px - half
        max_x = px + half
        min_y = py - half
        max_y = py + half

        warp_map = self.warps.get(map_id, {})
        lines = []

        for y in range(min_y, max_y + 1):
            row_chars = []
            for x in range(min_x, max_x + 1):
                if (x, y) == (px, py):
                    row_chars.append("@")
                elif (x, y) in warp_map:
                    row_chars.append("W")
                elif (x, y) in tile_map:
                    ch = tile_map[(x, y)]
                    # Mark walkable dead-end tiles with X (walls stay as #)
                    if ch in (".", ",") and (x, y) in dead_end_tiles:
                        row_chars.append("X")
                    else:
                        row_chars.append(ch)
                else:
                    row_chars.append("?")
            lines.append("".join(row_chars))

        return "\n".join(lines)

    def frontier_dirs(
        self,
        map_id: int,
        player_pos: Tuple[int, int],
        radius: int = 20,
    ) -> Optional[str]:
        """Return a directional exploration hint based on unexplored frontiers.

        Scans explored walkable tiles within *radius* of the player.  A tile is
        a "frontier" if it is walkable ('.') and at least one of its 4 neighbours
        has never been seen.  Frontiers are bucketed by cardinal direction
        relative to the player, and a hint string is returned ranking directions
        by frontier count.

        Returns None if not enough data or no frontiers detected.
        """
        tile_map = self.tiles.get(map_id)
        if not tile_map or len(tile_map) < 30:
            return None

        px, py = player_pos
        counts: Dict[str, int] = {"NORTH": 0, "SOUTH": 0, "EAST": 0, "WEST": 0}

        _WALKABLE = frozenset(".,")  # floor + grass
        for (tx, ty), ch in tile_map.items():
            # Only consider walkable tiles near the player
            if ch not in _WALKABLE:
                continue
            dx, dy = tx - px, ty - py
            if abs(dx) > radius or abs(dy) > radius:
                continue
            # Check if any neighbour is unexplored
            has_unknown = any(
                (tx + ox, ty + oy) not in tile_map
                for ox, oy in ((0, -1), (0, 1), (-1, 0), (1, 0))
            )
            if not has_unknown:
                continue
            # Bucket by dominant direction from player
            if abs(dy) >= abs(dx):
                if dy < 0:
                    counts["NORTH"] += 1
                else:
                    counts["SOUTH"] += 1
            else:
                if dx < 0:
                    counts["WEST"] += 1
                else:
                    counts["EAST"] += 1

        total = sum(counts.values())
        if total < 3:
            return None

        # Sort by count descending, filter out zero-count directions
        ranked = sorted(
            ((d, c) for d, c in counts.items() if c > 0),
            key=lambda x: x[1],
            reverse=True,
        )
        parts = [f"{d}({c})" for d, c in ranked]
        best_dir = ranked[0][0]
        return (
            f"FRONTIER: {total} unexplored edges nearby — "
            + ", ".join(parts)
            + f". Head {best_dir} for new territory."
        )
