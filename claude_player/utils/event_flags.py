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
    (0x027, "Oak appeared in Pallet",              "Start a new game (press A through intro/naming). Then go downstairs, exit house, walk NORTH into Route 1 tall grass to trigger Oak"),
    (0x022, "Got starter Pokémon",                 "Go to Oak's Lab and choose a starter Pokémon"),
    (0x023, "Battled rival in Oak's Lab",          "Walk toward the exit door of Oak's Lab — your rival will stop you and challenge you automatically (do NOT talk to him, just walk south to the door)"),
    (0x039, "Got Oak's Parcel",                    "Go NORTH through Route 1 to Viridian City and pick up Oak's Parcel from the Poke Mart clerk"),
    (0x025, "Got Pokedex",                         "Return SOUTH to Pallet Town and deliver the parcel to Prof. Oak in Oak's Lab to get the Pokedex"),
    (  -1,  "Through Viridian Forest",             "Head NORTH from Viridian City through Route 2 and navigate Viridian Forest to reach Pewter City on the other side"),
    (0x077, "Beat Brock",                          "Enter Pewter Gym (northern part of Pewter City) and defeat Brock to earn the Boulder Badge"),
    (  -2,  "Through Mt. Moon",                    "Head EAST from Pewter City through Route 3, then navigate all three floors of Mt. Moon to reach Route 4 and Cerulean City"),
    (0x0BF, "Beat Misty",                          "Enter Cerulean Gym (northeast part of Cerulean City) and defeat Misty to earn the Cascade Badge"),
    (  -3,  "Got S.S. Ticket from Bill",           "Head NORTH from Cerulean through Routes 24 and 25 to Bill's House at the eastern end — talk to Bill to help him change back to human form and receive the S.S. Ticket"),
    (0x5E0, "Got HM01 Cut",                        "Go SOUTH from Cerulean through Routes 5 and 6 to Vermilion City, board S.S. Anne at the dock (show the S.S. Ticket to the guard), and get HM01 Cut from the seasick captain"),
    (0x167, "Beat Lt. Surge",                      "Teach HM01 Cut to a Pokémon, use Cut on the tree blocking Vermilion Gym, and defeat Lt. Surge to earn the Thunder Badge"),
    (  -4,  "Got Silph Scope from Rocket Hideout", "Go to Celadon City and infiltrate Team Rocket's hideout under the Game Corner (hidden stairs inside the Game Corner) — fight down to B4F and defeat Giovanni to get the Silph Scope"),
    (0x1A9, "Beat Erika",                          "Enter Celadon Gym (west side of Celadon City, accessible via back tree passage) and defeat Erika to earn the Rainbow Badge"),
    (0x128, "Got Poke Flute",                      "Go to Lavender Town and climb Pokémon Tower — use the Silph Scope on 7F to reveal the ghost (Marowak), defeat it, then free Mr. Fuji at the top to receive the Poke Flute"),
    (0x259, "Beat Koga",                           "Go to Fuchsia City (via Cycling Road with Bicycle, or south via Routes 12-15) and defeat Koga to earn the Soul Badge"),
    (0x880, "Got HM03 Surf",                       "Enter Fuchsia City Safari Zone and find the Secret House in the west area to receive HM03 Surf"),
    (  -5,  "Got Gold Teeth",                      "Find and pick up the Gold Teeth item on the ground in Safari Zone West — you'll need them to trade to the Warden for HM04 Strength"),
    (  -6,  "Got HM04 Strength from Warden",       "Visit the Safari Zone Warden's house (south of Fuchsia Pokémon Center) and give him the Gold Teeth to receive HM04 Strength"),
    (  -7,  "Bought Celadon Dept Store drink",     "Go to Celadon Dept Store and take the elevator to the rooftop (5F) — buy a FRESH WATER, SODA POP, or LEMONADE from the vending machine to bribe Saffron City guards"),
    (  -8,  "Cleared Silph Co.",                   "Go to Saffron City (show a drink to the guard at any entrance gate), find Silph Co. (large tower in center of Saffron), work through the building and defeat Giovanni to liberate it"),
    (0x361, "Beat Sabrina",                        "Enter Saffron Gym (now accessible after Silph Co. is cleared) and defeat Sabrina to earn the Marsh Badge"),
    (  -9,  "Surfed to Cinnabar Island",           "Teach HM03 Surf, then Surf south from Pallet Town (south edge of Pallet) or from Fuchsia City via Routes 19 and 20 to reach Cinnabar Island"),
    ( -10,  "Got Secret Key from Pokémon Mansion", "Enter Pokémon Mansion on Cinnabar Island and navigate to the basement (B1F) to find the Secret Key that unlocks Cinnabar Gym"),
    (0x299, "Beat Blaine",                         "Enter Cinnabar Gym (unlocked with the Secret Key) and defeat Blaine to earn the Volcano Badge"),
    (0x051, "Beat Giovanni (Viridian Gym)",        "Return to Viridian City Gym (now open) and defeat Giovanni to earn the Earth Badge"),
    ( -11,  "Reached Victory Road",                "Head WEST from Viridian City to Route 22, then NORTH through Route 23 (badge checkers verify all 8 badges at each gate) to reach the Victory Road cave entrance"),
    ( -12,  "Through Victory Road",                "Navigate all three floors of Victory Road (use HM04 Strength to push boulders) and exit north to reach Indigo Plateau"),
    (0x8FE, "Beat Lance (Elite Four)",             "Defeat all four Elite Four members at Indigo Plateau: Lorelei (Ice), Bruno (Fighting), Agatha (Ghost), and Lance (Dragon) — heal your team at the Pokémon Center first"),
    (0x901, "Beat Champion",                       "After defeating the Elite Four, defeat Blue (your rival) to become Pokémon Champion"),
    (0x8C1, "Caught Mewtwo",                       "Go to Cerulean Cave (accessible from north Cerulean City after becoming Champion — need HM03 Surf) and catch Mewtwo"),
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
    (0x027, 0x26): "Go downstairs (walk onto the staircase).",
    (0x027, 0x25): "Exit through the front door.",
    (0x027, 0x00): "Walk EAST and then NORTH to the map edge to enter Route 1.",
    (0x027, 0x0C): "Walk into the tall grass. Oak will appear automatically.",
    # Got starter (0x022)
    (0x022, 0x00): "Enter Oak's Lab (the large building in south Pallet Town).",
    (0x022, 0x28): "Walk to the table with Pokeballs and press A facing it to choose a starter.",
    # Battled rival (0x023)
    (0x023, 0x28): "Walk SOUTH toward the exit door — your rival will stop you automatically. Do NOT talk to him.",
    # Got Oak's Parcel (0x039)
    (0x039, 0x28): "Exit the lab and head NORTH through Route 1 to Viridian City Mart.",
    (0x039, 0x00): "Head NORTH to Route 1, then continue to Viridian City.",
    (0x039, 0x0C): "Keep heading NORTH to reach Viridian City.",
    (0x039, 0x01): "Enter the Poke Mart to pick up Oak's Parcel.",
    (0x039, 0x2A): "Talk to the shopkeeper to get Oak's Parcel.",
    # Got Pokedex (0x025)
    (0x025, 0x2A): "Exit the Mart and head SOUTH to Pallet Town.",
    (0x025, 0x01): "Head SOUTH toward Route 1 to return to Pallet Town.",
    (0x025, 0x0C): "Keep heading SOUTH to reach Pallet Town.",
    (0x025, 0x00): "Enter Oak's Lab (the large building in south Pallet Town) and deliver the parcel.",
    (0x025, 0x28): "Talk to Prof. Oak to deliver the parcel and receive the Pokedex.",
    # Through Viridian Forest (-1)
    (  -1,  0x00): "Head NORTH through Route 1 toward Viridian City.",
    (  -1,  0x0C): "Continue NORTH to Viridian City.",
    (  -1,  0x01): "Head WEST and then NORTH toward Viridian Forest South Gate.",
    (  -1,  0x0D): "Continue NORTH to Pewter City.",
    (  -1,  0x32): "Walk NORTH through the gate to enter Viridian Forest.",
    (  -1,  0x33): (
        "Viridian Forest is a winding maze — HEAD NORTH to reach Viridian Forest North Gate. "
        "Use the COMPASS bearings to track distance to exits and explore methodically, ruling out dead ends. "
        "Trust your NAV to find the correct path through the maze."
    ),
    (  -1,  0x2F): "Exit NORTH through Route 2 to reach Pewter City.",
    # Beat Brock (0x077)
    (0x077, 0x02): "Enter Pewter Gym by going NORTH and then WEST in town to find the path and defeat Brock.",
    (0x077, 0x36): "Battle the trainers and defeat Brock at the back of the gym. Head NORTH!",
    # Through Mt. Moon (-2)
    (  -2,  0x02): "Visit Pewter Mart first (Potions, Antidotes) — Mt. Moon has no shop. Mart is EAST of gym but a wall blocks direct access; follow the NAV path around the buildings. Then head EAST to Route 3.",
    (  -2,  0x0E): "Continue EAST through Route 3; Mt. Moon entrance is at the far east end.",
    (  -2,  0x44): "Pokémon Center rest stop.",
    (  -2,  0x3B): "Navigate Mt. Moon 1F — head NORTH toward the cave interior to find stairs down to Mt. Moon B1F. Do NOT use W0/W1 warps (wrong exit, ledges block east).",
    (  -2,  0x3C): "B1F has sections separated by walls. Find stairs to Mt. Moon B2F (north zone) to reach the fossil area and exit. Avoid warps that lead to B2F south dead-end. Explore unexplored passages to connect sections.",
    (  -2,  0x3D): "B2F has two disconnected zones. Goal: reach W1 to Mt. Moon B1F (east exit) for Route 4. If stuck in the south zone, return to B1F and find a different route to B2F north zone.",
    (  -2,  0x0F): "Route 4. If ledges (<) block eastward travel, you exited Mt. Moon from the WRONG side. Go back INTO Mt. Moon and progress through B1F→B2F→B1F to reach the correct Route 4 exit. If path east is clear, head EAST to Cerulean City.",
    # Beat Misty (0x0BF)
    (0x0BF, 0x03): "Enter Cerulean Gym (northeast area of Cerulean City) and defeat Misty.",
    (0x0BF, 0x41): "Battle the trainers and defeat Misty at the back of the gym.",
    # Got S.S. Ticket from Bill (-3)
    (  -3,  0x03): "Head NORTH from Cerulean City to Route 24, then continue EAST through Route 25 to find Bill's House.",
    (  -3,  0x23): "Continue EAST along Route 24 to reach Route 25.",
    (  -3,  0x24): "Bill's House is at the eastern end of Route 25.",
    (  -3,  0x58): "Talk to Bill — help him change back to human form to receive the S.S. Ticket.",
    # Got HM01 Cut (0x5E0)
    (0x5E0, 0x03): "Head SOUTH from Cerulean through Routes 5 and 6 (or underground path) to Vermilion City.",
    (0x5E0, 0x10): "Continue SOUTH through Route 5 toward Vermilion City.",
    (0x5E0, 0x11): "Continue SOUTH through Route 6 to Vermilion City.",
    (0x5E0, 0x05): "Head to the dock at the southeast part of Vermilion and board S.S. Anne (show your S.S. Ticket).",
    (0x5E0, 0x5E): "Board S.S. Anne — show the S.S. Ticket to the guard at the gangplank.",
    (0x5E0, 0x5F): "Explore S.S. Anne and find the captain's cabin.",
    (0x5E0, 0x65): "Talk to the seasick captain and rub his back to receive HM01 Cut.",
    # Beat Lt. Surge (0x167)
    (0x167, 0x05): "Find Vermilion Gym (south of the city). Teach HM01 Cut and use Cut on the small tree blocking the gym entrance.",
    (0x167, 0x5C): "Search adjacent trash cans to find two switches — flip both quickly to unlock the door, then defeat Lt. Surge.",
    # Got Silph Scope (-4)
    (  -4,  0x04): "Head WEST from Lavender Town toward Celadon City via Route 8.",
    (  -4,  0x05): "Head NORTH from Vermilion through Routes 6 and 5 toward Celadon City.",
    (  -4,  0x06): "Stock up at Celadon Dept Store (Super Potions, Antidotes) before heading underground — Rocket Hideout has no shop. Then find the Game Corner and enter.",
    (  -4,  0x87): "Find the hidden stairs to the Rocket Hideout (a Rocket Grunt near the poster in the corner conceals the switch).",
    (  -4,  0xC7): "Head NORTH to find stairs down to Rocket Hideout B2F.",
    (  -4,  0xC8): "Continue to Rocket Hideout B3F.",
    (  -4,  0xC9): "Continue to Rocket Hideout B4F — Giovanni is here.",
    (  -4,  0xCA): "Defeat Giovanni on B4F to receive the Silph Scope.",
    # Beat Erika (0x1A9)
    (0x1A9, 0x06): "Enter Celadon Gym (west side of Celadon City — enter through the tree passage in the back) and defeat Erika.",
    (0x1A9, 0x86): "Navigate through the grass trainers and defeat Erika at the back of the gym.",
    # Got Poke Flute (0x128)
    (0x128, 0x04): "Stock up at Lavender Town Poke Mart (Potions, Antidotes) before climbing — Pokémon Tower has no shop. Then enter the tower and climb to the 7th floor.",
    (0x128, 0x8E): "Climb NORTH up through Pokémon Tower 2F — find stairs to upper floors.",
    (0x128, 0x8F): "Continue NORTH up to Pokémon Tower 3F.",
    (0x128, 0x90): "Continue NORTH up to Pokémon Tower 4F.",
    (0x128, 0x91): "Continue NORTH up to Pokémon Tower 5F.",
    (0x128, 0x92): "Continue NORTH up to Pokémon Tower 6F.",
    (0x128, 0x93): "Continue NORTH up to Pokémon Tower 7F.",
    (0x128, 0x94): "On 7F: use the Silph Scope near the ghost to reveal Marowak — defeat it (no Pokeballs), then defeat the Rocket Grunts and free Mr. Fuji to receive the Poke Flute.",
    # Beat Koga (0x259)
    (0x259, 0x07): "Enter Fuchsia Gym and defeat Koga.",
    (0x259, 0x9D): "Navigate the invisible walls in Fuchsia Gym and defeat Koga.",
    # Got HM03 Surf (0x880)
    (0x880, 0x07): "Enter the Safari Zone gate (south part of Fuchsia City) and find the Secret House.",
    (0x880, 0x9C): "Enter the Safari Zone and head WEST then NORTH to find the Secret House.",
    (0x880, 0xDC): "Head WEST from Safari Zone Center toward the west area.",
    (0x880, 0xDB): "Head NORTH through Safari Zone West — the Secret House is in the northwest corner.",
    (0x880, 0xDE): "Talk to the man in the Secret House to receive HM03 Surf.",
    # Got Gold Teeth (-5)
    (  -5,  0x9C): "Enter the Safari Zone and explore the west area to find the Gold Teeth on the ground.",
    (  -5,  0xDC): "Head WEST from Safari Zone Center toward Safari Zone West.",
    (  -5,  0xDB): "Search Safari Zone West for the Gold Teeth item on the ground.",
    (  -5,  0x07): "Enter the Safari Zone gate (south Fuchsia City) to find the Gold Teeth.",
    # Got HM04 Strength from Warden (-6)
    (  -6,  0x9C): "Exit the Safari Zone and head to the Warden's House south of the Pokémon Center.",
    (  -6,  0x07): "Visit the Warden's House (south of Fuchsia Pokémon Center) and give him the Gold Teeth.",
    (  -6,  0x9B): "Give the Gold Teeth to the Safari Zone Warden to receive HM04 Strength.",
    # Bought Celadon drink (-7)
    (  -7,  0x07): "Head NORTH or WEST toward Celadon City to reach the Dept Store.",
    (  -7,  0x06): "Enter Celadon Dept Store (large building in east Celadon City) and take the elevator to the 5F rooftop.",
    (  -7,  0x7A): "Take the elevator to 5F (the rooftop) to reach the vending machines.",
    (  -7,  0x7E): "Buy a FRESH WATER, SODA POP, or LEMONADE from the vending machines — needed to bribe Saffron City guards.",
    # Cleared Silph Co. (-8)
    (  -8,  0x06): "Head EAST from Celadon to Saffron City — show a drink to the guard at the gate to enter.",
    (  -8,  0x0A): "Stock up at Saffron City Poke Mart (Hyper Potions, Revives, Full Heals) before entering — Silph Co. is a long multi-floor gauntlet with no shop. Then find Silph Co. (the large tower in the center of Saffron City) and enter.",
    (  -8,  0xB5): "Work through Silph Co. — use the Lift Key to access upper floors and defeat Rocket Grunts.",
    (  -8,  0xCF): "Continue through Silph Co. 2F — find the Lift Key if you haven't already.",
    (  -8,  0xD0): "Continue through Silph Co. 3F.",
    (  -8,  0xD1): "Continue through Silph Co. 4F.",
    (  -8,  0xD2): "Continue through Silph Co. 5F.",
    (  -8,  0xD3): "Continue through Silph Co. 6F.",
    (  -8,  0xD4): "You're deep in Silph Co. 7F — find and defeat Giovanni to liberate the building.",
    # Beat Sabrina (0x361)
    (0x361, 0x0A): "Enter Saffron Gym (now accessible after clearing Silph Co.) and defeat Sabrina.",
    (0x361, 0xB2): "Use the teleport pads to navigate Saffron Gym and defeat Sabrina.",
    # Surfed to Cinnabar Island (-9)
    (  -9,  0x00): "Use HM03 Surf to Surf SOUTH from Pallet Town — Cinnabar Island is directly south.",
    (  -9,  0x07): "Surf south from Fuchsia City through Routes 19 and 20 to Cinnabar Island.",
    (  -9,  0x1E): "Continue SOUTH through Route 19.",
    (  -9,  0x1F): "Continue WEST through Route 20 (Seafoam Islands are here).",
    (  -9,  0x20): "Continue SOUTH through Route 21 to reach Cinnabar Island.",
    # Got Secret Key from Pokémon Mansion (-10)
    ( -10,  0x08): "Stock up at Cinnabar Island Poke Mart (Hyper Potions, Revives, Antidotes) before entering — Pokémon Mansion has no shop. Then enter the Mansion (the ruined building in north Cinnabar Island) and navigate to the basement.",
    ( -10,  0xA5): "Explore Pokémon Mansion 1F — find stairs up through upper floors, eventually down to Pokémon Mansion B1F.",
    ( -10,  0xD6): "Continue through Pokémon Mansion 2F — find stairs up to Pokémon Mansion 3F.",
    ( -10,  0xD7): "Continue through Pokémon Mansion 3F — find stairs down to Pokémon Mansion B1F.",
    ( -10,  0xD8): "Search Pokémon Mansion B1F for the Secret Key on the ground.",
    # Beat Blaine (0x299)
    (0x299, 0x08): "Enter Cinnabar Gym (unlocked with the Secret Key) and defeat Blaine.",
    (0x299, 0xA6): "Answer the quiz questions (or fight skipped trainers) and defeat Blaine.",
    # Beat Giovanni Viridian Gym (0x051)
    (0x051, 0x01): "Enter Viridian Gym (north Viridian City — now open) and defeat Giovanni.",
    (0x051, 0x2D): "Navigate the spinning tiles in Viridian Gym and defeat Giovanni to earn the Earth Badge.",
    # Reached Victory Road (-11)
    ( -11,  0x01): "Head WEST from Viridian City to Route 22, then NORTH toward the Pokémon League.",
    ( -11,  0x21): "Continue NORTH through Route 22 to the Pokémon League gatehouse.",
    ( -11,  0x22): "Head NORTH through Route 23 — badge checkers verify all 8 badges at each gate.",
    # Through Victory Road (-12)
    ( -12,  0x22): "Last chance to stock up — buy Full Restores, Hyper Potions, and Revives from Viridian City before entering Victory Road (no shops inside). Then enter the cave at the north end of Route 23.",
    ( -12,  0x6C): "Navigate Victory Road 1F — use HM04 Strength to push boulders. Find stairs to Victory Road 2F.",
    ( -12,  0xC2): "Continue through Victory Road 2F — find stairs to Victory Road 3F.",
    ( -12,  0xC6): "Continue through Victory Road 3F to the exit.",
    ( -12,  0x09): "You've reached Indigo Plateau! Enter the Pokémon League building to face the Elite Four.",
    # Beat Lance / Elite Four (0x8FE)
    (0x8FE, 0x09): "Heal at the Pokémon Center, then buy Full Restores, Max Potions, and Revives from the Indigo Plateau Mart before entering the Pokémon League to face the Elite Four.",
    (0x8FE, 0xAE): "Heal at the Pokémon Center, then face: Lorelei (Ice), Bruno (Fighting), Agatha (Ghost), Lance (Dragon).",
}


def get_map_hint(next_flag: Optional[int], current_map_id: int) -> Optional[str]:
    """Return a context-aware hint for the current milestone + map, or None."""
    if next_flag is None:
        return None
    return MAP_HINTS.get((next_flag, current_map_id))
