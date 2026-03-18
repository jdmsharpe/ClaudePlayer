"""Pokemon Red party context reader.

Reads party RAM to give the agent awareness of its full team:
species, levels, HP, status conditions, moves + PP, and stats.
Always available (not battle-specific), enabling smart decisions
like healing at a Pokemon Center when the party is hurting.

RAM addresses and struct layout from pret/pokered wram.asm.
"""

import logging
from typing import Any, Dict, List, Optional

from pyboy import PyBoy

from claude_player.utils.ram_constants import ADDR_PARTY_COUNT as _ADDR_PARTY_COUNT
from claude_player.data.pokemon import POKEMON_NAMES, MOVE_DATA, HM_MOVE_IDS
from claude_player.utils.battle_context import _decode_status, _read_word

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gym data indexed by badge count (0 = haven't beaten Brock yet, etc.)
# (leader_name, gym_type, set_of_effective_types_to_have)
# ---------------------------------------------------------------------------

_NEXT_GYM_INFO = [
    ("Brock", "Rock", {"Water", "Grass", "Fighting", "Ground"}),
    ("Misty", "Water", {"Electric", "Grass"}),
    ("Lt. Surge", "Electric", {"Ground"}),
    ("Erika", "Grass", {"Fire", "Ice", "Flying", "Poison"}),
    ("Koga", "Poison", {"Psychic", "Ground"}),
    ("Sabrina", "Psychic", {"Bug"}),  # Gen 1: Ghost moves bugged vs Psychic
    ("Blaine", "Fire", {"Water", "Ground", "Rock"}),
    ("Giovanni", "Ground", {"Water", "Grass", "Ice"}),
    ("Elite Four", "Mixed", set()),  # No single-type recommendation
]

# Gen 1 type → set of types it's weak to (for mono-type warning)
_TYPE_WEAKNESSES: Dict[str, List[str]] = {
    "Normal": ["Fighting"],
    "Fire": ["Water", "Ground", "Rock"],
    "Water": ["Electric", "Grass"],
    "Grass": ["Fire", "Ice", "Flying", "Poison", "Bug"],
    "Electric": ["Ground"],
    "Ice": ["Fire", "Fighting", "Rock"],
    "Fighting": ["Flying", "Psychic"],
    "Poison": ["Ground", "Psychic", "Bug"],
    "Ground": ["Water", "Grass", "Ice"],
    "Rock": ["Water", "Grass", "Fighting", "Ground"],
    "Flying": ["Electric", "Ice", "Rock"],
    "Psychic": ["Bug"],  # Ghost bugged in Gen 1
    "Bug": ["Fire", "Flying", "Rock", "Poison"],
    "Ghost": ["Ghost"],  # Only self-weakness in Gen 1
    "Dragon": ["Ice", "Dragon"],
}
_ADDR_PARTY_SPECIES = 0xD164  # 6 bytes + 0xFF terminator

# Party mon struct base and size
_ADDR_PARTY_MON1 = 0xD16B  # First Pokemon struct
_PARTY_MON_SIZE = 44  # 0x2C bytes per Pokemon

# Nickname RAM: wPartyMon1Nick = 0xD2B5, 11 bytes each (10 chars + 0x50 terminator)
_ADDR_PARTY_NICK1 = 0xD2B5
_NICK_SIZE = 11

# Gen 1 character map (charmap.asm)
_GEN1_CHARMAP: Dict[int, str] = {
    0x7F: " ",
    **{0x80 + i: chr(ord("A") + i) for i in range(26)},  # A-Z
    **{0xA0 + i: chr(ord("a") + i) for i in range(26)},  # a-z
    **{0xF6 + i: str(i) for i in range(10)},             # 0-9
    0xE1: "PK", 0xE2: "MN",
    0xE3: "-", 0xE6: "?", 0xE7: "!",
    0xE8: ".", 0xF4: ",",
    0x60: "'",  # apostrophe (e.g. FARFETCH'D)
}


def _decode_nickname(pyboy: PyBoy, slot: int) -> str:
    """Read and decode a Gen 1 party Pokemon nickname."""
    base = _ADDR_PARTY_NICK1 + slot * _NICK_SIZE
    chars = []
    for i in range(10):
        b = pyboy.memory[base + i]
        if b == 0x50:  # terminator
            break
        chars.append(_GEN1_CHARMAP.get(b, ""))
    return "".join(chars)

# Offsets within each 44-byte party mon struct
_OFF_SPECIES = 0x00  # 1 byte
_OFF_HP = 0x01  # 2 bytes big-endian (current HP)
_OFF_STATUS = 0x04  # 1 byte (status condition)
_OFF_TYPE1 = 0x05  # 1 byte
_OFF_TYPE2 = 0x06  # 1 byte
_OFF_MOVES = 0x08  # 4 bytes (move IDs)
_OFF_EXP = 0x0E  # 3 bytes big-endian
_OFF_PP = 0x1D  # 4 bytes (bits 0-5 = PP, bits 6-7 = PP Up count)
_OFF_LEVEL = 0x21  # 1 byte (actual level)
_OFF_MAX_HP = 0x22  # 2 bytes big-endian
_OFF_ATTACK = 0x24  # 2 bytes big-endian
_OFF_DEFENSE = 0x26  # 2 bytes big-endian
_OFF_SPEED = 0x28  # 2 bytes big-endian
_OFF_SPECIAL = 0x2A  # 2 bytes big-endian

# Gen 1 type byte → display name
_TYPE_NAMES: Dict[int, str] = {
    0x00: "Normal",
    0x01: "Fighting",
    0x02: "Flying",
    0x03: "Poison",
    0x04: "Ground",
    0x05: "Rock",
    0x07: "Bug",
    0x08: "Ghost",
    0x14: "Fire",
    0x15: "Water",
    0x16: "Grass",
    0x17: "Electric",
    0x18: "Psychic",
    0x19: "Ice",
    0x1A: "Dragon",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_exp(pyboy: PyBoy, addr: int) -> int:
    """Read a 3-byte big-endian experience value."""
    return (
        (pyboy.memory[addr] << 16)
        | (pyboy.memory[addr + 1] << 8)
        | pyboy.memory[addr + 2]
    )


def _read_party_pokemon(pyboy: PyBoy, slot: int) -> Optional[Dict[str, Any]]:
    """Read one party Pokemon's data from RAM.

    Args:
        pyboy: PyBoy instance.
        slot: Party slot index (0-5).

    Returns:
        Dict with Pokemon data, or None if slot is empty.
    """
    base = _ADDR_PARTY_MON1 + (slot * _PARTY_MON_SIZE)

    species_id = pyboy.memory[base + _OFF_SPECIES]
    if species_id == 0:
        return None

    name = POKEMON_NAMES.get(species_id, f"???({species_id:#04x})")
    nickname = _decode_nickname(pyboy, slot)
    # Suppress nickname if it matches species name (default, no custom name set)
    if nickname.upper() == name.upper():
        nickname = ""
    hp = _read_word(pyboy, base + _OFF_HP)
    max_hp = _read_word(pyboy, base + _OFF_MAX_HP)
    level = pyboy.memory[base + _OFF_LEVEL]
    status = _decode_status(pyboy.memory[base + _OFF_STATUS])

    type1_id = pyboy.memory[base + _OFF_TYPE1]
    type2_id = pyboy.memory[base + _OFF_TYPE2]
    type1 = _TYPE_NAMES.get(type1_id, f"???({type1_id:#04x})")
    type2 = _TYPE_NAMES.get(type2_id, f"???({type2_id:#04x})")
    types = [type1] if type1 == type2 else [type1, type2]

    exp = _read_exp(pyboy, base + _OFF_EXP)

    attack = _read_word(pyboy, base + _OFF_ATTACK)
    defense = _read_word(pyboy, base + _OFF_DEFENSE)
    speed = _read_word(pyboy, base + _OFF_SPEED)
    special = _read_word(pyboy, base + _OFF_SPECIAL)

    # Read moves and PP
    moves: List[Dict[str, Any]] = []
    hm_moves: List[str] = []
    for i in range(4):
        move_id = pyboy.memory[base + _OFF_MOVES + i]
        if move_id == 0:
            break
        pp_raw = pyboy.memory[base + _OFF_PP + i]
        pp = pp_raw & 0x3F  # Lower 6 bits = current PP
        move_name, move_type, move_power, base_pp = MOVE_DATA.get(
            move_id, (f"Move#{move_id}", "???", 0, 0)
        )
        is_hm = move_id in HM_MOVE_IDS
        if is_hm:
            hm_moves.append(HM_MOVE_IDS[move_id])
        moves.append(
            {
                "name": move_name,
                "type": move_type,
                "power": move_power,
                "pp": pp,
                "base_pp": base_pp,
                "is_hm": is_hm,
            }
        )

    return {
        "slot": slot,
        "species_id": species_id,
        "name": name,
        "nickname": nickname,
        "level": level,
        "hp": hp,
        "max_hp": max_hp,
        "hp_pct": round(100 * hp / max_hp) if max_hp > 0 else 0,
        "status": status,
        "types": types,
        "exp": exp,
        "attack": attack,
        "defense": defense,
        "speed": speed,
        "special": special,
        "moves": moves,
        "hm_moves": hm_moves,
    }


# ---------------------------------------------------------------------------
# Health assessment — YOUR IMPLEMENTATION
# ---------------------------------------------------------------------------


def assess_party_health(party: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Assess overall party health and return a recommendation.

    Args:
        party: List of Pokemon dicts from _read_party_pokemon.

    Returns:
        Dict with keys:
            alive (int): count of non-fainted Pokemon
            fainted (int): count of fainted Pokemon
            total_hp_pct (int): weighted HP percentage across the team
            low_pp (bool): True if any alive Pokemon has all damage moves at 0 PP
            status_count (int): number of Pokemon with non-OK status conditions
            needs_healing (bool): True if party should visit a Pokemon Center
            recommendation (str|None): advice string for the agent, or None
    """
    # Compute base stats once
    total_hp = sum(mon["hp"] for mon in party)
    total_max_hp = sum(mon["max_hp"] for mon in party)
    total_hp_pct = round(100 * total_hp / total_max_hp) if total_max_hp > 0 else 0

    alive = sum(1 for mon in party if mon["hp"] > 0)
    fainted = sum(1 for mon in party if mon["hp"] == 0)
    status_count = sum(1 for mon in party if mon["status"] != "OK")
    poisoned = sum(1 for mon in party if mon["hp"] > 0 and mon["status"] == "PSN")

    # Check if any alive mon has all damage moves at 0 PP
    low_pp = any(
        all(move["pp"] == 0 for move in mon["moves"] if move["power"] > 0)
        for mon in party
        if mon["hp"] > 0 and any(m["power"] > 0 for m in mon["moves"])
    )

    # Lead Pokemon (slot 0) awareness
    lead = party[0]
    lead_fainted = lead["hp"] == 0
    lead_low_hp = lead["hp_pct"] <= 25 and lead["hp"] > 0
    lead_no_pp = (
        lead["hp"] > 0
        and any(m["power"] > 0 for m in lead["moves"])
        and all(m["pp"] == 0 for m in lead["moves"] if m["power"] > 0)
    )

    # Graduated recommendation (highest priority first)
    recommendation = None
    needs_healing = False

    heal_where = "Heal at Pokemon Center (or Mom in Pallet Town)"

    if alive == 0:
        recommendation = f"CRITICAL: All Pokemon fainted! {heal_where}!"
        needs_healing = True
    elif total_hp_pct < 25 or (fainted >= 2 and alive == 1):
        recommendation = f"URGENT: {heal_where}!"
        needs_healing = True
    elif total_hp_pct < 50 or fainted >= 2:
        recommendation = f"{heal_where} soon"
        needs_healing = True
    elif lead_no_pp:
        recommendation = f"Lead has no PP for damage moves — switch or {heal_where.lower()}"
        needs_healing = True
    elif poisoned > 0:
        # Gen 1: poison drains 1 HP every 4 steps in the overworld
        recommendation = f"Warning: {poisoned} poisoned — losing HP while walking!"
        needs_healing = poisoned >= alive  # only urgent if whole team is poisoned
    elif lead_low_hp:
        recommendation = "Lead Pokemon HP is low — consider healing"

    return {
        "alive": alive,
        "fainted": fainted,
        "total_hp_pct": total_hp_pct,
        "low_pp": low_pp,
        "status_count": status_count,
        "needs_healing": needs_healing,
        "recommendation": recommendation,
    }


# ---------------------------------------------------------------------------
# Team strategy assessment (pure — no RAM access)
# ---------------------------------------------------------------------------


def _assess_team_strategy(
    party: List[Dict[str, Any]], badge_count: int
) -> Dict[str, Any]:
    """Analyze the team composition and produce actionable tips.

    Args:
        party: List of Pokemon dicts from _read_party_pokemon.
        badge_count: Number of badges earned (0-8).

    Returns:
        Dict with party_types, move_types, next_gym info, has_coverage, tips.
    """
    alive = [mon for mon in party if mon["hp"] > 0]

    # Collect species types across alive Pokemon
    party_types: set[str] = set()
    for mon in alive:
        party_types.update(mon["types"])

    # Collect damaging move types across alive Pokemon
    move_types: set[str] = set()
    for mon in alive:
        for move in mon["moves"]:
            if move["power"] > 0:
                move_types.add(move["type"])

    # Next gym info
    next_gym = None
    has_coverage = False
    if badge_count < len(_NEXT_GYM_INFO):
        leader, gym_type, wanted = _NEXT_GYM_INFO[badge_count]
        has_coverage = bool(move_types & wanted) if wanted else True
        next_gym = {
            "leader": leader,
            "gym_type": gym_type,
            "wanted_types": wanted,
            "has_coverage": has_coverage,
        }

    # Build tips (max 3, priority order)
    tips: List[str] = []

    if len(party) < 4 and badge_count >= 1:
        tips.append(f"Catch more Pokemon — only {len(party)}/6 slots used")

    if next_gym and not has_coverage and next_gym["wanted_types"]:
        type_list = " or ".join(sorted(next_gym["wanted_types"]))
        tips.append(
            f"Catch/train a {type_list} type for "
            f"{next_gym['leader']} ({next_gym['gym_type']} gym)"
        )

    if len(alive) >= 2 and len(party_types) == 1:
        solo_type = next(iter(party_types))
        weaknesses = _TYPE_WEAKNESSES.get(solo_type, [])
        if weaknesses:
            tips.append(
                f"Team is all {solo_type} — "
                f"vulnerable to {', '.join(weaknesses)}, diversify"
            )

    return {
        "party_types": party_types,
        "move_types": move_types,
        "next_gym": next_gym,
        "has_coverage": has_coverage,
        "tips": tips[:3],
    }


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


def _format_party_text(
    party: List[Dict[str, Any]],
    health: Dict[str, Any],
    strategy: Optional[Dict[str, Any]] = None,
) -> str:
    """Format party data into a compact text block for the agent."""
    lines = ["=== PARTY STATUS ==="]

    for mon in party:
        type_str = "/".join(mon["types"])
        status_str = f" [{mon['status']}]" if mon["status"] != "OK" else ""

        # Compact move summary: name, PP, and HM tag
        move_parts = []
        for m in mon["moves"]:
            hm_tag = "[HM]" if m.get("is_hm") else ""
            move_parts.append(f"{m['name']}:{m['pp']}pp{hm_tag}")

        # Stats line
        stats_str = (
            f"Atk:{mon['attack']} Def:{mon['defense']} "
            f"Spd:{mon['speed']} Spc:{mon['special']}"
        )

        lines.append(
            f"  {mon['slot']+1}. {mon['name']} Lv{mon['level']} "
            f"({type_str}) HP:{mon['hp']}/{mon['max_hp']}"
            f"{status_str} — {', '.join(move_parts)}"
        )
        lines.append(f"     {stats_str}")

        # HM summary for this mon
        if mon.get("hm_moves"):
            lines.append(f"     HMs: {', '.join(mon['hm_moves'])}")

    # Team summary
    parts = [
        f"{health['alive']}/{health['alive']+health['fainted']} alive",
        f"Team HP:{health['total_hp_pct']}%",
    ]
    if health["fainted"] > 0:
        parts.append(f"{health['fainted']} fainted")
    if health["status_count"] > 0:
        parts.append(f"{health['status_count']} with status")
    if health["low_pp"]:
        parts.append("LOW PP")
    lines.append(f"  TEAM: {' | '.join(parts)}")

    if health.get("recommendation"):
        lines.append(f"  HEAL: {health['recommendation']}")

    # Strategy section
    if strategy:
        type_str = ", ".join(sorted(strategy["party_types"])) or "none"
        move_str = ", ".join(sorted(strategy["move_types"])) or "none"
        lines.append(f"  TYPES: {type_str} | Moves: {move_str}")

        gym = strategy.get("next_gym")
        if gym and gym["wanted_types"]:
            wanted = " or ".join(sorted(gym["wanted_types"]))
            lines.append(
                f"  NEXT GYM: {gym['leader']} ({gym['gym_type']}) "
                f"— want {wanted}"
            )

        for tip in strategy.get("tips", []):
            lines.append(f"  TIP: {tip}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_party_context(pyboy: PyBoy) -> Optional[Dict[str, Any]]:
    """Extract full party context from RAM.

    Always available (overworld and battle).  Must be called on the main
    thread (PyBoy access is not thread-safe).

    Returns dict with "text" key and structured party data,
    or None if party is empty / data not ready.
    """
    try:
        party_count = pyboy.memory[_ADDR_PARTY_COUNT]
        if party_count == 0 or party_count > 6:
            return None

        party: List[Dict[str, Any]] = []
        for slot in range(party_count):
            mon = _read_party_pokemon(pyboy, slot)
            if mon:
                party.append(mon)

        if not party:
            return None

        health = assess_party_health(party)

        # Badge count from wObtainedBadges (1 byte, each bit = 1 badge)
        badge_byte = pyboy.memory[0xD356]
        badge_count = bin(badge_byte).count("1")

        strategy = _assess_team_strategy(party, badge_count)
        text = _format_party_text(party, health, strategy)

        names = ", ".join(f"{m['name']} Lv{m['level']}" for m in party)
        logger.info(f"Party: {names} — {health['total_hp_pct']}% HP")

        return {
            "text": text,
            "party": party,
            "health": health,
            "strategy": strategy,
            "party_count": len(party),
        }
    except Exception as e:
        logger.error(f"Error extracting party context: {e}", exc_info=True)
        return None
