"""Pokemon Red bag/inventory context reader.

Reads bag RAM to give the agent awareness of its items, money, and badges.
Enables smart decisions like knowing it has healing items, which HMs are
usable, and whether progression items are missing for the current goal.

Bag state changes infrequently (pickup/use/buy), so context is injected
only on change or periodically — same smart injection pattern as party_context.

RAM addresses and item IDs from pret/pokered wram.asm and constants/.
"""

import logging
from typing import Any, Dict, List, Optional

from pyboy import PyBoy

from claude_player.utils.ram_constants import (
    ADDR_NUM_BAG_ITEMS as _ADDR_NUM_BAG_ITEMS,
    ADDR_BAG_ITEMS as _ADDR_BAG_ITEMS,
    ADDR_PLAYER_MONEY as _ADDR_PLAYER_MONEY,
    ADDR_OBTAINED_BADGES as _ADDR_OBTAINED_BADGES,
)
from claude_player.data.items import (
    ITEM_NAMES, KEY_ITEMS, BADGE_NAMES, HM_BADGE_REQS,
    BALL_IDS, MEDICINE_IDS, BATTLE_ITEM_IDS,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_bcd_money(pyboy: PyBoy, addr: int) -> int:
    """Decode 3-byte BCD-encoded money value.

    Each byte stores two decimal digits (high nibble, low nibble).
    Example: 0x01 0x23 0x45 → 12345.
    """
    total = 0
    for i in range(3):
        byte = pyboy.memory[addr + i]
        high = (byte >> 4) & 0x0F
        low = byte & 0x0F
        total = total * 100 + high * 10 + low
    return total


def _read_bag_items(pyboy: PyBoy) -> List[Dict[str, Any]]:
    """Read all bag item slots from RAM.

    Returns list of dicts with: id, name, quantity, is_key_item, category.
    """
    count = pyboy.memory[_ADDR_NUM_BAG_ITEMS]
    if count == 0 or count > 20:
        return []

    items = []
    for i in range(count):
        addr = _ADDR_BAG_ITEMS + (i * 2)
        item_id = pyboy.memory[addr]
        quantity = pyboy.memory[addr + 1]

        if item_id == 0xFF:  # List terminator
            break

        name = ITEM_NAMES.get(item_id, f"ITEM_{item_id:#04x}")
        is_key = item_id in KEY_ITEMS

        # Categorize
        if item_id in BALL_IDS:
            category = "ball"
        elif item_id in MEDICINE_IDS:
            category = "medicine"
        elif item_id in BATTLE_ITEM_IDS:
            category = "battle"
        elif 0xC4 <= item_id <= 0xC8:
            category = "hm"
        elif 0xC9 <= item_id <= 0xFA:
            category = "tm"
        elif is_key:
            category = "key"
        else:
            category = "other"

        items.append({
            "id": item_id,
            "name": name,
            "quantity": quantity,
            "is_key_item": is_key,
            "category": category,
        })

    return items


def _read_badges(pyboy: PyBoy) -> List[str]:
    """Read badge bitfield and return list of obtained badge names."""
    badge_byte = pyboy.memory[_ADDR_OBTAINED_BADGES]
    badges = []
    for bit in range(8):
        if badge_byte & (1 << bit):
            badges.append(BADGE_NAMES[bit])
    return badges


# ---------------------------------------------------------------------------
# Inventory assessment
# ---------------------------------------------------------------------------


def assess_inventory(
    items: List[Dict[str, Any]],
    badges: List[str],
    money: int,
) -> Dict[str, Any]:
    """Analyze inventory for progression-relevant information.

    Returns dict with:
        key_items: list of key item names held
        hm_status: list of dicts {name, owned, badge_unlocked, usable}
        healing_items: total count of HP/status restore items
        pokeballs: total count of all ball types
        money: current money
        badge_count: number of badges
        warnings: list of progression warnings
    """
    # Build item lookup by ID
    item_ids = {item["id"] for item in items}

    # Key items
    key_items = [item["name"] for item in items if item["is_key_item"]]

    # HM status
    badge_set = set(badges)
    hm_status = []
    for hm_id, (hm_name, badge_bit) in HM_BADGE_REQS.items():
        owned = hm_id in item_ids
        required_badge = BADGE_NAMES[badge_bit]
        badge_unlocked = required_badge in badge_set
        hm_status.append({
            "name": hm_name,
            "hm_id": hm_id,
            "owned": owned,
            "required_badge": required_badge,
            "badge_unlocked": badge_unlocked,
            "usable": owned and badge_unlocked,
        })

    # Counts
    healing_items = sum(
        item["quantity"] for item in items if item["category"] == "medicine"
    )
    pokeballs = sum(
        item["quantity"] for item in items if item["category"] == "ball"
    )

    # Progression warnings
    warnings = []

    # HM warnings: have HM but not the badge, or need HM for common blockers
    for hm in hm_status:
        if hm["owned"] and not hm["badge_unlocked"]:
            warnings.append(
                f"Have HM {hm['name']} but need {hm['required_badge']} Badge to use it"
            )

    # Common progression hints based on badge count
    badge_count = len(badges)
    if badge_count >= 1 and not any(h["owned"] for h in hm_status if h["name"] == "Cut"):
        warnings.append("Need HM01 Cut from S.S. Anne captain")
    if badge_count >= 4 and not any(h["owned"] for h in hm_status if h["name"] == "Surf"):
        warnings.append("Need HM03 Surf from Safari Zone warden")
    if badge_count >= 4 and not any(h["owned"] for h in hm_status if h["name"] == "Strength"):
        warnings.append("Need HM04 Strength from Safari Zone warden")

    # No Poke Balls warning (early game)
    if pokeballs == 0 and badge_count < 2:
        warnings.append("No Poke Balls — buy some to catch Pokemon")

    return {
        "key_items": key_items,
        "hm_status": hm_status,
        "healing_items": healing_items,
        "pokeballs": pokeballs,
        "money": money,
        "badge_count": badge_count,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


def _format_bag_text(
    items: List[Dict[str, Any]],
    badges: List[str],
    assessment: Dict[str, Any],
) -> str:
    """Format bag data into a compact text block for the agent."""
    lines = ["=== INVENTORY ==="]

    # Badges
    badge_str = ", ".join(badges) if badges else "None"
    lines.append(f"  Badges: {len(badges)}/8 ({badge_str})")

    # Money
    lines.append(f"  Money: ${assessment['money']}")

    # Key items with HM usability markers
    if assessment["key_items"]:
        key_parts = []
        for name in assessment["key_items"]:
            # Mark HMs with usability
            for hm in assessment["hm_status"]:
                hm_label = f"HM0{list(HM_BADGE_REQS.keys()).index(hm['hm_id']) + 1}"
                if hm_label in name:
                    suffix = "\u2713" if hm["usable"] else f"(need {hm['required_badge']})"
                    name = f"{name} {hm['name']}{suffix}"
                    break
            key_parts.append(name)
        lines.append(f"  Key: {', '.join(key_parts)}")

    # Poke Balls
    ball_items = [item for item in items if item["category"] == "ball"]
    if ball_items:
        ball_parts = [f"{b['name']} x{b['quantity']}" for b in ball_items]
        lines.append(f"  Balls: {', '.join(ball_parts)}")

    # Medicine
    med_items = [item for item in items if item["category"] == "medicine"]
    if med_items:
        med_parts = [f"{m['name']} x{m['quantity']}" for m in med_items]
        lines.append(f"  Medicine: {', '.join(med_parts)}")

    # Battle items
    battle_items = [item for item in items if item["category"] == "battle"]
    if battle_items:
        battle_parts = [f"{b['name']} x{b['quantity']}" for b in battle_items]
        lines.append(f"  Battle: {', '.join(battle_parts)}")

    # TMs (compact — just count + list names)
    tm_items = [item for item in items if item["category"] == "tm"]
    if tm_items:
        tm_names = [t["name"] for t in tm_items]
        lines.append(f"  TMs: {', '.join(tm_names)}")

    # Other uncategorized items
    other_items = [
        item for item in items
        if item["category"] == "other" and not item["is_key_item"]
    ]
    if other_items:
        other_parts = [f"{o['name']} x{o['quantity']}" for o in other_items]
        lines.append(f"  Other: {', '.join(other_parts)}")

    # Warnings
    for warning in assessment["warnings"]:
        lines.append(f"  WARNING: {warning}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_bag_context(pyboy: PyBoy) -> Optional[Dict[str, Any]]:
    """Extract bag/inventory context from RAM.

    Always available (overworld and battle). Must be called on the main
    thread (PyBoy access is not thread-safe).

    Returns dict with "text" key and structured inventory data,
    or None if bag is empty / data not ready.
    """
    try:
        items = _read_bag_items(pyboy)
        badges = _read_badges(pyboy)
        money = _read_bcd_money(pyboy, _ADDR_PLAYER_MONEY)

        assessment = assess_inventory(items, badges, money)
        text = _format_bag_text(items, badges, assessment)

        # Build snapshot for change detection: (item_id, qty) tuples + money + badges
        snapshot = (
            tuple((item["id"], item["quantity"]) for item in items),
            money,
            assessment["badge_count"],
        )

        item_count = len(items)
        logger.info(
            f"Bag: {item_count} items, {assessment['badge_count']} badges, "
            f"${money}"
        )

        return {
            "text": text,
            "items": items,
            "badges": badges,
            "assessment": assessment,
            "snapshot": snapshot,
            "money": money,
        }
    except Exception as e:
        logger.error(f"Error extracting bag context: {e}", exc_info=True)
        return None
