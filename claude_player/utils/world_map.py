"""Persistent per-map tile accumulator.

Stitches together the 10x9 viewport grid each turn into a full explored map
using absolute map coordinates.  Only static terrain is stored (no NPCs/items).
"""

from __future__ import annotations

import heapq
import json
import logging
import os
from collections import deque
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Tile types worth stamping (static terrain only — NPCs/ghosts excluded)
# Items (i) and objects (o) are included: items self-heal when collected
# (next visit overwrites 'i' with '.'), objects are stationary.
_STATIC_TILES = frozenset(".#,=v><TBio")  # W excluded: warp positions tracked in warp_map, rebuilt each turn

# Impassable tiles for world-map A*
_BLOCKED_TILES: FrozenSet[str] = frozenset("#=TBWio")

from claude_player.utils.pathfinding import LEDGE_ALLOWED_DIR, NEIGHBORS, DIR_BUTTONS

# Max rendered dimension before we crop around the player
_MAX_RENDER_SIZE = 40       # AI context (full exploration visible)
_MAX_DISPLAY_SIZE = 20     # Web/terminal display — large enough to show full explored maps (panel scrolls)

# Max steps in a world-map A* path before we truncate
_MAX_PATH_STEPS = 30


class WorldMap:
    """Accumulates explored tiles across turns, keyed by map ID."""

    def __init__(self) -> None:
        # map_id → {(abs_x, abs_y): tile_char}
        self.tiles: Dict[int, Dict[Tuple[int, int], str]] = {}
        # map_id → {(abs_x, abs_y): dest_name}
        self.warps: Dict[int, Dict[Tuple[int, int], str]] = {}
        # map_id → [(abs_x, abs_y), ...] — positions where cycling was detected
        self.dead_ends: Dict[int, List[Tuple[int, int]]] = {}
        # Map connectivity graph: map_id → {dest_map_id, ...}
        self.map_graph: Dict[int, Set[int]] = {}
        # Map ID → human name (populated from warp_data as maps are visited)
        self.map_names: Dict[int, str] = {}

    def update_graph(
        self,
        map_id: int,
        warp_data: Optional[Dict[str, Any]],
        last_map_id: Optional[int] = None,
    ) -> None:
        """Record map connectivity edges without stamping tiles.

        Safe to call on map-change turns (warp/connection data is from RAM,
        not the screen grid which may still be transitioning).

        Args:
            map_id: Current map ID.
            warp_data: Warp/connection data from spatial_context.
            last_map_id: Previous map ID, used to resolve 0xFF ("outside /
                last map") warps into real map IDs.
        """
        if not warp_data:
            return
        if "map_name" in warp_data:
            self.map_names[map_id] = warp_data["map_name"]
        if map_id not in self.map_graph:
            self.map_graph[map_id] = set()
        for w in warp_data.get("warps", []):
            dest_mid = w.get("dest_map")
            # 0xFF = "outside (last map)" — resolve to actual map ID
            if dest_mid == 0xFF and last_map_id is not None:
                dest_mid = last_map_id
            if dest_mid is not None and dest_mid != 0xFF:
                self.map_graph[map_id].add(dest_mid)
                if dest_mid not in self.map_graph:
                    self.map_graph[dest_mid] = set()
                self.map_graph[dest_mid].add(map_id)
                if dest_mid not in self.map_names and w.get("dest_name"):
                    self.map_names[dest_mid] = w["dest_name"]
        for conn in warp_data.get("connections", []):
            dest_mid = conn.get("dest_map")
            if dest_mid is not None and dest_mid != 0xFF:
                self.map_graph[map_id].add(dest_mid)
                if dest_mid not in self.map_graph:
                    self.map_graph[dest_mid] = set()
                self.map_graph[dest_mid].add(map_id)
                if dest_mid not in self.map_names:
                    self.map_names[dest_mid] = conn.get("dest_name", "?")

    def update(
        self,
        map_id: int,
        player_pos: Tuple[int, int],
        player_screen_pos: Tuple[int, int],
        grid: List[List[str]],
        warp_data: Optional[Dict[str, Any]] = None,
        last_map_id: Optional[int] = None,
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

        # The player's own tile shows as '@' in the overlay grid and is never
        # stamped by the loop above.  Ensure the player's current map position
        # is recorded as walkable so cycling detection and dead-end markers are
        # consistent with the world map display.
        if (px_map, py_map) not in tile_map:
            tile_map[(px_map, py_map)] = "."

        # Record warps with destination names.
        # Rebuild from scratch each update to avoid stale duplicates.
        if warp_data:
            self.warps[map_id] = {}
            warp_map = self.warps[map_id]
            mw = warp_data.get("map_width", 0)
            mh = warp_data.get("map_height", 0)

            # Record graph edges (shared with update_graph)
            self.update_graph(map_id, warp_data, last_map_id=last_map_id)

            for w in warp_data.get("warps", []):
                warp_map[(w["map_x"], w["map_y"])] = w.get("dest_name", "?")

            # Record map connections as edge-tile warps.
            # Connections = walk off the map edge to reach adjacent map.
            # We synthesize warp entries on walkable edge tiles so A* can
            # path to them just like regular warps.
            for conn in warp_data.get("connections", []):
                dest = conn.get("dest_name", "?")
                d = conn.get("direction")
                # Map dims are in blocks, coords are in steps (2 per block)
                if d == "SOUTH":
                    edge_y = mh * 2 - 1
                    for ex in range(mw * 2):
                        if tile_map.get((ex, edge_y)) in (".", ","):
                            warp_map.setdefault((ex, edge_y), dest)
                elif d == "NORTH":
                    for ex in range(mw * 2):
                        if tile_map.get((ex, 0)) in (".", ","):
                            warp_map.setdefault((ex, 0), dest)
                elif d == "WEST":
                    for ey in range(mh * 2):
                        if tile_map.get((0, ey)) in (".", ","):
                            warp_map.setdefault((0, ey), dest)
                elif d == "EAST":
                    edge_x = mw * 2 - 1
                    for ey in range(mh * 2):
                        if tile_map.get((edge_x, ey)) in (".", ","):
                            warp_map.setdefault((edge_x, ey), dest)

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
            "dead_ends": {
                str(mid): [[x, y] for x, y in zones]
                for mid, zones in self.dead_ends.items()
                if zones
            },
            "map_graph": {
                str(mid): sorted(neighbors)
                for mid, neighbors in self.map_graph.items()
            },
            "map_names": {
                str(mid): name
                for mid, name in self.map_names.items()
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
            for mid_str, zones in data.get("dead_ends", {}).items():
                mid = int(mid_str)
                self.dead_ends[mid] = [tuple(z) for z in zones]
            for mid_str, neighbors in data.get("map_graph", {}).items():
                mid = int(mid_str)
                if mid not in self.map_graph:
                    self.map_graph[mid] = set()
                self.map_graph[mid].update(neighbors)
            for mid_str, name in data.get("map_names", {}).items():
                mid = int(mid_str)
                if mid not in self.map_names:
                    self.map_names[mid] = name
            logger.info(
                f"WorldMap loaded: {sum(len(t) for t in self.tiles.values())} tiles "
                f"across {len(self.tiles)} maps, {len(self.map_graph)} graph nodes"
            )
        except Exception as e:
            logger.warning(f"WorldMap load failed: {e}")

    def render(
        self,
        map_id: int,
        player_pos: Tuple[int, int],
        dead_end_zones: Optional[List[Tuple[int, int]]] = None,
        max_size: Optional[int] = None,
    ) -> Optional[str]:
        """Render the explored map for the given map ID.

        dead_end_zones: list of (x, y) centre points that were detected as
            stuck/dead-end areas. Shown as 'X' on the map so Claude can see
            visited traps relative to unexplored ('?') territory.
        max_size: override max rendered dimension (default _MAX_RENDER_SIZE).

        Returns None if fewer than 15 tiles explored (viewport already covers it).
        """
        tile_map = self.tiles.get(map_id)
        if not tile_map or len(tile_map) < 15:
            return None

        render_size = max_size if max_size is not None else _MAX_RENDER_SIZE

        # Build a set of tiles within radius 2 of any dead-end zone centre
        dead_end_tiles: set = set()
        if dead_end_zones:
            for dz_x, dz_y in dead_end_zones:
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        dead_end_tiles.add((dz_x + dx, dz_y + dy))

        # Crop to explored bounds (+ 1 tile margin), capped at max render size.
        # Small maps (gates, houses) shrink to fit; large maps (forest, routes)
        # center on the player within the max window.
        px, py = player_pos
        warp_map = self.warps.get(map_id, {})
        all_positions = set(tile_map.keys()) | set(warp_map.keys()) | {(px, py)}
        exp_min_x = min(p[0] for p in all_positions) - 1
        exp_max_x = max(p[0] for p in all_positions) + 1
        exp_min_y = min(p[1] for p in all_positions) - 1
        exp_max_y = max(p[1] for p in all_positions) + 1

        half = render_size // 2
        min_x = max(exp_min_x, px - half)
        max_x = min(exp_max_x, px + half)
        min_y = max(exp_min_y, py - half)
        max_y = min(exp_max_y, py + half)
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

    def find_path_to(
        self,
        map_id: int,
        start: Tuple[int, int],
        goal: Tuple[int, int],
        max_steps: int = _MAX_PATH_STEPS,
        blocked: Optional[Set[Tuple[int, int]]] = None,
    ) -> Optional[List[Tuple[int, int]]]:
        """A* pathfinding on the accumulated tile map.

        Unlike viewport A* (10x9 grid), this uses ALL explored tiles for the
        given map — hundreds or thousands of tiles — enabling multi-screen
        maze navigation.

        Args:
            map_id: Map ID to pathfind on.
            start: (x, y) absolute map position of player.
            goal: (x, y) absolute map position of target.
            max_steps: Truncate path after this many steps (avoids huge outputs).
            blocked: Extra positions to treat as impassable (e.g. NPC tiles).

        Returns:
            List of (x, y) positions from start to goal (or truncated), or None.
        """
        tile_map = self.tiles.get(map_id)
        if not tile_map:
            return None
        if start == goal:
            return [start]
        _extra_blocked = blocked or set()
        warp_map = self.warps.get(map_id, {})
        # Goal must be in explored territory OR be a known warp.
        # Warps are ROM-sourced and added to warp_map even if the player has
        # never rendered that exact tile into tile_map (it shows as 'W' in the
        # explored-map display but tile_map has no entry).  _passable() already
        # grants the goal unconditional passage, so A* can reach it from its
        # explored neighbours regardless.
        if goal not in tile_map and goal not in warp_map:
            return None

        def _passable(x: int, y: int, dx: int, dy: int) -> bool:
            if (x, y) == goal or (x, y) == start:
                return True  # player is standing here / goal always reachable
            if (x, y) in _extra_blocked:
                return False  # NPC or other temporary obstacle
            if (x, y) in warp_map:
                return False  # stepping on a non-goal warp tile would teleport us
            ch = tile_map.get((x, y))
            if ch is None:
                # Unexplored tile — treat as tentatively passable if ANY
                # orthogonal neighbour is a known walkable tile.  This bridges
                # 1-tile exploration gaps that break A* connectivity in mazes
                # like Mt. Moon where corridors are never fully walked.
                for nx, ny in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                    nch = tile_map.get((nx, ny))
                    if nch is not None and nch not in _BLOCKED_TILES:
                        return True
                return False
            if ch in LEDGE_ALLOWED_DIR:
                return (dx, dy) == LEDGE_ALLOWED_DIR[ch]
            return ch not in _BLOCKED_TILES

        def _h(x: int, y: int) -> int:
            return abs(goal[0] - x) + abs(goal[1] - y)

        counter = 0
        open_heap: list = [(_h(*start), counter, start)]
        counter += 1
        g_score: Dict[Tuple[int, int], int] = {start: 0}
        came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}
        closed: Set[Tuple[int, int]] = set()

        while open_heap:
            _, _, current = heapq.heappop(open_heap)
            if current in closed:
                continue
            if current == goal:
                path = [current]
                while current in came_from:
                    current = came_from[current]
                    path.append(current)
                path.reverse()
                # Truncate long paths — agent re-evaluates each turn anyway
                if len(path) > max_steps:
                    path = path[: max_steps + 1]
                return path
            closed.add(current)
            cx, cy = current
            g_cur = g_score[current]

            for dx, dy in NEIGHBORS:
                nx, ny = cx + dx, cy + dy
                if (nx, ny) in closed:
                    continue
                if not _passable(nx, ny, dx, dy):
                    continue
                tentative_g = g_cur + 1
                if tentative_g < g_score.get((nx, ny), float("inf")):
                    g_score[(nx, ny)] = tentative_g
                    came_from[(nx, ny)] = current
                    heapq.heappush(
                        open_heap, (tentative_g + _h(nx, ny), counter, (nx, ny))
                    )
                    counter += 1

        return None

    def find_frontier_path(
        self,
        map_id: int,
        start: Tuple[int, int],
        preferred_direction: Optional[str] = None,
        dead_end_tiles: Optional[Set[Tuple[int, int]]] = None,
        blocked: Optional[Set[Tuple[int, int]]] = None,
        max_steps: int = _MAX_PATH_STEPS,
    ) -> Optional[List[Tuple[int, int]]]:
        """Find path to the nearest frontier tile (walkable with unexplored neighbor).

        Frontier tiles in the *preferred_direction* from start are prioritised
        via a heuristic bonus.  Dead-end tiles are penalised heavily so the
        path avoids known traps.

        Returns path list or None.
        """
        tile_map = self.tiles.get(map_id)
        if not tile_map or len(tile_map) < 30:
            return None

        _WALKABLE = frozenset(".,")
        _dead = dead_end_tiles or set()
        _extra_blocked = blocked or set()
        warp_map = self.warps.get(map_id, {})

        # Precompute frontier set: walkable tiles with ≥1 unexplored neighbor
        frontiers: Set[Tuple[int, int]] = set()
        for (tx, ty), ch in tile_map.items():
            if ch not in _WALKABLE:
                continue
            if (tx, ty) in _dead:
                continue
            if any((tx + ox, ty + oy) not in tile_map for ox, oy in NEIGHBORS):
                frontiers.add((tx, ty))

        if not frontiers:
            return None

        # Direction bias: prefer frontiers in the compass direction of the goal
        _DIR_BIAS = {"NORTH": (0, -1), "SOUTH": (0, 1), "WEST": (-1, 0), "EAST": (1, 0)}
        bias_dx, bias_dy = _DIR_BIAS.get(preferred_direction or "", (0, 0))

        def _h(x: int, y: int) -> int:
            """Heuristic: distance to nearest frontier, biased by direction."""
            # Bias: subtract a bonus for tiles in the preferred direction
            bonus = 0
            if bias_dx or bias_dy:
                dx = x - start[0]
                dy = y - start[1]
                bonus = dx * bias_dx + dy * bias_dy  # positive = in preferred dir
            return -min(bonus, 10)  # cap so it doesn't dominate

        def _passable(x: int, y: int, dx: int, dy: int) -> bool:
            if (x, y) == start:
                return True
            if (x, y) in _extra_blocked:
                return False
            if (x, y) in warp_map:
                return False  # stepping on a non-start warp tile would teleport us
            ch = tile_map.get((x, y))
            if ch is None:
                return False
            if ch in LEDGE_ALLOWED_DIR:
                return (dx, dy) == LEDGE_ALLOWED_DIR[ch]
            return ch not in _BLOCKED_TILES

        counter = 0
        open_heap: list = [(_h(*start), counter, start)]
        counter += 1
        g_score: Dict[Tuple[int, int], int] = {start: 0}
        came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}
        closed: Set[Tuple[int, int]] = set()

        while open_heap:
            _, _, current = heapq.heappop(open_heap)
            if current in closed:
                continue
            if current in frontiers and current != start:
                # Found a frontier — reconstruct path
                path = [current]
                while current in came_from:
                    current = came_from[current]
                    path.append(current)
                path.reverse()
                if len(path) > max_steps:
                    path = path[: max_steps + 1]
                return path
            closed.add(current)
            cx, cy = current
            g_cur = g_score[current]

            for ddx, ddy in NEIGHBORS:
                nx, ny = cx + ddx, cy + ddy
                if (nx, ny) in closed:
                    continue
                if not _passable(nx, ny, ddx, ddy):
                    continue
                # Penalise dead-end tiles so path avoids them
                cost = 1 + (5 if (nx, ny) in _dead else 0)
                tentative_g = g_cur + cost
                if tentative_g < g_score.get((nx, ny), float("inf")):
                    g_score[(nx, ny)] = tentative_g
                    came_from[(nx, ny)] = current
                    heapq.heappush(
                        open_heap, (tentative_g + _h(nx, ny), counter, (nx, ny))
                    )
                    counter += 1

        return None

    def find_nav_hint(
        self,
        map_id: int,
        player_pos: Tuple[int, int],
        preferred_dest: Optional[str] = None,
        preferred_direction: Optional[str] = None,
        dead_end_zones: Optional[List[Tuple[int, int]]] = None,
        npc_positions: Optional[List[Tuple[int, int]]] = None,
        max_steps: int = _MAX_PATH_STEPS,
    ) -> Optional[str]:
        """Find A* path from player to a known warp, or nearest frontier.

        If *preferred_dest* is given (substring match on warp name), only
        warps matching it are tried.  If no warp is reachable, falls back
        to the nearest unexplored frontier in *preferred_direction*, avoiding
        *dead_end_zones*.  *npc_positions* are treated as temporary obstacles.

        Returns a NAV hint string with button commands, or None.
        """
        warp_map = self.warps.get(map_id)
        tile_map = self.tiles.get(map_id)
        if not tile_map or len(tile_map) < 30:
            return None

        # Build dead-end tile set for avoidance
        dead_end_tiles: Set[Tuple[int, int]] = set()
        if dead_end_zones:
            for dz_x, dz_y in dead_end_zones:
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        dead_end_tiles.add((dz_x + dx, dz_y + dy))

        # NPC positions as temporary obstacles
        npc_blocked: Set[Tuple[int, int]] = set(npc_positions) if npc_positions else set()

        # Try warps first
        best_path: Optional[List[Tuple[int, int]]] = None
        best_name: Optional[str] = None
        best_len = float("inf")

        _DIR_VEC = {"NORTH": (0, -1), "SOUTH": (0, 1),
                    "EAST": (1, 0), "WEST": (-1, 0)}

        if warp_map:
            # Split warps into preferred and other sets
            preferred_warps: List[Tuple[Tuple[int, int], str]] = []
            other_warps: List[Tuple[Tuple[int, int], str]] = []
            for warp_pos, dest_name in warp_map.items():
                if preferred_dest and preferred_dest.lower() in dest_name.lower():
                    preferred_warps.append((warp_pos, dest_name))
                else:
                    other_warps.append((warp_pos, dest_name))

            # Try preferred warps, but skip any that lie in the opposite
            # direction from preferred_direction (prevents e.g. pathing to
            # Pewter City WEST when the goal says EAST).
            for warp_pos, dest_name in preferred_warps:
                if preferred_direction:
                    dvx, dvy = _DIR_VEC.get(preferred_direction, (0, 0))
                    score = (warp_pos[0] - player_pos[0]) * dvx + (warp_pos[1] - player_pos[1]) * dvy
                    if score < 0:
                        continue  # warp is behind us relative to goal direction
                path = self.find_path_to(map_id, player_pos, warp_pos, max_steps=200, blocked=npc_blocked)
                if path and len(path) < best_len:
                    best_path = path
                    best_name = dest_name
                    best_len = len(path)

            # If preferred_dest matched no warps, try warps that lie in the
            # preferred_direction from the player (furthest first so the
            # agent makes maximal forward progress).
            if not best_path and preferred_direction:
                dvx, dvy = _DIR_VEC.get(preferred_direction, (0, 0))
                px, py = player_pos
                directional_warps = []
                for warp_pos, dest_name in other_warps:
                    score = (warp_pos[0] - px) * dvx + (warp_pos[1] - py) * dvy
                    if score > 0:
                        directional_warps.append((score, warp_pos, dest_name))
                directional_warps.sort(reverse=True)
                for _, warp_pos, dest_name in directional_warps:
                    path = self.find_path_to(map_id, player_pos, warp_pos, max_steps=200, blocked=npc_blocked)
                    if path:
                        best_path = path
                        best_name = dest_name
                        best_len = len(path)
                        break

            # Fall back to any reachable warp when no preferred_dest given.
            if not best_path and not preferred_dest:
                for warp_pos, dest_name in other_warps:
                    path = self.find_path_to(map_id, player_pos, warp_pos, max_steps=200, blocked=npc_blocked)
                    if path and len(path) < best_len:
                        best_path = path
                        best_name = dest_name
                        best_len = len(path)

        # Fall back to frontier exploration if no warp reachable
        if not best_path:
            frontier_path = self.find_frontier_path(
                map_id, player_pos,
                preferred_direction=preferred_direction,
                dead_end_tiles=dead_end_tiles,
                blocked=npc_blocked,
                max_steps=max_steps,
            )
            if frontier_path:
                best_path = frontier_path
                best_name = "unexplored frontier"
                best_len = len(frontier_path)

        if not best_path or not best_name:
            return None

        # Truncate for output
        truncated = len(best_path) > max_steps + 1
        display_path = best_path[: max_steps + 1] if truncated else best_path
        buttons = self._path_to_buttons(display_path)
        if not buttons:
            return None

        total_dist = best_len - 1  # steps, not nodes
        suffix = f" (+{total_dist - max_steps} more)" if truncated else ""
        return (
            f"NAV(map): to {best_name} ({total_dist} tiles): "
            f"{buttons}{suffix} — re-evaluate after executing"
        )

    def find_map_path(
        self,
        src_map: int,
        dst_map: int,
        exclude_maps: Optional[Set[int]] = None,
    ) -> Optional[List[int]]:
        """BFS on the map connectivity graph.

        Args:
            src_map: Starting map ID.
            dst_map: Target map ID.
            exclude_maps: Map IDs to skip during BFS (for retry when
                the first-hop map is reachable in the graph but not
                via tile-level A* due to terrain like ledges).

        Returns list of map IDs from src to dst (inclusive), or None if
        no path exists in the explored graph.
        """
        if src_map == dst_map:
            return [src_map]
        if src_map not in self.map_graph:
            return None
        _exclude = exclude_maps or set()

        queue: deque = deque([(src_map, [src_map])])
        visited: Set[int] = {src_map}
        while queue:
            current, path = queue.popleft()
            for neighbor in self.map_graph.get(current, set()):
                if neighbor in visited:
                    continue
                # Excluded maps are only blocked as the first hop
                # (direct neighbor of src).  A* on the current map
                # couldn't reach their warps, but they may be
                # reachable through intermediate maps (e.g. Route 4
                # can't A* east to Cerulean due to ledges, but
                # Route 4 → Mt. Moon → … → Cerulean works).
                if neighbor in _exclude and len(path) == 1:
                    continue
                if neighbor == dst_map:
                    return path + [neighbor]
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))
        return None

    def next_map_toward(
        self,
        src_map: int,
        dst_map: int,
        exclude_maps: Optional[Set[int]] = None,
    ) -> Optional[str]:
        """Return the name of the next map to visit on the way to dst_map.

        Uses BFS on the map connectivity graph.  Returns None if no path
        exists or src == dst.
        """
        path = self.find_map_path(src_map, dst_map, exclude_maps=exclude_maps)
        if not path or len(path) < 2:
            return None
        next_id = path[1]
        return self.map_names.get(next_id)

    @staticmethod
    def _path_to_buttons(
        path: List[Tuple[int, int]],
        frames_per_tile: int = 16,
        max_single: int = 128,
        max_total: int = 256,
    ) -> str:
        """Convert (x,y) path to button string, respecting frame caps.

        max_single: max frames per command token (128 = 8 tiles).
        max_total: max total directional frames per turn (256).
        """
        if len(path) < 2:
            return ""
        # Collect (direction, tile_count) segments
        segments: List[Tuple[str, int]] = []
        cur_dir: Optional[str] = None
        cur_count = 0
        for i in range(1, len(path)):
            dx = path[i][0] - path[i - 1][0]
            dy = path[i][1] - path[i - 1][1]
            btn = DIR_BUTTONS.get((dx, dy))
            if btn is None:
                continue
            if btn == cur_dir:
                cur_count += 1
            else:
                if cur_dir is not None:
                    segments.append((cur_dir, cur_count))
                cur_dir = btn
                cur_count = 1
        if cur_dir is not None:
            segments.append((cur_dir, cur_count))

        # Emit commands respecting per-token and total frame caps
        commands: List[str] = []
        total_frames = 0
        max_tiles_per_cmd = max_single // frames_per_tile  # 8
        for btn, tiles in segments:
            remaining_tiles = tiles
            while remaining_tiles > 0 and total_frames < max_total:
                chunk = min(remaining_tiles, max_tiles_per_cmd)
                chunk_frames = chunk * frames_per_tile
                if total_frames + chunk_frames > max_total:
                    chunk = (max_total - total_frames) // frames_per_tile
                    if chunk <= 0:
                        break
                    chunk_frames = chunk * frames_per_tile
                commands.append(f"{btn}{chunk_frames}")
                total_frames += chunk_frames
                remaining_tiles -= chunk
            if total_frames >= max_total:
                break
        return " ".join(commands)

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
