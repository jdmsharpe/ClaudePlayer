"""Tactical goal derivation from MAP_HINTS table + NAV fallback.

Derives a map-specific tactical goal from the current story milestone
and player's map location.  Falls back to map-graph BFS routing when
no hand-authored hint exists.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from claude_player.utils.event_flags import get_map_hint

if TYPE_CHECKING:
    from claude_player.utils.world_map import WorldMap


def derive_tactical_goal(
    next_flag: Optional[int],
    current_map_id: Optional[int],
) -> Optional[str]:
    """Return the map-specific tactical goal, or None to fall back to strategic.

    Args:
        next_flag: The flag number of the next uncompleted story milestone.
        current_map_id: The player's current map ID.

    Returns:
        A tactical action string from MAP_HINTS, or None if no entry exists.
    """
    if next_flag is None or current_map_id is None:
        return None
    return get_map_hint(next_flag, current_map_id)


def derive_nav_tactical_goal(
    world_map: WorldMap,
    current_map_id: int,
    strategic_goal: Optional[str],
) -> Optional[str]:
    """Generate a tactical goal from map-graph BFS when MAP_HINTS has no entry.

    Extracts a target map name from the strategic goal text via longest
    substring match against known map names, then BFS-routes to find the
    next hop.

    Args:
        world_map: Persistent WorldMap with map_graph and map_names.
        current_map_id: The player's current map ID.
        strategic_goal: The current strategic goal text to extract target from.

    Returns:
        A routing-based tactical goal like "Navigate to Route 4 (toward
        Cerulean City)", or None if no route can be determined.
    """
    if not strategic_goal:
        return None

    # Find target map by longest substring match (same logic as nav_planner)
    target_map_id = None
    best_match_len = 0
    goal_lower = strategic_goal.lower()
    for mid, mname in world_map.map_names.items():
        if mid == current_map_id:
            continue
        if mname.lower() in goal_lower and len(mname) > best_match_len:
            target_map_id = mid
            best_match_len = len(mname)

    if target_map_id is None:
        return None

    target_name = world_map.map_names.get(target_map_id, "?")

    # BFS for next hop
    map_path = world_map.find_map_path(current_map_id, target_map_id)
    if not map_path or len(map_path) < 2:
        return None

    next_hop_id = map_path[1]
    next_hop_name = world_map.map_names.get(next_hop_id, "?")

    if next_hop_id == target_map_id:
        return f"Head to {target_name}"
    return f"Navigate to {next_hop_name} (toward {target_name})"
