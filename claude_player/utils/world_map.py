"""Persistent per-map tile accumulator.

Stitches together the 10x9 viewport grid each turn into a full explored map
using absolute map coordinates.  Only static terrain is stored (no NPCs/items).
"""

from __future__ import annotations

import heapq
import json
import logging
import os
import random
from collections import deque
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Tile types worth stamping (static terrain only — NPCs/ghosts excluded)
# Items (i) and objects (o) are included: items self-heal when collected
# (next visit overwrites 'i' with '.'), objects are stationary.
_STATIC_TILES = frozenset(".:#,=v><TBio")  # W excluded: warp positions tracked in warp_map, rebuilt each turn

# Impassable tiles for world-map A*
_BLOCKED_TILES: FrozenSet[str] = frozenset("#=TBWio")

from claude_player.utils.pathfinding import LEDGE_ALLOWED_DIR, NEIGHBORS, DIR_BUTTONS

# Max rendered dimension before we crop around the player
_MAX_RENDER_SIZE = 40       # AI context (full exploration visible)
_MAX_DISPLAY_SIZE = 20     # Web/terminal display — large enough to show full explored maps (panel scrolls)

# Max steps in a world-map A* path before we truncate.
# Kept near viewport range so the agent re-evaluates frequently
# rather than blindly walking through unseen territory.
_MAX_PATH_STEPS = 20

# Warp exhaustion: how many turns before an exhausted warp becomes eligible again
_WARP_EXHAUST_DECAY_TURNS = 30

# Max agent-placed markers per map (keeps legend readable)
MAX_MARKERS_PER_MAP = 8


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
        # Warp cycling detection: recent warp transitions
        self._warp_transitions: deque = deque(maxlen=20)
        # map_id → {(warp_x, warp_y): turn_exhausted} — soft-deprioritized warps
        self._exhausted_warps: Dict[int, Dict[Tuple[int, int], int]] = {}
        # Active cycling pairs: map_id → {set of map_ids currently cycling with it}
        # Populated by record_warp_transition, decays with exhausted warps.
        self._cycling_maps: Dict[int, Set[int]] = {}
        # Verified route cache: map_id → {dest_name → [(x,y), ...]}
        # Routes cached when A* path successfully leads to a warp (confirmed
        # by map transition).  Used as a shortcut on future visits.
        self.route_cache: Dict[int, Dict[str, List[Tuple[int, int]]]] = {}
        # Pending route: set when NAV computes a path to a warp, cleared on
        # map transition (success → cache) or on failed movement (discard).
        self._pending_route: Optional[Tuple[int, str, List[Tuple[int, int]]]] = None  # (map_id, dest_name, path)
        # map_id → set of ((x1,y1),(x2,y2)) edges blocked by tile pair collisions
        # (e.g. cave elevation boundaries).  Accumulated from viewport data.
        self.pair_blocked_edges: Dict[int, Set[Tuple[Tuple[int, int], Tuple[int, int]]]] = {}
        # Agent-placed map markers: map_id → {(abs_x, abs_y): label}
        # Persistent POI annotations rendered on the expanded world map as '*'.
        self.markers: Dict[int, Dict[Tuple[int, int], str]] = {}

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
                # Use base (unqualified) name for map_names — override
                # names like "Mt. Moon B1F (east exit)" must not pollute
                # the canonical map name used by BFS target matching.
                canonical = w.get("dest_base_name") or w.get("dest_name")
                if dest_mid not in self.map_names and canonical:
                    self.map_names[dest_mid] = canonical
        for conn in warp_data.get("connections", []):
            dest_mid = conn.get("dest_map")
            if dest_mid is not None and dest_mid != 0xFF:
                self.map_graph[map_id].add(dest_mid)
                if dest_mid not in self.map_graph:
                    self.map_graph[dest_mid] = set()
                self.map_graph[dest_mid].add(map_id)
                if dest_mid not in self.map_names:
                    self.map_names[dest_mid] = conn.get("dest_name", "?")

    def ensure_graph_edge(self, map_a: int, map_b: int) -> None:
        """Ensure a bidirectional edge exists between two maps in the graph.

        Called on any map transition (warp, connection, or walk-off) to
        guarantee the graph captures all connectivity, even when warp_data
        was unavailable.
        """
        if map_a not in self.map_graph:
            self.map_graph[map_a] = set()
        if map_b not in self.map_graph:
            self.map_graph[map_b] = set()
        self.map_graph[map_a].add(map_b)
        self.map_graph[map_b].add(map_a)

    def record_warp_transition(
        self,
        from_map: int,
        player_pos: Tuple[int, int],
        to_map: int,
        turn: int,
        arrival_pos: Optional[Tuple[int, int]] = None,
    ) -> None:
        """Record a warp/map transition and detect ping-pong cycling.

        Identifies the closest warp on from_map that leads to to_map,
        records the transition, and marks warps as exhausted when
        back-and-forth cycling is detected.  Also auto-exhausts the
        arrival warp on the new map so NAV doesn't route back to it.

        Args:
            from_map: Map ID the player just left.
            player_pos: Player's last known position on from_map.
            to_map: Map ID the player just arrived on.
            turn: Current turn number (for decay tracking).
            arrival_pos: Player's position on the new map (to_map).
                If provided, the closest warp on to_map leading back
                to from_map is auto-exhausted to prevent immediate
                backtracking.
        """
        # Find the warp on from_map closest to player_pos that leads to to_map
        warp_map = self.warps.get(from_map, {})
        to_map_name = self.map_names.get(to_map, "")
        best_warp_pos = None
        best_dist = float("inf")
        for warp_pos, dest_name in warp_map.items():
            if to_map_name and to_map_name.lower() not in dest_name.lower():
                continue
            dist = abs(warp_pos[0] - player_pos[0]) + abs(warp_pos[1] - player_pos[1])
            if dist < best_dist:
                best_dist = dist
                best_warp_pos = warp_pos
        if best_warp_pos is None:
            # Couldn't identify which warp — use player position as proxy
            best_warp_pos = player_pos

        self._warp_transitions.append((from_map, best_warp_pos, to_map))
        logger.info(
            f"WARP TRANSITION: map 0x{from_map:02X} warp={best_warp_pos} "
            f"→ map 0x{to_map:02X} (history={len(self._warp_transitions)})"
        )

        # Detect ping-pong: count (A→B) and (B→A) pairs in recent history
        pair_warps: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
        for fm, wp, tm in self._warp_transitions:
            pair_warps.setdefault((fm, tm), []).append(wp)

        for (a, b), a_warps in pair_warps.items():
            if a == b:
                continue  # intra-map warp (e.g. stairs within same floor), not cycling
            if (b, a) not in pair_warps:
                continue
            b_warps = pair_warps[(b, a)]
            # Need at least 2 full round-trips (3+2 transitions) to confirm cycling
            if len(a_warps) < 3 or len(b_warps) < 2:
                continue
            # Mark the specific warps used as exhausted — don't refresh
            # already-exhausted warps so they can decay naturally
            for wp in a_warps:
                self._exhausted_warps.setdefault(a, {}).setdefault(wp, turn)
            for wp in b_warps:
                self._exhausted_warps.setdefault(b, {}).setdefault(wp, turn)
            logger.info(
                f"WARP CYCLING detected: 0x{a:02X}↔0x{b:02X} — "
                f"exhausted {len(a_warps)} warps on 0x{a:02X}, "
                f"{len(b_warps)} warps on 0x{b:02X}"
            )
            # Track cycling relationship so NAV can skip these hops
            self._cycling_maps.setdefault(a, set()).add(b)
            self._cycling_maps.setdefault(b, set()).add(a)
            # Invalidate route cache for cycling maps — but skip from_map
            # since it was just confirmed as a working route this transition
            for mid in (a, b):
                if mid == from_map:
                    continue  # just confirmed; don't invalidate
                if mid in self.route_cache:
                    logger.info(f"ROUTE CACHE INVALIDATED: map 0x{mid:02X} (cycling)")
                    del self.route_cache[mid]

        # Auto-exhaust the arrival warp on the new map so NAV doesn't
        # immediately route back through the door we just entered from.
        if arrival_pos is not None and from_map != to_map:
            arrival_warp_map = self.warps.get(to_map, {})
            from_map_name = self.map_names.get(from_map, "")
            best_arrival_warp = None
            best_arrival_dist = float("inf")
            for warp_pos, dest_name in arrival_warp_map.items():
                if from_map_name and from_map_name.lower() not in dest_name.lower():
                    continue
                dist = abs(warp_pos[0] - arrival_pos[0]) + abs(warp_pos[1] - arrival_pos[1])
                if dist < best_arrival_dist:
                    best_arrival_dist = dist
                    best_arrival_warp = warp_pos
            if best_arrival_warp is not None and best_arrival_dist <= 2:
                self._exhausted_warps.setdefault(to_map, {})[best_arrival_warp] = turn
                logger.info(
                    f"ARRIVAL WARP EXHAUSTED: map 0x{to_map:02X} "
                    f"warp={best_arrival_warp} (entry from 0x{from_map:02X})"
                )

    def get_active_exhausted_warps(
        self,
        map_id: int,
        current_turn: int,
    ) -> Set[Tuple[int, int]]:
        """Return exhausted warp positions for map_id that haven't decayed.

        Args:
            map_id: Map to check.
            current_turn: Current turn number for decay calculation.

        Returns:
            Set of (x, y) warp positions still within the exhaustion window.
        """
        exhausted = self._exhausted_warps.get(map_id, {})
        if not exhausted:
            return set()
        return {
            pos for pos, exhaust_turn in exhausted.items()
            if current_turn - exhaust_turn < _WARP_EXHAUST_DECAY_TURNS
        }

    def get_cycling_maps(self, map_id: int, current_turn: int) -> Set[int]:
        """Return set of map IDs currently in a cycling relationship with map_id.

        Cycling decays when all exhausted warps between the pair have decayed.
        """
        cycling = self._cycling_maps.get(map_id, set())
        if not cycling:
            return set()
        # Only return maps where exhausted warps are still active
        active: Set[int] = set()
        for other_id in cycling:
            if self.get_active_exhausted_warps(map_id, current_turn) or \
               self.get_active_exhausted_warps(other_id, current_turn):
                active.add(other_id)
        return active

    def get_cross_map_stuck_warning(self) -> Optional[str]:
        """Detect cross-map looping from warp transition history.

        Scans the last 20 warp transitions for repetitive map pairs.
        If the same two maps account for 6+ transitions (3 round-trips)
        and no *new* map has been visited in the last 8 transitions,
        returns a warning string for injection into turn context.

        Returns:
            Warning string if cross-map looping detected, else None.
        """
        transitions = self._warp_transitions
        if len(transitions) < 6:
            return None

        # Count transitions per directed map pair
        pair_counts: Dict[Tuple[int, int], int] = {}
        for from_map, _, to_map in transitions:
            pair_counts[(from_map, to_map)] = pair_counts.get((from_map, to_map), 0) + 1

        # Find the most repeated undirected pair (A→B + B→A)
        seen_pairs: set = set()
        worst_pair = None
        worst_count = 0
        for (a, b), count_ab in pair_counts.items():
            if a == b:
                continue
            key = (min(a, b), max(a, b))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            count_ba = pair_counts.get((b, a), 0)
            total = count_ab + count_ba
            if total > worst_count:
                worst_count = total
                worst_pair = (a, b)

        if worst_pair is None or worst_count < 6:
            return None

        # Check variety in recent transitions — if the agent is visiting
        # 4+ distinct maps in the last 8 warps, it's exploring, not looping
        recent = list(transitions)[-8:]
        recent_maps = {fm for fm, _, _ in recent} | {tm for _, _, tm in recent}
        if len(recent_maps) > 3:
            return None  # visiting variety of maps, not stuck

        a, b = worst_pair
        name_a = self.map_names.get(a, f"0x{a:02X}")
        name_b = self.map_names.get(b, f"0x{b:02X}")
        return (
            f"CROSS-MAP LOOP: You have warped between {name_a} and {name_b} "
            f"{worst_count} times without progress. The warps you are using "
            f"may lead to dead-end sections. Try a DIFFERENT warp, or explore "
            f"unexplored areas on the current map before warping again."
        )

    def set_pending_route(
        self,
        map_id: int,
        dest_name: str,
        path: List[Tuple[int, int]],
    ) -> None:
        """Record a NAV path as pending verification.

        Called when find_nav_hint computes a path to a named warp.
        If the player subsequently transitions to a new map, the route
        is confirmed and cached.  Only paths ≥5 tiles are worth caching.
        """
        if len(path) < 5:
            return
        self._pending_route = (map_id, dest_name, path)

    def confirm_route(self, from_map: int) -> None:
        """Confirm and cache the pending route after a successful map transition.

        Called when the player warps/transitions to a new map.  If the
        pending route's map_id matches from_map, the route is cached
        as verified.
        """
        if self._pending_route is None:
            return
        p_map_id, dest_name, path = self._pending_route
        self._pending_route = None
        if p_map_id != from_map:
            return
        # Cache the route (overwrite any previous route to same dest)
        if p_map_id not in self.route_cache:
            self.route_cache[p_map_id] = {}
        self.route_cache[p_map_id][dest_name] = path
        logger.info(
            f"ROUTE CACHED: map 0x{p_map_id:02X} → {dest_name} "
            f"({len(path)} tiles)"
        )

    def discard_pending_route(self) -> None:
        """Discard the pending route (e.g. player got stuck, didn't reach warp)."""
        self._pending_route = None

    def get_cached_route(
        self,
        map_id: int,
        dest_name: str,
        player_pos: Tuple[int, int],
        max_splice_dist: int = 3,
    ) -> Optional[List[Tuple[int, int]]]:
        """Retrieve a cached route, splicing from the nearest point to player.

        Args:
            map_id: Current map ID.
            dest_name: Warp destination name to look up.
            player_pos: Current player position.
            max_splice_dist: Max Manhattan distance from player to any point
                on the cached route for it to be usable.

        Returns:
            Sub-path from the nearest cached waypoint to the destination,
            or None if no cache hit or player is too far from the route.
        """
        routes = self.route_cache.get(map_id, {})
        if dest_name not in routes:
            return None
        cached_path = routes[dest_name]
        # Find the closest point on the cached path to the player
        best_idx = None
        best_dist = float("inf")
        for i, (cx, cy) in enumerate(cached_path):
            dist = abs(cx - player_pos[0]) + abs(cy - player_pos[1])
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        if best_idx is None or best_dist > max_splice_dist:
            return None
        # Return from the nearest point onward
        spliced = cached_path[best_idx:]
        if len(spliced) < 2:
            return None
        logger.info(
            f"ROUTE CACHE HIT: map 0x{map_id:02X} → {dest_name} "
            f"spliced from idx {best_idx} ({len(spliced)} tiles remaining, "
            f"player dist={best_dist})"
        )
        return spliced

    def update(
        self,
        map_id: int,
        player_pos: Tuple[int, int],
        player_screen_pos: Tuple[int, int],
        grid: List[List[str]],
        warp_data: Optional[Dict[str, Any]] = None,
        last_map_id: Optional[int] = None,
        pair_blocked: Optional[Set[Tuple[Tuple[int, int], Tuple[int, int]]]] = None,
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

        # Stamp tile pair collision edges (absolute coords)
        if pair_blocked:
            if map_id not in self.pair_blocked_edges:
                self.pair_blocked_edges[map_id] = set()
            self.pair_blocked_edges[map_id].update(pair_blocked)

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
                        if tile_map.get((ex, edge_y)) in (".", ",", ":"):
                            warp_map.setdefault((ex, edge_y), dest)
                elif d == "NORTH":
                    for ex in range(mw * 2):
                        if tile_map.get((ex, 0)) in (".", ",", ":"):
                            warp_map.setdefault((ex, 0), dest)
                elif d == "WEST":
                    for ey in range(mh * 2):
                        if tile_map.get((0, ey)) in (".", ",", ":"):
                            warp_map.setdefault((0, ey), dest)
                elif d == "EAST":
                    edge_x = mw * 2 - 1
                    for ey in range(mh * 2):
                        if tile_map.get((edge_x, ey)) in (".", ",", ":"):
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
            "route_cache": {
                str(mid): {
                    dest: [[x, y] for x, y in path]
                    for dest, path in routes.items()
                }
                for mid, routes in self.route_cache.items()
                if routes
            },
            "pair_blocked_edges": {
                str(mid): [[list(e[0]), list(e[1])] for e in edges]
                for mid, edges in self.pair_blocked_edges.items()
                if edges
            },
            "exhausted_warps": {
                str(mid): {f"{x},{y}": turn for (x, y), turn in warps.items()}
                for mid, warps in self._exhausted_warps.items()
                if warps
            },
            "warp_transitions": [
                [fm, list(wp), tm] for fm, wp, tm in self._warp_transitions
            ],
            "markers": {
                str(mid): {f"{x},{y}": label for (x, y), label in marks.items()}
                for mid, marks in self.markers.items()
                if marks
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
            for mid_str, routes in data.get("route_cache", {}).items():
                mid = int(mid_str)
                if mid not in self.route_cache:
                    self.route_cache[mid] = {}
                for dest, coords in routes.items():
                    self.route_cache[mid][dest] = [tuple(c) for c in coords]
            for mid_str, edges in data.get("pair_blocked_edges", {}).items():
                mid = int(mid_str)
                if mid not in self.pair_blocked_edges:
                    self.pair_blocked_edges[mid] = set()
                for e in edges:
                    self.pair_blocked_edges[mid].add((tuple(e[0]), tuple(e[1])))
            for mid_str, warps in data.get("exhausted_warps", {}).items():
                mid = int(mid_str)
                for key, turn in warps.items():
                    x, y = map(int, key.split(","))
                    self._exhausted_warps.setdefault(mid, {})[(x, y)] = turn
            for entry in data.get("warp_transitions", []):
                fm, wp, tm = entry[0], tuple(entry[1]), entry[2]
                self._warp_transitions.append((fm, wp, tm))
            for mid_str, marks in data.get("markers", {}).items():
                mid = int(mid_str)
                if mid not in self.markers:
                    self.markers[mid] = {}
                for key, label in marks.items():
                    x, y = map(int, key.split(","))
                    self.markers[mid][(x, y)] = label
            logger.info(
                f"WorldMap loaded: {sum(len(t) for t in self.tiles.values())} tiles "
                f"across {len(self.tiles)} maps, {len(self.map_graph)} graph nodes, "
                f"{sum(len(w) for w in self._exhausted_warps.values())} exhausted warps"
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
        marker_map = self.markers.get(map_id, {})
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
                elif (x, y) in marker_map:
                    row_chars.append("*")
                elif (x, y) in tile_map:
                    ch = tile_map[(x, y)]
                    # Mark walkable dead-end tiles with X (walls stay as #)
                    if ch in (".", ",", ":") and (x, y) in dead_end_tiles:
                        row_chars.append("X")
                    else:
                        row_chars.append(ch)
                else:
                    row_chars.append("?")
            lines.append("".join(row_chars))

        # Append marker legend if any markers are within the rendered bounds
        visible_markers = {
            pos: label for pos, label in marker_map.items()
            if min_x <= pos[0] <= max_x and min_y <= pos[1] <= max_y
        }
        if visible_markers:
            lines.append("Markers (*):  " + " | ".join(
                f"({x},{y}): {label}" for (x, y), label in sorted(visible_markers.items())
            ))

        return "\n".join(lines)

    def find_path_to(
        self,
        map_id: int,
        start: Tuple[int, int],
        goal: Tuple[int, int],
        max_steps: int = _MAX_PATH_STEPS,
        blocked: Optional[Set[Tuple[int, int]]] = None,
        variance: int = 0,
    ) -> Optional[List[Tuple[int, int]]]:
        """A* pathfinding on the accumulated tile map.

        Unlike viewport A* (10x9 grid), this uses ALL explored tiles for the
        given map — hundreds or thousands of tiles — enabling multi-screen
        maze navigation.  Respects tile pair collision edges stored in
        pair_blocked_edges (e.g. cave elevation boundaries).

        Args:
            map_id: Map ID to pathfind on.
            start: (x, y) absolute map position of player.
            goal: (x, y) absolute map position of target.
            max_steps: Truncate path after this many steps (avoids huge outputs).
            blocked: Extra positions to treat as impassable (e.g. NPC tiles).
            variance: 0 = deterministic optimal path. 1-3 = increasing random
                cost jitter per tile, causing A* to explore alternate routes.
                Useful when the optimal path is repeatedly failing.

        Returns:
            List of (x, y) positions from start to goal (or truncated), or None.
        """
        tile_map = self.tiles.get(map_id)
        if not tile_map:
            return None
        if start == goal:
            return [start]
        _extra_blocked = blocked or set()
        _pair_bl = self.pair_blocked_edges.get(map_id, set())
        warp_map = self.warps.get(map_id, {})
        # Goal must be in explored territory OR be a known warp.
        # Warps are ROM-sourced and added to warp_map even if the player has
        # never rendered that exact tile into tile_map (it shows as 'W' in the
        # explored-map display but tile_map has no entry).  _passable() already
        # grants the goal unconditional passage, so A* can reach it from its
        # explored neighbours regardless.
        if goal not in tile_map and goal not in warp_map:
            return None

        # Pre-compute per-tile random costs for variance mode.
        # Using a dict seeded once per call ensures consistent costs within
        # a single A* expansion (admissibility preserved per-call) while
        # producing different paths across calls.
        _tile_jitter: Dict[Tuple[int, int], int] = {}
        if variance > 0:
            jitter_max = variance * 2  # variance 1→2, 2→4, 3→6
            for pos in tile_map:
                _tile_jitter[pos] = random.randint(0, jitter_max)

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
                if ((cx, cy), (nx, ny)) in _pair_bl:
                    continue  # tile pair collision (elevation boundary)
                if not _passable(nx, ny, dx, dy):
                    continue
                jitter = _tile_jitter.get((nx, ny), 0)
                tentative_g = g_cur + 1 + jitter
                if tentative_g < g_score.get((nx, ny), float("inf")):
                    g_score[(nx, ny)] = tentative_g
                    came_from[(nx, ny)] = current
                    heapq.heappush(
                        open_heap, (tentative_g + _h(nx, ny), counter, (nx, ny))
                    )
                    counter += 1

        return None

    def frontier_ratio(self, map_id: int) -> float:
        """Ratio of frontier tiles to total walkable tiles on this map.

        Frontier = walkable tile with at least one unexplored neighbor.
        Returns 0.0 if map has fewer than 15 explored tiles (too early to judge).
        High ratio (>0.3) means the map is barely explored.
        """
        tile_map = self.tiles.get(map_id)
        if not tile_map or len(tile_map) < 15:
            return 0.0
        _WALKABLE = frozenset(".,:")
        walkable = 0
        frontier = 0
        dead = set()
        for dz_x, dz_y in self.dead_ends.get(map_id, []):
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    dead.add((dz_x + dx, dz_y + dy))
        for (tx, ty), ch in tile_map.items():
            if ch not in _WALKABLE:
                continue
            walkable += 1
            if (tx, ty) in dead:
                continue
            if any((tx + ox, ty + oy) not in tile_map for ox, oy in NEIGHBORS):
                frontier += 1
        return frontier / walkable if walkable > 0 else 0.0

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

        _WALKABLE = frozenset(".,:")
        _dead = dead_end_tiles or set()
        _extra_blocked = blocked or set()
        _pair_bl = self.pair_blocked_edges.get(map_id, set())
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
                if ((cx, cy), (nx, ny)) in _pair_bl:
                    continue  # tile pair collision (elevation boundary)
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
        current_turn: int = 0,
        variance: int = 0,
    ) -> Optional[str]:
        """Find A* path from player to a known warp, or nearest frontier.

        If *preferred_dest* is given (substring match on warp name), only
        warps matching it are tried.  Exhausted warps (from ping-pong
        cycling detection) are deprioritized: non-exhausted warps are tried
        first, and exhausted warps are used only as a fallback to prevent
        getting stuck.  If no warp is reachable, falls back to the nearest
        unexplored frontier in *preferred_direction*, avoiding
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

        # Flag for callers to detect exhausted-warp fallback
        self._used_exhausted_warp = False

        # Exhausted warps: soft-deprioritize (try fresh warps first)
        exhausted = self.get_active_exhausted_warps(map_id, current_turn)

        # ── Route cache check ──
        # Before running A*, check if we have a verified route to the
        # preferred destination.  Only use when not in variance mode
        # (variance means the optimal path is failing, so don't reuse it).
        if preferred_dest and variance == 0:
            cached = self.get_cached_route(map_id, preferred_dest, player_pos)
            if cached:
                # Verify the cached path is still walkable (no new NPCs blocking)
                path_blocked = any(pos in npc_blocked for pos in cached)
                if not path_blocked:
                    self.set_pending_route(map_id, preferred_dest, cached)
                    buttons = self._path_to_buttons(cached)
                    if buttons:
                        total_dist = len(cached) - 1
                        return (
                            f"NAV(map): to {preferred_dest} ({total_dist} tiles, cached): "
                            f"{buttons} — re-evaluate after executing"
                        )

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
                if warp_pos == player_pos:
                    continue  # can't path to warp we're standing on
                if preferred_dest and preferred_dest.lower() in dest_name.lower():
                    preferred_warps.append((warp_pos, dest_name))
                else:
                    other_warps.append((warp_pos, dest_name))

            # Further split preferred warps into fresh and exhausted
            fresh_preferred = [(p, n) for p, n in preferred_warps if p not in exhausted]
            exhausted_preferred = [(p, n) for p, n in preferred_warps if p in exhausted]

            # Try fresh preferred warps first, but skip any that lie in the
            # opposite direction from preferred_direction.
            for warp_pos, dest_name in fresh_preferred:
                if preferred_direction:
                    dvx, dvy = _DIR_VEC.get(preferred_direction, (0, 0))
                    score = (warp_pos[0] - player_pos[0]) * dvx + (warp_pos[1] - player_pos[1]) * dvy
                    if score < 0:
                        continue  # warp is behind us relative to goal direction
                path = self.find_path_to(map_id, player_pos, warp_pos, max_steps=200, blocked=npc_blocked, variance=variance)
                if path and len(path) < best_len:
                    best_path = path
                    best_name = dest_name
                    best_len = len(path)

            # If no fresh preferred warp worked, try exhausted ones as fallback
            # (soft deprioritization — prevents getting stuck when the only
            # exit is through an exhausted warp)
            if not best_path and exhausted_preferred:
                for warp_pos, dest_name in exhausted_preferred:
                    if preferred_direction:
                        dvx, dvy = _DIR_VEC.get(preferred_direction, (0, 0))
                        score = (warp_pos[0] - player_pos[0]) * dvx + (warp_pos[1] - player_pos[1]) * dvy
                        if score < 0:
                            continue
                    path = self.find_path_to(map_id, player_pos, warp_pos, max_steps=200, blocked=npc_blocked, variance=variance)
                    if path and len(path) < best_len:
                        best_path = path
                        best_name = dest_name
                        best_len = len(path)
                if best_path:
                    self._used_exhausted_warp = True
                    logger.info(
                        f"NAV: using exhausted warp to {best_name} as fallback "
                        f"(no fresh alternative on map 0x{map_id:02X})"
                    )

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
                    path = self.find_path_to(map_id, player_pos, warp_pos, max_steps=200, blocked=npc_blocked, variance=variance)
                    if path:
                        best_path = path
                        best_name = dest_name
                        best_len = len(path)
                        break

            # Fall back to any reachable warp when no preferred_dest given,
            # or when preferred warps existed but were all unreachable
            # (e.g. B2F south zone can't reach W1 but CAN reach W2).
            if not best_path and (not preferred_dest or preferred_warps):
                for warp_pos, dest_name in other_warps:
                    path = self.find_path_to(map_id, player_pos, warp_pos, max_steps=200, blocked=npc_blocked, variance=variance)
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

        # ── Ledge-aware truncation ──
        # A path tile is "ledge-dangerous" if the player standing there
        # could accidentally step onto an adjacent one-way ledge.  A ledge
        # tile with allowed direction (dx, dy) can only be entered from
        # position (ledge_x - dx, ledge_y - dy).  We only truncate when
        # the path tile IS that launch position AND the path doesn't
        # intentionally cross the ledge (i.e. the next step isn't the
        # ledge tile itself — A* already validated that crossing).
        ledge_cutoff = None
        if tile_map and len(best_path) > 3:
            for i, (px, py) in enumerate(best_path):
                if i < 2:
                    continue  # skip first 2 steps (too close to start)
                for ox, oy in NEIGHBORS:
                    adj_pos = (px + ox, py + oy)
                    adj_ch = tile_map.get(adj_pos)
                    if adj_ch not in LEDGE_ALLOWED_DIR:
                        continue
                    ldx, ldy = LEDGE_ALLOWED_DIR[adj_ch]
                    # Is the player at the launch position for this ledge?
                    if (ox, oy) != (ldx, ldy):
                        continue  # ledge faces away — no danger
                    # If path intentionally crosses this ledge, skip
                    if i + 1 < len(best_path) and best_path[i + 1] == adj_pos:
                        continue
                    ledge_cutoff = max(2, i - 1)
                    break
                if ledge_cutoff is not None:
                    break

        # Truncate for output
        effective_max = max_steps
        if ledge_cutoff is not None and ledge_cutoff < effective_max:
            effective_max = ledge_cutoff
            logger.info(
                f"NAV ledge truncation: path to {best_name} cut from "
                f"{len(best_path)-1} to {effective_max} steps "
                f"(ledge adjacent at step {ledge_cutoff+1})"
            )
        truncated = len(best_path) > effective_max + 1
        display_path = best_path[: effective_max + 1] if truncated else best_path
        buttons = self._path_to_buttons(display_path)
        if not buttons:
            return None

        total_dist = best_len - 1  # steps, not nodes
        suffix = f" (+{total_dist - effective_max} more)" if truncated else ""
        # Record as pending route for verification on map transition
        if best_name != "unexplored frontier":
            self.set_pending_route(map_id, best_name, best_path)
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

        _WALKABLE = frozenset(".,:")  # floor + grass + elevated platform
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
