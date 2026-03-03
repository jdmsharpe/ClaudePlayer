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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RAM addresses (pret/pokered wram.asm)
# ---------------------------------------------------------------------------

_ADDR_NUM_BAG_ITEMS = 0xD31D  # 1 byte, max 20
_ADDR_BAG_ITEMS = 0xD31E      # pairs of (item_id, quantity), 2 bytes each
_ADDR_PLAYER_MONEY = 0xD347   # 3 bytes, BCD-encoded
_ADDR_OBTAINED_BADGES = 0xD356  # 1 byte bitfield

# ---------------------------------------------------------------------------
# Gen 1 item ID → display name
# Sourced from pret/pokered constants/item_constants.asm
# ---------------------------------------------------------------------------

_ITEM_NAMES: Dict[int, str] = {
    0x01: "MASTER BALL",
    0x02: "ULTRA BALL",
    0x03: "GREAT BALL",
    0x04: "POKE BALL",
    0x05: "TOWN MAP",
    0x06: "BICYCLE",
    0x07: "?????",
    0x08: "SAFARI BALL",
    0x09: "POKEDEX",
    0x0A: "MOON STONE",
    0x0B: "ANTIDOTE",
    0x0C: "BURN HEAL",
    0x0D: "ICE HEAL",
    0x0E: "AWAKENING",
    0x0F: "PARLYZ HEAL",
    0x10: "FULL RESTORE",
    0x11: "MAX POTION",
    0x12: "HYPER POTION",
    0x13: "SUPER POTION",
    0x14: "POTION",
    0x15: "BOULDERBADGE",
    0x16: "CASCADEBADGE",
    0x17: "THUNDERBADGE",
    0x18: "RAINBOWBADGE",
    0x19: "SOULBADGE",
    0x1A: "MARSHBADGE",
    0x1B: "VOLCANOBADGE",
    0x1C: "EARTHBADGE",
    0x1D: "ESCAPE ROPE",
    0x1E: "REPEL",
    0x1F: "OLD AMBER",
    0x20: "FIRE STONE",
    0x21: "THUNDERSTONE",
    0x22: "WATER STONE",
    0x23: "HP UP",
    0x24: "PROTEIN",
    0x25: "IRON",
    0x26: "CARBOS",
    0x27: "CALCIUM",
    0x28: "RARE CANDY",
    0x29: "DOME FOSSIL",
    0x2A: "HELIX FOSSIL",
    0x2B: "SECRET KEY",
    0x2C: "?????",
    0x2D: "BIKE VOUCHER",
    0x2E: "X ACCURACY",
    0x2F: "LEAF STONE",
    0x30: "CARD KEY",
    0x31: "NUGGET",
    0x32: "PP UP",
    0x33: "POKE DOLL",
    0x34: "FULL HEAL",
    0x35: "REVIVE",
    0x36: "MAX REVIVE",
    0x37: "GUARD SPEC.",
    0x38: "SUPER REPEL",
    0x39: "MAX REPEL",
    0x3A: "DIRE HIT",
    0x3B: "COIN",
    0x3C: "FRESH WATER",
    0x3D: "SODA POP",
    0x3E: "LEMONADE",
    0x3F: "S.S. TICKET",
    0x40: "GOLD TEETH",
    0x41: "X ATTACK",
    0x42: "X DEFEND",
    0x43: "X SPEED",
    0x44: "X SPECIAL",
    0x45: "COIN CASE",
    0x46: "OAK'S PARCEL",
    0x47: "ITEMFINDER",
    0x48: "SILPH SCOPE",
    0x49: "POKE FLUTE",
    0x4A: "LIFT KEY",
    0x4B: "EXP. ALL",
    0x4C: "OLD ROD",
    0x4D: "GOOD ROD",
    0x4E: "SUPER ROD",
    0x4F: "PP UP",
    0x50: "ETHER",
    0x51: "MAX ETHER",
    0x52: "ELIXER",
    0x53: "MAX ELIXER",
    # HMs and TMs
    0xC4: "HM01",  # Cut
    0xC5: "HM02",  # Fly
    0xC6: "HM03",  # Surf
    0xC7: "HM04",  # Strength
    0xC8: "HM05",  # Flash
    # TMs (0xC9 = TM01 through 0xFE = TM50, but only include commonly relevant ones)
    0xC9: "TM01",  0xCA: "TM02",  0xCB: "TM03",  0xCC: "TM04",  0xCD: "TM05",
    0xCE: "TM06",  0xCF: "TM07",  0xD0: "TM08",  0xD1: "TM09",  0xD2: "TM10",
    0xD3: "TM11",  0xD4: "TM12",  0xD5: "TM13",  0xD6: "TM14",  0xD7: "TM15",
    0xD8: "TM16",  0xD9: "TM17",  0xDA: "TM18",  0xDB: "TM19",  0xDC: "TM20",
    0xDD: "TM21",  0xDE: "TM22",  0xDF: "TM23",  0xE0: "TM24",  0xE1: "TM25",
    0xE2: "TM26",  0xE3: "TM27",  0xE4: "TM28",  0xE5: "TM29",  0xE6: "TM30",
    0xE7: "TM31",  0xE8: "TM32",  0xE9: "TM33",  0xEA: "TM34",  0xEB: "TM35",
    0xEC: "TM36",  0xED: "TM37",  0xEE: "TM38",  0xEF: "TM39",  0xF0: "TM40",
    0xF1: "TM41",  0xF2: "TM42",  0xF3: "TM43",  0xF4: "TM44",  0xF5: "TM45",
    0xF6: "TM46",  0xF7: "TM47",  0xF8: "TM48",  0xF9: "TM49",  0xFA: "TM50",
}

# Items that gate story progression
_KEY_ITEMS = {
    0x3F,  # S.S. Ticket
    0x46,  # Oak's Parcel
    0x48,  # Silph Scope
    0x49,  # Poke Flute
    0x30,  # Card Key
    0x4A,  # Lift Key
    0x2B,  # Secret Key
    0x40,  # Gold Teeth
    0x05,  # Town Map
    0x06,  # Bicycle
    0x09,  # Pokedex
    0x45,  # Coin Case
    0x47,  # Itemfinder
    0x2D,  # Bike Voucher
    0x4B,  # Exp. All
    0x4C,  # Old Rod
    0x4D,  # Good Rod
    0x4E,  # Super Rod
    0xC4,  # HM01 Cut
    0xC5,  # HM02 Fly
    0xC6,  # HM03 Surf
    0xC7,  # HM04 Strength
    0xC8,  # HM05 Flash
}

# Badge bit positions (bit 0 = Boulder, bit 7 = Earth)
_BADGE_NAMES = [
    "Boulder",    # bit 0 — Brock (Pewter)
    "Cascade",    # bit 1 — Misty (Cerulean)
    "Thunder",    # bit 2 — Lt. Surge (Vermilion)
    "Rainbow",    # bit 3 — Erika (Celadon)
    "Soul",       # bit 4 — Koga (Fuchsia)
    "Marsh",      # bit 5 — Sabrina (Saffron)
    "Volcano",    # bit 6 — Blaine (Cinnabar)
    "Earth",      # bit 7 — Giovanni (Viridian)
]

# HM item ID → (display name, required badge bit for field use)
_HM_BADGE_REQS: Dict[int, tuple] = {
    0xC4: ("Cut",      1),  # Cascade Badge (bit 1)
    0xC5: ("Fly",      2),  # Thunder Badge (bit 2)
    0xC6: ("Surf",     4),  # Soul Badge (bit 4)
    0xC7: ("Strength", 3),  # Rainbow Badge (bit 3)
    0xC8: ("Flash",    0),  # Boulder Badge (bit 0)
}

# Item categories for grouping
_BALL_IDS = {0x01, 0x02, 0x03, 0x04, 0x08}  # Master, Ultra, Great, Poke, Safari
_MEDICINE_IDS = {
    0x0B, 0x0C, 0x0D, 0x0E, 0x0F,  # Status heals
    0x10, 0x11, 0x12, 0x13, 0x14,  # HP restores
    0x34, 0x35, 0x36,              # Full Heal, Revive, Max Revive
    0x50, 0x51, 0x52, 0x53,        # Ethers/Elixirs
}
_BATTLE_ITEM_IDS = {
    0x2E, 0x37, 0x3A,  # X Accuracy, Guard Spec., Dire Hit
    0x41, 0x42, 0x43, 0x44,  # X Attack/Defend/Speed/Special
    0x33,  # Poke Doll
}


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

        name = _ITEM_NAMES.get(item_id, f"ITEM_{item_id:#04x}")
        is_key = item_id in _KEY_ITEMS

        # Categorize
        if item_id in _BALL_IDS:
            category = "ball"
        elif item_id in _MEDICINE_IDS:
            category = "medicine"
        elif item_id in _BATTLE_ITEM_IDS:
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
            badges.append(_BADGE_NAMES[bit])
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
    for hm_id, (hm_name, badge_bit) in _HM_BADGE_REQS.items():
        owned = hm_id in item_ids
        required_badge = _BADGE_NAMES[badge_bit]
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
                hm_label = f"HM0{list(_HM_BADGE_REQS.keys()).index(hm['hm_id']) + 1}"
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
