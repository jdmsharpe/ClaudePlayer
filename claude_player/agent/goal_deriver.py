"""Tactical goal derivation from MAP_HINTS table.

Derives a map-specific tactical goal from the current story milestone
and player's map location.  Pure function — no state, no I/O.
"""

from typing import Optional

from claude_player.utils.event_flags import get_map_hint


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
