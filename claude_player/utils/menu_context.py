"""Pokemon Red overworld menu context reader.

Reads menu cursor RAM to identify which menu is active outside of battle
and provides structured navigation hints with compound input strings.
Only active when wIsInBattle == 0 and a menu/text window is visible.

RAM addresses from pret/pokered wram.asm.
"""

import logging
from typing import Any, Dict, List, Optional

from pyboy import PyBoy

from claude_player.utils.ram_constants import (
    ADDR_IS_IN_BATTLE,
    ADDR_MENU_ITEM,
    ADDR_MENU_TOP_X,
    ADDR_MENU_TOP_Y,
    ADDR_STATUS_FLAGS5,
    ADDR_WINDOW_Y,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RAM addresses (menu-specific)
# ---------------------------------------------------------------------------

_ADDR_MAX_MENU      = 0xCC28  # wMaxMenuItem (0-based max index)
_ADDR_MENU_TO_SWAP  = 0xCC35  # wMenuItemToSwap (non-zero = swap pending)
_ADDR_LIST_SCROLL   = 0xCC36  # wListScrollOffset
_ADDR_NAMING_TYPE   = 0xD07D  # wNamingScreenType (0=player,1=rival,2=pokemon)

# ---------------------------------------------------------------------------
# Menu fingerprinting — uses (Y, X, max_item) to disambiguate overlapping menus
# ---------------------------------------------------------------------------

# START menu items (with Pokedex = max 6 → 7 items)
_START_ITEMS_POKEDEX = ["POKEDEX", "POKEMON", "ITEM", "player", "SAVE", "OPTION", "EXIT"]
_START_ITEMS_NO_DEX = ["POKEMON", "ITEM", "player", "SAVE", "OPTION", "EXIT"]

_NAMING_TYPES = {0: "Player name", 1: "Rival name", 2: "Pokemon nickname"}


# ---------------------------------------------------------------------------
# Menu identification
# ---------------------------------------------------------------------------

def _identify_menu(top_y: int, top_x: int, max_item: int) -> str:
    """Identify menu type from cursor metadata and max_item.

    Uses Y/X position as primary signal, max_item to disambiguate
    overlapping coordinate ranges (party_list vs mart, party_submenu
    vs item_submenu).
    """
    # START menu: right column, Y=2, X=9-12
    if top_y == 2 and 9 <= top_x <= 12:
        return "start_menu"

    # OPTIONS menu: Y=3, X=6-8, max_item=7 (TEXT SPEED/BATTLE ANIM/STYLE)
    if top_y == 3 and 6 <= top_x <= 8:
        return "options_menu"

    # YES/NO: bottom-right, Y=6-8, X=11-15, always max_item=1
    if 6 <= top_y <= 8 and 11 <= top_x <= 15:
        return "yes_no"

    # Party list vs Mart: both near Y=0-2, X=0-2
    # Party list: Y=0-1, X=0, max_item = party_count-1 (1-5, usually 5)
    # Mart BUY/SELL/QUIT: max_item always 2, typically Y=0-2, X=1-2
    if 0 <= top_y <= 1 and top_x == 0:
        # X=0 is party list territory; mart uses X=1-2
        return "party_list"
    if top_y == 2 and top_x == 1 and max_item == 2:
        return "mart_menu"

    # Item list (bag): Y=2-3, X=3-6
    if 2 <= top_y <= 3 and 3 <= top_x <= 6:
        return "item_list"

    # Party submenu vs Item submenu: both bottom-right area
    # Party sub (STATS/SWITCH/CANCEL + field moves): max_item >= 2
    # Item sub (USE/TOSS): max_item = 1
    if 10 <= top_y <= 14 and 10 <= top_x <= 15:
        if max_item >= 2:
            return "party_submenu"
        return "item_submenu"

    return "unknown_menu"


# ---------------------------------------------------------------------------
# Menu-specific formatters
# ---------------------------------------------------------------------------

def _format_start_menu(cursor: int, max_item: int) -> str:
    """Format START menu with cursor and TIP."""
    items = _START_ITEMS_POKEDEX if max_item >= 6 else _START_ITEMS_NO_DEX
    lines = ["=== MENU CONTEXT ===", "MENU: Start Menu"]
    for i, item in enumerate(items):
        marker = " ← cursor" if i == cursor else ""
        prefix = "  > " if i == cursor else "    "
        lines.append(f"{prefix}{item}{marker}")

    # TIP: suggest navigating to POKEMON (most common need)
    pokemon_idx = 1 if max_item >= 6 else 0  # POKEMON position
    item_idx = 2 if max_item >= 6 else 1      # ITEM position

    if cursor == pokemon_idx:
        lines.append("TIP: Open party screen — send: A")
    elif cursor < pokemon_idx:
        downs = "D " * (pokemon_idx - cursor)
        lines.append(f"TIP: Open party screen — send: {downs.strip()} A")
    elif cursor > pokemon_idx:
        ups = "U " * (cursor - pokemon_idx)
        lines.append(f"TIP: Open party screen — send: {ups.strip()} A")

    return "\n".join(lines)


def _format_party_menu(
    cursor: int,
    max_item: int,
    swap_pending: int,
    party_data: Optional[Dict[str, Any]],
) -> str:
    """Format party list with HP/status and switch TIP."""
    lines = ["=== MENU CONTEXT ==="]
    if swap_pending:
        lines.append("MENU: Pokemon Party [SWAP MODE]")
    else:
        lines.append("MENU: Pokemon Party")

    party = party_data.get("party", []) if party_data else []
    lead_fainted = False
    first_alive_slot = None

    for i, mon in enumerate(party):
        status_str = f" [{mon['status']}]" if mon["status"] != "OK" else ""
        marker = " ← cursor" if i == cursor else ""
        if swap_pending and i == swap_pending - 1:
            marker = " [SWAP FROM]"
        fnt = ""
        if mon["hp"] == 0:
            fnt = " [FNT]"
            if i == 0:
                lead_fainted = True
        if mon["hp"] > 0 and first_alive_slot is None:
            first_alive_slot = i
        lines.append(
            f"  {i+1}. {mon['name']} Lv{mon['level']} "
            f"HP:{mon['hp']}/{mon['max_hp']}{status_str}{fnt}{marker}"
        )

    # TIP generation
    if swap_pending:
        # Swap mode: navigate to target, press A to open swap-confirm submenu, A again for SWAP
        if first_alive_slot is not None and party:
            target = party[first_alive_slot]
            nav = "D " * first_alive_slot
            tip_seq = f"{nav.strip()} A A".strip()
            lines.append(
                f"TIP: SWAP MODE — move cursor to {target['name']} then A (submenu) then A (SWAP). "
                f"send: {tip_seq}"
            )
        else:
            lines.append("TIP: SWAP MODE — navigate to target Pokemon, press A to open submenu, then A to select SWAP.")
    elif lead_fainted and first_alive_slot is not None:
        target = party[first_alive_slot]
        nav = "D " * first_alive_slot
        # Full sequence: A(open fainted submenu) D(to SWITCH) A(confirm) nav(to target) A(submenu) A(SWAP)
        tip_seq = f"A D A {nav.strip()} A A".strip()
        lines.append(
            f"TIP: Fainted lead does NOT block walking — press B to close and keep moving. "
            f"Or swap now: send: {tip_seq} "
            f"(open CATERPIE menu → SWITCH → confirm → move to {target['name']} → SWAP → confirm)"
        )
    else:
        lines.append("TIP: Select a Pokemon with A to view stats/use field move. B to close.")

    return "\n".join(lines)


def _format_yes_no(cursor: int) -> str:
    """Format YES/NO prompt with TIP."""
    lines = ["=== MENU CONTEXT ===", "MENU: YES/NO Prompt"]
    lines.append(f"  {'> ' if cursor == 0 else '  '}YES{' ← cursor' if cursor == 0 else ''}")
    lines.append(f"  {'> ' if cursor == 1 else '  '}NO{' ← cursor' if cursor == 1 else ''}")

    if cursor == 0:
        lines.append("TIP: Select YES — send: A")
    else:
        lines.append("TIP: Select YES — send: U A  |  Select NO — send: A")

    return "\n".join(lines)


def _format_item_list(
    cursor: int,
    scroll_offset: int,
    bag_data: Optional[Dict[str, Any]],
) -> str:
    """Format bag item list with cursor."""
    lines = ["=== MENU CONTEXT ===", f"MENU: Item Bag (scroll:{scroll_offset})"]

    items = []
    if bag_data and bag_data.get("assessment"):
        items = bag_data["assessment"].get("items_detail", [])

    if items:
        # Show visible window of items
        visible_start = scroll_offset
        visible_end = min(visible_start + 4, len(items))
        for i in range(visible_start, visible_end):
            idx_in_view = i - visible_start
            marker = " ← cursor" if idx_in_view == cursor else ""
            item = items[i]
            lines.append(f"  {item.get('name', '???')} x{item.get('qty', '?')}{marker}")
    else:
        lines.append(f"  Cursor at position {cursor+1} (0-indexed {cursor})")

    lines.append("TIP: A to select item, B to close bag.")

    return "\n".join(lines)


def _format_item_submenu(cursor: int) -> str:
    """Format USE/TOSS submenu."""
    lines = ["=== MENU CONTEXT ===", "MENU: Item Action"]
    options = ["USE", "TOSS"]
    for i, opt in enumerate(options):
        marker = " ← cursor" if i == cursor else ""
        lines.append(f"  {'> ' if i == cursor else '  '}{opt}{marker}")

    if cursor == 0:
        lines.append("TIP: Use item — send: A")
    else:
        lines.append("TIP: Use item — send: U A  |  Toss item — send: A")

    return "\n".join(lines)


def _format_party_submenu(cursor: int, max_item: int) -> str:
    """Format party action submenu (field moves + STATS/SWITCH/CANCEL).

    Gen 1 party submenu order: [field_move_1, ..., STATS, SWITCH, CANCEL]
    CANCEL is always last (max_item), SWITCH second-to-last, STATS third-to-last.
    """
    lines = ["=== MENU CONTEXT ===", "MENU: Pokemon Action"]

    # Build option list: field moves (if any) + STATS + SWITCH + CANCEL
    options = []
    num_field_moves = max_item - 2  # STATS + SWITCH + CANCEL = 3 fixed slots
    for i in range(num_field_moves):
        options.append(f"FIELD MOVE {i + 1}")
    options.extend(["STATS", "SWITCH", "CANCEL"])

    for i, opt in enumerate(options):
        marker = " ← cursor" if i == cursor else ""
        prefix = "  > " if i == cursor else "    "
        lines.append(f"{prefix}{opt}{marker}")

    # TIP: most common need is SWITCH
    switch_idx = max_item - 1
    if cursor == switch_idx:
        lines.append("TIP: Confirm SWITCH — send: A")
    elif cursor < switch_idx:
        downs = "D " * (switch_idx - cursor)
        lines.append(f"TIP: Select SWITCH — send: {downs.strip()} A")
    else:
        # cursor on CANCEL, go up to SWITCH
        lines.append("TIP: Select SWITCH — send: U A  |  Cancel — send: A")

    return "\n".join(lines)


def _format_options_menu(cursor: int, max_item: int) -> str:
    """Format OPTIONS settings screen."""
    lines = [
        "=== MENU CONTEXT ===",
        "MENU: Options",
        f"  Cursor: slot {cursor+1} of {max_item+1}",
        "TIP: Press B to close options and return to START menu.",
    ]
    return "\n".join(lines)


def _format_mart_menu(cursor: int) -> str:
    """Format Mart Buy/Sell/Quit menu."""
    lines = ["=== MENU CONTEXT ===", "MENU: Poke Mart"]
    options = ["BUY", "SELL", "QUIT"]
    for i, opt in enumerate(options):
        marker = " ← cursor" if i == cursor else ""
        lines.append(f"  {'> ' if i == cursor else '  '}{opt}{marker}")

    if cursor == 0:
        lines.append("TIP: Buy items — send: A")
    elif cursor == 1:
        lines.append("TIP: Sell items — send: A  |  Buy items — send: U A")
    else:
        lines.append("TIP: Exit mart — send: A  |  Buy items — send: U U A")

    return "\n".join(lines)


def _format_naming_screen(pyboy: PyBoy) -> str:
    """Format naming screen with TIP."""
    naming_type = pyboy.memory[_ADDR_NAMING_TYPE]
    type_name = _NAMING_TYPES.get(naming_type, f"Unknown ({naming_type})")

    lines = [
        "=== MENU CONTEXT ===",
        f"MENU: Naming Screen ({type_name})",
        "TIP: Press START to finalize current name. "
        "Or use arrows to navigate alphabet, A to type letter.",
    ]
    return "\n".join(lines)


def _format_unknown_menu(cursor: int, max_item: int, top_y: int, top_x: int) -> str:
    """Format unknown menu with generic cursor info."""
    lines = [
        "=== MENU CONTEXT ===",
        f"MENU: Unknown (Y={top_y}, X={top_x})",
        f"  Cursor: slot {cursor+1} of {max_item+1}",
        "TIP: Use D/U to navigate, A to select, B to cancel/close.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_menu_context(
    pyboy: PyBoy,
    party_data: Optional[Dict[str, Any]] = None,
    bag_data: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Extract overworld menu context from RAM.

    Only returns data when a menu is active outside of battle.
    Must be called on the main thread (PyBoy access is not thread-safe).

    Args:
        pyboy: PyBoy instance
        party_data: Pre-extracted party context (reused for party menu display)
        bag_data: Pre-extracted bag context (reused for item list display)

    Returns:
        Dict with "text", "menu_type", "cursor", etc. or None if no menu active.
    """
    try:
        # Gate 1: not in battle (battle_context handles battle menus)
        if pyboy.memory[ADDR_IS_IN_BATTLE] != 0:
            return None

        # Gate 2: menu/text window is active
        status5 = pyboy.memory[ADDR_STATUS_FLAGS5]
        wy = pyboy.memory[ADDR_WINDOW_Y]
        if not ((status5 & 0x01) or (wy < 144)):
            return None

        # Read cursor metadata
        top_y = pyboy.memory[ADDR_MENU_TOP_Y]
        top_x = pyboy.memory[ADDR_MENU_TOP_X]
        cursor = pyboy.memory[ADDR_MENU_ITEM]
        max_item = pyboy.memory[_ADDR_MAX_MENU]
        swap_pending = pyboy.memory[_ADDR_MENU_TO_SWAP]
        scroll_offset = pyboy.memory[_ADDR_LIST_SCROLL]

        # Check for naming screen first (special case — doesn't use standard menu)
        naming_type = pyboy.memory[_ADDR_NAMING_TYPE]
        # Naming screen sets wNamingScreenType to 0-2, but this byte might be stale.
        # Combine with menu Y/X: naming screen cursor starts around Y=4-5, X=1-3
        # and max_item is 6-7 (for the 5-row alphabet + ED/case rows).
        if naming_type <= 2 and max_item >= 5 and 3 <= top_y <= 5 and top_x <= 3:
            text = _format_naming_screen(pyboy)
            return {
                "text": text,
                "menu_type": "naming_screen",
                "cursor": cursor,
                "max_item": max_item,
                "top_y": top_y,
                "top_x": top_x,
            }

        # Identify menu from cursor metadata + max_item
        menu_type = _identify_menu(top_y, top_x, max_item)

        # Format based on menu type
        if menu_type == "start_menu":
            text = _format_start_menu(cursor, max_item)
        elif menu_type == "party_list":
            text = _format_party_menu(cursor, max_item, swap_pending, party_data)
        elif menu_type == "party_submenu":
            text = _format_party_submenu(cursor, max_item)
        elif menu_type == "yes_no":
            text = _format_yes_no(cursor)
        elif menu_type == "item_list":
            text = _format_item_list(cursor, scroll_offset, bag_data)
        elif menu_type == "item_submenu":
            text = _format_item_submenu(cursor)
        elif menu_type == "mart_menu":
            text = _format_mart_menu(cursor)
        elif menu_type == "options_menu":
            text = _format_options_menu(cursor, max_item)
        else:
            text = _format_unknown_menu(cursor, max_item, top_y, top_x)

        logger.info(
            f"Menu context: {menu_type} cursor={cursor}/{max_item} "
            f"Y={top_y} X={top_x} swap={swap_pending}"
        )

        return {
            "text": text,
            "menu_type": menu_type,
            "cursor": cursor,
            "max_item": max_item,
            "top_y": top_y,
            "top_x": top_x,
        }
    except Exception as e:
        logger.error(f"Error extracting menu context: {e}", exc_info=True)
        return None
