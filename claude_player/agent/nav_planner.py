"""Navigation planner: map-graph BFS + compass fallback + spatial text injection.

Extracted from game_agent.py to isolate the NAV pipeline into a focused,
independently testable module.  The main entry point is compute_nav(),
which takes pure data and returns an updated spatial_text string.
"""

import logging
import re
from typing import Dict, List, Optional, Tuple

from claude_player.utils.warp_overrides import WARP_DEST_NAME_OVERRIDES
from claude_player.utils.world_map import WorldMap

# Pre-compute lowercased override names for fast validation
_OVERRIDE_NAMES_LOWER = {v.lower() for v in WARP_DEST_NAME_OVERRIDES.values()}

# Last NAV method used — set by compute_nav(), read by game_agent for TURN_SUMMARY
last_nav_method: str = ""

# Track consecutive "exhausted" results to trigger frontier earlier.
# When graph routing repeatedly falls back to exhausted warps, the agent
# is likely cycling through bad warps.  After 2 consecutive exhausted
# results, force frontier exploration to discover new territory.
_consecutive_exhausted: int = 0

# Cache A* failures: (map_id, hop_id) pairs that failed.
# Cleared when the map's tile count changes (new tiles explored).
_failed_hops: dict = {}  # map_id → {hop_id: tile_count_at_failure}


# Direction keyword mapping for COMPASS fallback NAV parsing
_DIR_TO_COMPASS_KW = {
    "NORTH": "UP", "SOUTH": "DOWN",
    "EAST": "RIGHT", "WEST": "LEFT",
}


def _parse_compass_dest(
    spatial_text: str,
    goal_upper: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Extract preferred destination and direction from COMPASS lines.

    Parses the COMPASS block in spatial_text, cross-referencing with the
    goal text to find which compass destination best matches the agent's
    current objective.

    Returns:
        (preferred_dest, preferred_direction) — either may be None.
    """
    preferred_direction = next(
        (d for d in ("NORTH", "SOUTH", "EAST", "WEST")
         if re.search(rf"\b{d}\b", goal_upper)),
        None,
    )
    preferred_dest = None
    secondary_preferred_dest = None
    first_compass_dest = None
    in_compass = False
    kw = _DIR_TO_COMPASS_KW.get(preferred_direction, "") if preferred_direction else ""
    for line in spatial_text.split("\n"):
        if line.startswith("COMPASS"):
            in_compass = True
            continue
        if in_compass and line.startswith("  ") and ":" in line:
            dest = line.strip().split(":")[0].strip()
            if first_compass_dest is None:
                first_compass_dest = dest
            if kw and kw in line.upper():
                line_upper = line.upper()
                kw_idx = line_upper.find(kw)
                other_dirs = {"UP", "DOWN", "LEFT", "RIGHT"} - {kw}
                first_other = min(
                    (line_upper.find(d) for d in other_dirs if d in line_upper),
                    default=9999,
                )
                if kw_idx < first_other:
                    preferred_dest = dest
                    break
                elif secondary_preferred_dest is None:
                    secondary_preferred_dest = dest
        elif in_compass and not line.startswith("  "):
            in_compass = False
    if not preferred_dest:
        preferred_dest = secondary_preferred_dest
    if not preferred_dest:
        preferred_dest = first_compass_dest
        if preferred_dest and not preferred_direction:
            for line in spatial_text.split("\n"):
                if line.startswith("  ") and preferred_dest in line:
                    for d in ("NORTH", "SOUTH", "EAST", "WEST"):
                        if d in line.upper():
                            preferred_direction = d
                            break
                    break
    return preferred_dest, preferred_direction


def _inject_nav_hint(spatial_text: str, wm_nav: str) -> str:
    """Replace viewport NAV with world-map NAV in spatial text.

    Inserts the hint right after the COMPASS block (before the grid) so
    it's the first actionable hint the agent reads.  Falls back to after
    the 'Map position:' line if no COMPASS block is found.
    """
    new_lines = []
    for line in spatial_text.split("\n"):
        if line.startswith("NAV:"):
            continue  # drop viewport NAV
        new_lines.append(line)
    # Insert after COMPASS block; fallback: after Map position.
    insert_idx = len(new_lines)
    for i, line in enumerate(new_lines):
        if line.startswith("COMPASS"):
            insert_idx = i + 1
            while insert_idx < len(new_lines) and new_lines[insert_idx].startswith("  "):
                insert_idx += 1
            break
        if line.startswith("Map position:"):
            insert_idx = i + 1
    new_lines.insert(insert_idx, wm_nav)
    return "\n".join(new_lines)


def compute_nav(
    world_map: WorldMap,
    map_id: int,
    player_pos: Tuple[int, int],
    goal_text: str,
    spatial_text: str,
    npc_positions: Optional[List] = None,
    strategic_goal_text: Optional[str] = None,
    current_turn: int = 0,
    variance: int = 0,
) -> str:
    """Run the full NAV pipeline and return updated spatial_text.

    Pipeline priority:
      1. Map graph — extract target map from goal text (tactical first,
         then strategic fallback), BFS to find next hop, A* to that
         hop's warp.  If A* fails (e.g. ledges block), exclude that map
         and retry BFS up to 3 times.
      2. COMPASS fallback — parse direction from goal text, match against
         COMPASS entries in the spatial context.
      3. If neither produces a hint, spatial_text is returned unchanged.

    Args:
        world_map: Persistent WorldMap with tiles, warps, and map graph.
        map_id: Current map ID.
        player_pos: Player (x, y) in block coordinates.
        goal_text: Primary goal string for NAV (tactical goal when available).
        spatial_text: Full spatial context string to augment.
        npc_positions: Optional list of NPC absolute positions for A*.
        strategic_goal_text: Fallback goal string for map-graph BFS matching.

    Returns:
        The spatial_text string, potentially with a NAV hint injected.
    """
    global last_nav_method, _consecutive_exhausted
    last_nav_method = ""
    dead_end_zones = world_map.dead_ends.get(map_id, [])
    wm_nav = None

    # ── Step 0: Frontier-first exploration ──
    # When the map is barely explored (>30% frontier tiles), prioritize
    # exploring before goal-directed routing.  This prevents the agent from
    # bee-lining to a warp and missing items, NPCs, or alternate paths.
    _FRONTIER_RATIO_THRESHOLD = 0.3
    fr = world_map.frontier_ratio(map_id)
    if fr > _FRONTIER_RATIO_THRESHOLD and variance == 0:
        dead_end_tiles: set = set()
        if dead_end_zones:
            for dz_x, dz_y in dead_end_zones:
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        dead_end_tiles.add((dz_x + dx, dz_y + dy))
        npc_blocked: set = set(npc_positions) if npc_positions else set()
        frontier_path = world_map.find_frontier_path(
            map_id, player_pos,
            dead_end_tiles=dead_end_tiles,
            blocked=npc_blocked,
        )
        if frontier_path and len(frontier_path) > 1:
            buttons = WorldMap._path_to_buttons(frontier_path)
            if buttons:
                total_dist = len(frontier_path) - 1
                wm_nav = (
                    f"NAV(explore): map {int(fr*100)}% unexplored — "
                    f"explore first ({total_dist} tiles): {buttons}"
                )
                last_nav_method = "explore"
                _consecutive_exhausted = 0  # reset stale counter
                logging.info(f"NAV frontier-first (ratio={fr:.2f}): {wm_nav}")
                return _inject_nav_hint(spatial_text, wm_nav)

    # ── Step 1: Map graph lookup ──
    # Find target map by longest substring match in goal text.
    # Try primary (tactical) goal first, then strategic fallback.
    target_map_id = None
    best_match_len = 0
    search_texts = [t for t in (goal_text, strategic_goal_text) if t]
    for search_text in search_texts:
        for mid, mname in world_map.map_names.items():
            if mid == map_id:
                continue
            if mname.lower() in search_text.lower() and len(mname) > best_match_len:
                target_map_id = mid
                best_match_len = len(mname)
        if target_map_id is not None:
            break  # Found a match in this tier, don't fall through

    exclude_maps: set = set()
    # Pre-exclude maps we're currently cycling with — prevents the
    # BFS from routing back through the same floor over and over.
    cycling = world_map.get_cycling_maps(map_id, current_turn)
    if cycling:
        exclude_maps.update(cycling)
        logging.info(
            f"NAV: pre-excluding cycling maps: "
            f"{[f'0x{m:02X}' for m in cycling]}"
        )
    # Pre-exclude hops that previously failed A* on this map (same tile count)
    global _failed_hops
    cur_tile_count = len(world_map.tiles.get(map_id, {}))
    cached_failures = _failed_hops.get(map_id, {})
    for hop_id, tile_count in list(cached_failures.items()):
        if tile_count == cur_tile_count:
            exclude_maps.add(hop_id)
        else:
            del cached_failures[hop_id]  # tiles changed, retry
    if target_map_id is not None:
        # BFS with retry: if A* can't reach the first hop,
        # exclude it and re-BFS for an alternate route.
        for _attempt in range(3):
            map_path = world_map.find_map_path(
                map_id, target_map_id, exclude_maps=exclude_maps,
            )
            if not map_path or len(map_path) < 2:
                break
            hop_id = map_path[1]
            hop_name = world_map.map_names.get(hop_id)
            if not hop_name:
                break
            # Refine hop_name: if the goal text contains a more specific
            # warp dest_name that starts with the base hop_name (e.g.
            # "Mt. Moon B1F (east exit)" vs "Mt. Moon B1F"), prefer
            # the specific name so find_nav_hint can disambiguate warps
            # that share the same dest_map.
            refined_dest = hop_name
            for search_text in search_texts:
                # Look for "hop_name (qualifier)" pattern in goal text,
                # but only accept it if the match is a known override name
                # — prevents incidental parentheticals from misfiring.
                pattern = re.escape(hop_name) + r"\s*\([^)]+\)"
                m = re.search(pattern, search_text, re.IGNORECASE)
                if m and m.group(0).lower() in _OVERRIDE_NAMES_LOWER:
                    refined_dest = m.group(0)
                    break
            logging.info(
                f"NAV graph: target={world_map.map_names.get(target_map_id)!r} "
                f"next_hop={hop_name!r} path_len={len(map_path)} "
                f"map=0x{map_id:02X} pos={player_pos} "
                f"attempt={_attempt + 1}"
            )
            wm_nav = world_map.find_nav_hint(
                map_id, player_pos,
                preferred_dest=refined_dest,
                dead_end_zones=dead_end_zones,
                npc_positions=npc_positions,
                current_turn=current_turn,
                variance=variance,
            )
            if wm_nav:
                # Detect if this was a cached route or exhausted-warp fallback
                if "cached" in wm_nav:
                    last_nav_method = "cache"
                elif getattr(world_map, "_used_exhausted_warp", False):
                    last_nav_method = "exhausted"
                else:
                    last_nav_method = "graph"
                logging.info(f"NAV result: {wm_nav}")
                break
            # A* couldn't reach this hop — exclude, cache failure, and retry
            logging.info(f"NAV graph: {hop_name!r} unreachable via A*, excluding")
            exclude_maps.add(hop_id)
            _failed_hops.setdefault(map_id, {})[hop_id] = cur_tile_count

    # ── Track consecutive exhausted results ──
    # When graph routing repeatedly falls back to exhausted warps,
    # the agent is cycling through bad warps.  Force frontier exploration.
    if last_nav_method == "exhausted":
        _consecutive_exhausted += 1
        if _consecutive_exhausted >= 2:
            logging.info(
                f"NAV: {_consecutive_exhausted} consecutive exhausted results, "
                f"overriding to frontier exploration"
            )
            wm_nav = None  # discard the exhausted-warp result
            last_nav_method = ""
    elif last_nav_method in ("graph", "cache", "frontier", "explore"):
        _consecutive_exhausted = 0  # reset on successful non-exhausted nav

    # ── Step 2a: Frontier-first when graph routing was tried but failed ──
    # If the map graph FOUND a target but A* couldn't reach any hop's warp
    # (common in partially-explored caves), prefer pushing into unexplored
    # territory over compass fallback — compass often routes backward to the
    # previous floor (e.g. B1F → 1F instead of B1F → B2F → Route 4).
    # Also triggers when consecutive exhausted warps were detected above.
    if not wm_nav and target_map_id is not None and (exclude_maps or _consecutive_exhausted >= 2):
        dead_end_tiles: set = set()
        if dead_end_zones:
            for dz_x, dz_y in dead_end_zones:
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        dead_end_tiles.add((dz_x + dx, dz_y + dy))
        npc_blocked: set = set(npc_positions) if npc_positions else set()
        frontier_path = world_map.find_frontier_path(
            map_id, player_pos,
            dead_end_tiles=dead_end_tiles,
            blocked=npc_blocked,
        )
        if frontier_path and len(frontier_path) > 1:
            buttons = WorldMap._path_to_buttons(frontier_path)
            if buttons:
                total_dist = len(frontier_path) - 1
                wm_nav = (
                    f"NAV(map): to unexplored frontier ({total_dist} tiles): "
                    f"{buttons} — re-evaluate after executing"
                )
                last_nav_method = "frontier"
                logging.info(f"NAV frontier fallback (graph failed): {wm_nav}")

    # ── Step 2b: COMPASS fallback (no graph data or frontier empty) ──
    if not wm_nav:
        goal_upper = goal_text.upper()
        preferred_dest, preferred_direction = _parse_compass_dest(
            spatial_text, goal_upper,
        )
        logging.info(
            f"NAV compass fallback: dest={preferred_dest!r} dir={preferred_direction!r} "
            f"map=0x{map_id:02X} pos={player_pos} "
            f"warps={len(world_map.warps.get(map_id, {}))} "
            f"tiles={len(world_map.tiles.get(map_id, {}))}"
        )
        wm_nav = world_map.find_nav_hint(
            map_id, player_pos,
            preferred_dest=preferred_dest,
            preferred_direction=preferred_direction,
            dead_end_zones=dead_end_zones,
            npc_positions=npc_positions,
            current_turn=current_turn,
            variance=variance,
        )
        if wm_nav:
            last_nav_method = "compass"
            logging.info(f"NAV result: {wm_nav}")
        else:
            last_nav_method = "none"
            logging.info("NAV result: None (no path found)")

    # ── Step 3: Inject into spatial text ──
    if wm_nav:
        spatial_text = _inject_nav_hint(spatial_text, wm_nav)

    return spatial_text
