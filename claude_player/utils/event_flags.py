"""Pokemon Red event flag reader and story progression tracker.

Event flags are stored as a bit array at wEventFlags (0xD747), spanning
320 bytes (2560 bits).  Flag N lives at byte 0xD747 + (N // 8), bit N % 8.
A set bit means the event has occurred.

Flag numbers are derived from the pokered disassembly event_constants.asm.
"""

import logging
from typing import Callable, Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# Base RAM address for the event flags bit array
_ADDR_EVENT_FLAGS = 0xD747

# Ordered story progression milestones.
# Each entry: (flag_number, milestone_name, goal_text)
# Order follows the intended game flow — the first uncompleted flag
# determines the agent's auto-goal.
STORY_PROGRESSION: List[Tuple[int, str, str]] = [
    # Flag numbers parsed from pret/pokered event_constants.asm via const_def/const_skip tracing
    (0x027, "Oak appeared in Pallet",       "Start a new game (press A through intro/naming). Then go downstairs, exit house, walk NORTH into Route 1 tall grass to trigger Oak"),
    (0x022, "Got starter Pokemon",          "Go to Oak's Lab and choose a starter Pokemon"),
    (0x023, "Battled rival in Oak's Lab",   "Walk toward the exit door of Oak's Lab — your rival will stop you and challenge you automatically (do NOT talk to him, just walk south to the door)"),
    (0x039, "Got Oak's Parcel",            "Go NORTH through Route 1 to Viridian City and pick up Oak's Parcel from the Poke Mart clerk"),
    (0x025, "Got Pokedex",                  "Return SOUTH to Pallet Town and deliver the parcel to Prof. Oak in Oak's Lab to get the Pokedex"),
    (0x077, "Beat Brock",                   "Travel through Viridian Forest to Pewter City and defeat Brock"),
    (0x0BF, "Beat Misty",                   "Go through Mt. Moon to Cerulean City and defeat Misty"),
    (0x5E0, "Got HM01 Cut",                 "Go to Vermilion City via Route 5/6, board S.S. Anne (need S.S. Ticket from Bill on Route 25), and get HM01 Cut from the captain"),
    (0x167, "Beat Lt. Surge",               "Teach Cut to a Pokemon, use Cut on the tree blocking Vermilion Gym, and defeat Lt. Surge"),
    (0x128, "Got Poke Flute",               "Clear Pokemon Tower in Lavender Town to get the Poke Flute (need Silph Scope from Rocket Hideout in Celadon Game Corner basement)"),
    (0x1A9, "Beat Erika",                   "Go to Celadon City and defeat Erika"),
    (0x259, "Beat Koga",                    "Go to Fuchsia City and defeat Koga (Cycling Road needs Bicycle from Cerulean Bike Shop, or take Routes 12-15)"),
    (0x361, "Beat Sabrina",                 "Go to Saffron City and defeat Sabrina (buy a drink from Celadon Dept. Store rooftop vending machine to pass Saffron guards; must clear Silph Co. before gym opens)"),
    (0x880, "Got HM03 Surf",                "Go to Fuchsia City Safari Zone and find the Secret House to get HM03 Surf (also get Gold Teeth for the warden to receive HM04 Strength)"),
    (0x299, "Beat Blaine",                  "Teach Surf, Surf south from Pallet Town or Fuchsia City to Cinnabar Island, get the Secret Key from Pokemon Mansion, and defeat Blaine"),
    (0x051, "Beat Giovanni (Viridian Gym)", "Return to Viridian City Gym and defeat Giovanni"),
    (0x8FE, "Beat Lance (Elite Four)",      "Defeat all Elite Four members at Indigo Plateau"),
    (0x901, "Beat Champion",                "Travel Victory Road to Indigo Plateau and defeat Blue to become Champion (need HM04 Strength)"),
    (0x8C1, "Caught Mewtwo",               "Go to Cerulean Cave (unlocked after becoming Champion) and catch Mewtwo (need HM03 Surf)"),
]


def is_event_set(memory_read_func: Callable[[int], int], flag_number: int) -> bool:
    """Check whether a specific event flag is set in RAM.

    Args:
        memory_read_func: Callable that reads a byte from a RAM address
                          (e.g. pyboy.memory.__getitem__).
        flag_number: The event flag number to check.

    Returns:
        True if the flag bit is set.
    """
    byte_addr = _ADDR_EVENT_FLAGS + (flag_number // 8)
    bit_index = flag_number % 8
    byte_val = memory_read_func(byte_addr)
    return bool(byte_val & (1 << bit_index))


def check_story_progress(memory_read_func: Callable[[int], int]) -> Dict[str, Any]:
    """Check overall story progression by scanning all milestone flags.

    Args:
        memory_read_func: Callable that reads a byte from a RAM address.

    Returns:
        Dict with keys:
            completed: list of (flag, name, goal) for completed milestones
            next: (flag, name, goal) tuple for the first uncompleted milestone, or None
            next_goal: goal text string for the next milestone, or None
            progress_summary: human-readable one-line summary
    """
    completed: List[Tuple[int, str, str]] = []
    next_milestone: Optional[Tuple[int, str, str]] = None

    for flag, name, goal in STORY_PROGRESSION:
        try:
            if is_event_set(memory_read_func, flag):
                completed.append((flag, name, goal))
            elif next_milestone is None:
                next_milestone = (flag, name, goal)
        except Exception as e:
            logger.debug(f"Error reading flag 0x{flag:03X} ({name}): {e}")
            if next_milestone is None:
                next_milestone = (flag, name, goal)

    total = len(STORY_PROGRESSION)
    done = len(completed)

    if next_milestone:
        last_name = completed[-1][1] if completed else "none"
        summary = f"{done}/{total} milestones (last: {last_name}) | NEXT: {next_milestone[2]}"
    elif done == total:
        summary = f"{done}/{total} milestones — game complete!"
    else:
        summary = f"{done}/{total} milestones"

    return {
        "completed": completed,
        "next": next_milestone,
        "next_goal": next_milestone[2] if next_milestone else None,
        "progress_summary": summary,
    }


# Context-aware hints: (next_milestone_flag, current_map_id) → action hint.
# Only shown when the milestone is next AND the player is on the matching map.
MAP_HINTS: Dict[Tuple[int, int], str] = {
    # Oak appeared (0x027) — exit house, walk to Route 1 grass
    (0x027, 0x26): "Go downstairs (walk onto the staircase W tile).",
    (0x027, 0x25): "Exit through the front door (walk onto the door W tile).",
    (0x027, 0x00): "Walk NORTH to the map edge to enter Route 1.",
    (0x027, 0x0C): "Walk into the tall grass. Oak will appear automatically.",
    # Got starter (0x022) — pick a Pokeball in Oak's Lab
    (0x022, 0x00): "Enter Oak's Lab (the large building in south Pallet Town).",
    (0x022, 0x28): "Walk to the table with Pokeballs and press A facing it to choose a starter.",
    # Battled rival (0x023) — walk to exit, NOT talk to rival
    (0x023, 0x28): "Walk SOUTH toward the exit door — your rival will stop you automatically. Do NOT talk to him.",
    # Got Oak's Parcel (0x039) — go north to Viridian Mart to pick it up
    (0x039, 0x28): "Exit the lab and head NORTH through Route 1 to Viridian City Mart.",
    (0x039, 0x00): "Head NORTH to Route 1, then continue to Viridian City.",
    (0x039, 0x0C): "Keep heading NORTH to reach Viridian City.",
    (0x039, 0x01): "Enter the Poke Mart to pick up Oak's Parcel.",
    (0x039, 0x2A): "Talk to the shopkeeper to get Oak's Parcel.",
    # Got Pokedex (0x025) — return south to Oak's Lab with the parcel
    (0x025, 0x2A): "Exit the Mart and head SOUTH to Pallet Town.",
    (0x025, 0x01): "Head SOUTH toward Route 1 to return to Pallet Town.",
    (0x025, 0x0C): "Keep heading SOUTH to reach Pallet Town.",
    (0x025, 0x00): "Enter Oak's Lab (the large building in south Pallet Town) and deliver the parcel.",
    (0x025, 0x28): "Talk to Prof. Oak to deliver the parcel and receive the Pokedex.",
    # Beat Brock (0x077) — navigate to Pewter Gym
    (0x077, 0x00): "Head NORTH through Route 1 toward Viridian City.",
    (0x077, 0x0C): "Continue NORTH to Viridian City.",
    (0x077, 0x01): "Head NORTH toward Route 2 and Viridian Forest.",
    (0x077, 0x0D): "Enter the gatehouse to reach Viridian Forest.",
    (0x077, 0x32): "Walk NORTH through the gate to enter Viridian Forest.",
    (0x077, 0x33): "Navigate NORTH through the forest to the exit.",
    (0x077, 0x2F): "Exit NORTH to reach Route 2 near Pewter City.",
    (0x077, 0x02): "Enter Pewter Gym and defeat Brock.",
    (0x077, 0x36): "Battle the trainers and defeat Brock at the back of the gym.",
}


def get_map_hint(next_flag: Optional[int], current_map_id: int) -> Optional[str]:
    """Return a context-aware hint for the current milestone + map, or None."""
    if next_flag is None:
        return None
    return MAP_HINTS.get((next_flag, current_map_id))
