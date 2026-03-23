"""Pokémon Red event flag reader and story progression tracker.

Event flags are stored as a bit array at wEventFlags (0xD747), spanning
320 bytes (2560 bits).  Flag N lives at byte 0xD747 + (N // 8), bit N % 8.
A set bit means the event has occurred.

Flag numbers are derived from the pokered disassembly event_constants.asm.
"""

import logging
from typing import Callable, Dict, List, Optional, Tuple, Any

from claude_player.utils.ram_constants import (
    ADDR_EVENT_FLAGS,
    ADDR_NUM_BAG_ITEMS,
    ADDR_BAG_ITEMS,
)

logger = logging.getLogger(__name__)


def _has_item(memory_read_func: Callable[[int], int], item_id: int) -> bool:
    """Return True if item_id is present in the player's bag."""
    num = memory_read_func(ADDR_NUM_BAG_ITEMS)
    if num > 20:
        return False
    for i in range(num):
        if memory_read_func(ADDR_BAG_ITEMS + i * 2) == item_id:
            return True
    return False

# Ordered story progression milestones.
# Each entry: (flag_number, milestone_name, goal_text)
# Order follows the intended game flow — the first uncompleted flag
# determines the agent's auto-goal.
#
# Negative flag sentinels indicate non-event-flag milestones (-1 through -12 in story order):
#   _VISIT_MAP_CHECKS: sentinel → map_id that must appear in visited_maps (navigation checks)
#   _ITEM_ID_CHECKS:   sentinel → tuple of item_ids; any one present in bag = milestone done
# Navigation milestones: completed when a specific map has been visited.
_VISIT_MAP_CHECKS: Dict[int, int] = {
     -1: 0x02,  # Through Viridian Forest → Pewter City (0x02)
     -2: 0x03,  # Through Mt. Moon → Cerulean City (0x03)
     -8: 0xD4,  # Cleared Silph Co. → Silph Co. 7F (0xD4) — deep enough to confirm progress
     -9: 0x08,  # Surfed to Cinnabar Island → Cinnabar Island (0x08)
    -11: 0x6C,  # Reached Victory Road → Victory Road 1F (0x6C)
    -12: 0xAE,  # Through Victory Road → Indigo Plateau Lobby (0xAE)
}

# Item-possession milestones: completed when any item_id in the tuple is in the bag.
_ITEM_ID_CHECKS: Dict[int, tuple] = {
    -3: (0x3F,),              # Got S.S. Ticket (0x3F)
    -4: (0x48,),              # Got Silph Scope (0x48)
    -5: (0x40,),              # Got Gold Teeth (0x40)
    -6: (0xC7,),              # Got HM04 Strength (0xC7)
    -7: (0x3C, 0x3D, 0x3E),   # Bought drink: Fresh Water, Soda Pop, or Lemonade
    -10: (0x2B,),             # Got Secret Key (0x2B)
}

STORY_PROGRESSION: List[Tuple[int, str, str]] = [
    # Flag numbers parsed from pret/pokered event_constants.asm via const_def/const_skip tracing
    (0x027, "Oak appeared in Pallet",              "Go outside and walk into the tall grass on Route 1 to trigger Oak's appearance"),
    (0x022, "Got starter Pokémon",                 "Choose a starter Pokémon from Oak's Lab in Pallet Town"),
    (0x023, "Battled rival in Oak's Lab",          "Walk toward the exit of Oak's Lab — your rival will challenge you automatically"),
    (0x039, "Got Oak's Parcel",                    "Pick up Oak's Parcel from the Viridian City Poké Mart"),
    (0x025, "Got Pokédex",                         "Deliver the parcel to Prof. Oak in his lab in Pallet Town to receive the Pokédex"),
    (  -1,  "Through Viridian Forest",             "Travel through Viridian Forest to reach Pewter City"),
    (0x077, "Beat Brock",                          "Defeat Brock at Pewter City Gym to earn the Boulder Badge"),
    (  -2,  "Through Mt. Moon",                    "Navigate through Mt. Moon to reach Cerulean City"),
    (0x0BF, "Beat Misty",                          "Defeat Misty at Cerulean City Gym to earn the Cascade Badge"),
    (  -3,  "Got S.S. Ticket from Bill",           "Visit Bill's House at the end of Route 25 and help him to receive the S.S. Ticket"),
    (0x5E0, "Got HM01 Cut",                        "Board the S.S. Anne in Vermilion City and get HM01 Cut from the captain"),
    (0x167, "Beat Lt. Surge",                      "Teach Cut to a Pokémon, clear the tree at Vermilion City Gym, and defeat Lt. Surge for the Thunder Badge"),
    (  -4,  "Got Silph Scope from Rocket Hideout", "Find the Rocket Hideout beneath Celadon City Game Corner and defeat Giovanni to get the Silph Scope"),
    (0x1A9, "Beat Erika",                          "Defeat Erika at Celadon City Gym to earn the Rainbow Badge"),
    (0x128, "Got Poké Flute",                      "Climb Pokémon Tower in Lavender Town, use the Silph Scope on the ghost, and rescue Mr. Fuji for the Poké Flute"),
    (0x259, "Beat Koga",                           "Defeat Koga at Fuchsia City Gym to earn the Soul Badge"),
    (0x880, "Got HM03 Surf",                       "Find the Secret House in Fuchsia City Safari Zone to receive HM03 Surf"),
    (  -5,  "Got Gold Teeth",                      "Find the Gold Teeth item in Fuchsia City Safari Zone"),
    (  -6,  "Got HM04 Strength from Warden",       "Give the Gold Teeth to the Safari Zone Warden in Fuchsia City to receive HM04 Strength"),
    (  -7,  "Bought Celadon Dept Store drink",     "Buy a Fresh Water, Soda Pop, or Lemonade from the Celadon City Dept Store rooftop vending machines"),
    (  -8,  "Cleared Silph Co.",                   "Enter Silph Co. in Saffron City and defeat Giovanni to liberate the building"),
    (0x361, "Beat Sabrina",                        "Defeat Sabrina at Saffron City Gym to earn the Marsh Badge"),
    (  -9,  "Surfed to Cinnabar Island",           "Use Surf to reach Cinnabar Island"),
    ( -10,  "Got Secret Key from Pokémon Mansion", "Find the Secret Key in the basement of Pokémon Mansion on Cinnabar Island"),
    (0x299, "Beat Blaine",                         "Defeat Blaine at Cinnabar Island Gym (unlocked with the Secret Key) to earn the Volcano Badge"),
    (0x051, "Beat Giovanni (Viridian Gym)",        "Defeat Giovanni at Viridian City Gym to earn the Earth Badge"),
    ( -11,  "Reached Victory Road",                "Travel through Route 22 and Route 23 to reach the Victory Road entrance"),
    ( -12,  "Through Victory Road",                "Navigate Victory Road's three floors using Strength to push boulders and reach Indigo Plateau"),
    (0x8FE, "Beat Lance (Elite Four)",             "Defeat all four Elite Four members at Indigo Plateau: Lorelei, Bruno, Agatha, and Lance"),
    (0x901, "Beat Champion",                       "Defeat Blue (your rival) at Indigo Plateau to become Pokémon Champion"),
    (0x8C1, "Caught Mewtwo",                       "Enter Cerulean Cave and catch Mewtwo"),
]

# Recommended minimum party max-level for each milestone flag.
# Keyed by flag number → (min_level, gym_leader_name_or_context).
# Sourced from pokered trainer data: gym leaders' highest-level Pokemon.
# The gate fires when the party's highest level is below min_level.
MILESTONE_LEVEL_GATES: Dict[int, Tuple[int, str]] = {
    0x077: (12, "Brock (Lv14 Onix)"),
    -2:    (16, "Mt. Moon wild encounters Lv6-12"),
    0x0BF: (19, "Misty (Lv21 Starmie)"),
    -3:    (22, "Route 24-25 trainers"),
    0x5E0: (22, "S.S. Anne trainers"),
    0x167: (24, "Lt. Surge (Lv24 Raichu)"),
    -4:    (28, "Rocket Hideout (Giovanni Lv25)"),
    0x1A9: (29, "Erika (Lv29 Vileplume)"),
    0x128: (30, "Pokémon Tower (Lv24-30 ghosts)"),
    0x259: (38, "Koga (Lv43 Weezing)"),
    0x880: (38, "Safari Zone"),
    -8:    (40, "Silph Co. (Giovanni Lv41)"),
    0x361: (43, "Sabrina (Lv43 Alakazam)"),
    0x299: (47, "Blaine (Lv47 Arcanine)"),
    0x051: (50, "Giovanni (Lv50 Rhydon)"),
    -12:   (50, "Victory Road trainers"),
    0x8FE: (55, "Elite Four (Lance Lv62 Dragonite)"),
    0x901: (58, "Champion Blue (Lv65)"),
}


def is_event_set(memory_read_func: Callable[[int], int], flag_number: int) -> bool:
    """Check whether a specific event flag is set in RAM.

    Args:
        memory_read_func: Callable that reads a byte from a RAM address
                          (e.g. pyboy.memory.__getitem__).
        flag_number: The event flag number to check.

    Returns:
        True if the flag bit is set.
    """
    byte_addr = ADDR_EVENT_FLAGS + (flag_number // 8)
    bit_index = flag_number % 8
    byte_val = memory_read_func(byte_addr)
    return bool(byte_val & (1 << bit_index))


def check_story_progress(
    memory_read_func: Callable[[int], int],
    visited_maps: Optional[set] = None,
) -> Dict[str, Any]:
    """Check overall story progression by scanning all milestone flags.

    Args:
        memory_read_func: Callable that reads a byte from a RAM address.
        visited_maps: Set of map IDs the agent has visited (used for visit-map milestones).

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
            if flag < 0:
                if flag in _ITEM_ID_CHECKS:
                    # Item-possession milestone: done when any matching item is in the bag
                    is_done = any(
                        _has_item(memory_read_func, iid)
                        for iid in _ITEM_ID_CHECKS[flag]
                    )
                else:
                    # Visit-map milestone: done when the required map has been visited
                    required_map = _VISIT_MAP_CHECKS.get(flag)
                    is_done = (
                        required_map is not None
                        and visited_maps is not None
                        and required_map in visited_maps
                    )
            else:
                is_done = is_event_set(memory_read_func, flag)

            if is_done:
                completed.append((flag, name, goal))
            elif next_milestone is None:
                next_milestone = (flag, name, goal)
        except Exception as e:
            flag_str = str(flag) if flag < 0 else f"0x{flag:03X}"
            logger.debug(f"Error reading flag {flag_str} ({name}): {e}")
            if next_milestone is None:
                next_milestone = (flag, name, goal)

    total = len(STORY_PROGRESSION)
    done = len(completed)

    if next_milestone:
        summary = f"{done}/{total} milestones → NEXT: {next_milestone[2]}"
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
    # Oak appeared (0x027)
    (0x027, 0x26): "Head downstairs to leave the house.",
    (0x027, 0x25): "Exit through the front door.",
    (0x027, 0x00): "Walk toward Route 1 to the north.",
    (0x027, 0x0C): "Walk into the tall grass — Oak will appear.",
    # Got starter (0x022)
    (0x022, 0x00): "Oak's Lab is in the south part of town.",
    (0x022, 0x28): "Choose a starter Pokémon from the table.",
    # Battled rival (0x023)
    (0x023, 0x28): "Walk toward the exit — your rival will challenge you automatically.",
    # Got Oak's Parcel (0x039)
    (0x039, 0x28): "The Poké Mart is in a town to the north.",
    (0x039, 0x00): "There's a town to the north with a Poké Mart.",
    (0x039, 0x0C): "Keep heading toward the town ahead.",
    (0x039, 0x01): "Enter the Poké Mart to pick up the parcel.",
    (0x039, 0x2A): "Talk to the shopkeeper to get the parcel.",
    # Got Pokédex (0x025)
    (0x025, 0x2A): "Oak's Lab is back to the south.",
    (0x025, 0x01): "Head back toward Pallet Town.",
    (0x025, 0x0C): "Continue south toward Pallet Town.",
    (0x025, 0x00): "Deliver the parcel to Prof. Oak in his lab.",
    (0x025, 0x28): "Talk to Prof. Oak to deliver the parcel.",
    # Through Viridian Forest (-1)
    (  -1,  0x00): "There's a forest to the north between here and Pewter City.",
    (  -1,  0x0C): "Continue toward the city ahead.",
    (  -1,  0x01): "The forest entrance is to the north.",
    (  -1,  0x0D): "Continue toward Pewter City.",
    (  -1,  0x32): "Enter the forest ahead.",
    (  -1,  0x33): "The forest exit is to the north. Explore to find the path through.",
    (  -1,  0x2F): "Pewter City is just ahead.",
    # Beat Brock (0x077)
    (0x077, 0x02): "The gym is somewhere in this city. Explore to find it.",
    (0x077, 0x36): "Defeat Brock at the back of the gym.",
    # Through Mt. Moon (-2)
    (  -2,  0x02): "Stock up on Potions and Antidotes before heading east — there's a cave ahead with no shops.",
    (  -2,  0x0E): "The cave entrance is at the east end of this route.",
    (  -2,  0x44): "Rest at the Pokémon Center before entering the cave.",
    (  -2,  0x3B): "This cave has multiple floors. Explore to find stairs going down.",
    (  -2,  0x3C): "This floor connects deeper into the cave. Explore to find the way down.",
    (  -2,  0x3D): "The exit to Route 4 is on this floor. Explore to find it.",
    (  -2,  0x0F): "Cerulean City is to the east.",
    # Beat Misty (0x0BF)
    (0x0BF, 0x03): "The gym is in the northeast part of this city.",
    (0x0BF, 0x41): "Defeat Misty at the back of the gym.",
    # Got S.S. Ticket from Bill (-3)
    (  -3,  0x03): "Bill's House is to the north and east, past Route 24 and Route 25.",
    (  -3,  0x23): "Continue east toward Bill's House.",
    (  -3,  0x24): "Bill's House is at the eastern end of this route.",
    (  -3,  0x58): "Talk to Bill and help him to receive the S.S. Ticket.",
    # Got HM01 Cut (0x5E0)
    (0x5E0, 0x03): "Vermilion City is to the south.",
    (0x5E0, 0x10): "Continue south toward Vermilion City.",
    (0x5E0, 0x11): "Continue south toward Vermilion City.",
    (0x5E0, 0x05): "The S.S. Anne is docked to the southeast. Show your ticket to board.",
    (0x5E0, 0x5E): "Show the S.S. Ticket to board the ship.",
    (0x5E0, 0x5F): "Explore the ship to find the captain's cabin.",
    (0x5E0, 0x65): "Talk to the captain to receive HM01 Cut.",
    # Beat Lt. Surge (0x167)
    (0x167, 0x05): "The gym is in the south part of town. Use Cut on the tree blocking the entrance.",
    (0x167, 0x5C): "Search trash cans to find the switches that unlock the door, then defeat Lt. Surge.",
    # Got Silph Scope (-4)
    (  -4,  0x04): "Celadon City is to the west.",
    (  -4,  0x05): "Celadon City is to the north and west.",
    (  -4,  0x06): "Stock up before going underground. The Game Corner hides a secret entrance.",
    (  -4,  0x87): "There's a hidden entrance to the Rocket Hideout somewhere in this building.",
    (  -4,  0xC7): "The hideout has multiple basement floors. Explore to find stairs going down.",
    (  -4,  0xC8): "Continue deeper into the hideout.",
    (  -4,  0xC9): "Continue deeper — the boss is on the lowest floor.",
    (  -4,  0xCA): "Defeat Giovanni to receive the Silph Scope.",
    # Beat Erika (0x1A9)
    (0x1A9, 0x06): "The gym is on the west side of town. Look for a way in from behind.",
    (0x1A9, 0x86): "Defeat Erika at the back of the gym.",
    # Got Poké Flute (0x128)
    (0x128, 0x04): "Stock up before climbing — Pokémon Tower has no shop. The tower is in this town.",
    (0x128, 0x8E): "Climb through the tower. Stairs lead up to higher floors.",
    (0x128, 0x8F): "Continue climbing through the tower.",
    (0x128, 0x90): "Continue climbing through the tower.",
    (0x128, 0x91): "Continue climbing through the tower.",
    (0x128, 0x92): "Continue climbing through the tower.",
    (0x128, 0x93): "Continue climbing through the tower.",
    (0x128, 0x94): "Use the Silph Scope near the ghost, defeat it, then rescue Mr. Fuji at the top for the Poké Flute.",
    # Beat Koga (0x259)
    (0x259, 0x07): "The gym is in this city. Explore to find it.",
    (0x259, 0x9D): "The gym has invisible walls. Navigate carefully and defeat Koga.",
    # Got HM03 Surf (0x880)
    (0x880, 0x07): "The Safari Zone entrance is in this city.",
    (0x880, 0x9C): "Explore the Safari Zone to find the Secret House.",
    (0x880, 0xDC): "Explore further into the Safari Zone.",
    (0x880, 0xDB): "The Secret House is somewhere in this area.",
    (0x880, 0xDE): "Talk to the person inside to receive HM03 Surf.",
    # Got Gold Teeth (-5)
    (  -5,  0x9C): "The Gold Teeth are somewhere on the ground in the Safari Zone.",
    (  -5,  0xDC): "Explore further into the Safari Zone.",
    (  -5,  0xDB): "Search this area for the Gold Teeth item on the ground.",
    (  -5,  0x07): "Enter the Safari Zone to find the Gold Teeth.",
    # Got HM04 Strength from Warden (-6)
    (  -6,  0x9C): "The Warden's House is in town, near the Pokémon Center.",
    (  -6,  0x07): "The Warden's House is near the Pokémon Center in this city.",
    (  -6,  0x9B): "Give the Gold Teeth to the Warden to receive HM04 Strength.",
    # Bought Celadon drink (-7)
    (  -7,  0x07): "Head toward Celadon City to visit the Dept Store.",
    (  -7,  0x06): "The Dept Store is the large building in this city. Take the elevator to the rooftop.",
    (  -7,  0x7A): "Take the elevator to the rooftop.",
    (  -7,  0x7E): "Buy a drink from the vending machines — needed to enter Saffron City.",
    # Cleared Silph Co. (-8)
    (  -8,  0x06): "Saffron City is to the east — show a drink to the gate guard to enter.",
    (  -8,  0x0A): "Stock up before entering Silph Co. — it's a long multi-floor building with no shop. Silph Co. is the large tower in the center of this city.",
    (  -8,  0xB5): "Silph Co. has many floors. Explore to find the Lift Key and work your way up.",
    (  -8,  0xCF): "Explore this floor. The Lift Key may be here.",
    (  -8,  0xD0): "Continue exploring Silph Co.",
    (  -8,  0xD1): "Continue exploring Silph Co.",
    (  -8,  0xD2): "Continue exploring Silph Co.",
    (  -8,  0xD3): "Continue exploring Silph Co.",
    (  -8,  0xD4): "Defeat Giovanni somewhere on this floor to liberate the building.",
    # Beat Sabrina (0x361)
    (0x361, 0x0A): "The gym is in this city. It has teleport pads.",
    (0x361, 0xB2): "Use the teleport pads to navigate the gym and defeat Sabrina.",
    # Surfed to Cinnabar Island (-9)
    (  -9,  0x00): "Use Surf to head south toward Cinnabar Island.",
    (  -9,  0x07): "Cinnabar Island is to the south via the sea routes.",
    (  -9,  0x1E): "Continue south through the sea route.",
    (  -9,  0x1F): "Continue west through the sea route.",
    (  -9,  0x20): "Continue south toward Cinnabar Island.",
    # Got Secret Key from Pokémon Mansion (-10)
    ( -10,  0x08): "Stock up before entering — Pokémon Mansion has no shop. The Mansion is the ruined building in this town.",
    ( -10,  0xA5): "The Mansion has multiple floors. The Secret Key is in the basement. Explore to find stairs.",
    ( -10,  0xD6): "Continue exploring — find stairs to higher or lower floors.",
    ( -10,  0xD7): "Continue exploring — the basement is accessible from this floor.",
    ( -10,  0xD8): "Search this floor for the Secret Key on the ground.",
    # Beat Blaine (0x299)
    (0x299, 0x08): "The gym is now unlocked with the Secret Key.",
    (0x299, 0xA6): "Answer the quiz questions or fight trainers, then defeat Blaine.",
    # Beat Giovanni Viridian Gym (0x051)
    (0x051, 0x01): "Viridian City Gym is now open. Explore to find it.",
    (0x051, 0x2D): "Navigate the gym's puzzle and defeat Giovanni for the Earth Badge.",
    # Reached Victory Road (-11)
    ( -11,  0x01): "Route 22 is to the west, leading toward the Pokémon League.",
    ( -11,  0x21): "Continue north toward the Pokémon League.",
    ( -11,  0x22): "Badge checkers verify your badges along this route. Keep going north.",
    # Through Victory Road (-12)
    ( -12,  0x22): "Stock up on Full Restores and Revives before entering — no shops inside Victory Road.",
    ( -12,  0x6C): "Victory Road has three floors. Use Strength to push boulders and find stairs to progress.",
    ( -12,  0xC2): "Continue through Victory Road. Find stairs to the next floor.",
    ( -12,  0xC6): "Continue through Victory Road toward the exit.",
    ( -12,  0x09): "You've reached Indigo Plateau! The Pokémon League is inside.",
    # Beat Lance / Elite Four (0x8FE)
    (0x8FE, 0x09): "Heal and stock up at the Pokémon Center and Mart before facing the Elite Four.",
    (0x8FE, 0xAE): "Heal at the Pokémon Center, then face the Elite Four: Lorelei, Bruno, Agatha, Lance.",
}


def get_map_hint(next_flag: Optional[int], current_map_id: int) -> Optional[str]:
    """Return a context-aware hint for the current milestone + map, or None."""
    if next_flag is None:
        return None
    return MAP_HINTS.get((next_flag, current_map_id))
