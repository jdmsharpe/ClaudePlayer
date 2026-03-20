"""Pokémon Red battle context reader.

Reads battle RAM to provide structured data about the current fight:
both Pokémon's stats, available moves with power/PP, and menu cursor
position.  Injected as text context during battles, replacing the
spatial grid (which is useless on the battle screen).

RAM addresses and data tables sourced from the pret/pokered disassembly.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from pyboy import PyBoy

from claude_player.data.pokemon import (
    POKEMON_NAMES, MOVE_DATA, TYPE_CHART, TYPE_NAMES,
    SPECIAL_TYPES, HM_MOVE_IDS, INTERNAL_TO_DEX,
    STAGE_MULTS, HP_ITEMS, STATUS_CURE_ITEMS,
    RARE_POKEMON, SLEEP_MOVE_IDS, PARALYZE_MOVE_IDS,
)
from claude_player.utils.ram_constants import (
    ADDR_BAG_ITEMS,
    ADDR_CUR_MAP,
    ADDR_IS_IN_BATTLE,
    ADDR_MENU_ITEM,
    ADDR_MENU_TOP_X,
    ADDR_MENU_TOP_Y,
    ADDR_NUM_BAG_ITEMS,
    ADDR_PARTY_BASE,
    ADDR_PARTY_COUNT,
    ADDR_POKEDEX_OWNED,
    ADDR_STATUS_FLAGS5,
    PARTY_MON_SIZE,
    decode_status,
    read_word,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RAM addresses (battle-specific, not shared)
# ---------------------------------------------------------------------------

# Player's active Pokémon (wBattleMon)
_ADDR_PLAYER_SPECIES = 0xD014
_ADDR_PLAYER_HP      = 0xD015  # 2 bytes, big-endian
_ADDR_PLAYER_STATUS  = 0xD018
_ADDR_PLAYER_MOVES   = 0xD01C  # 4 bytes (move IDs)
_ADDR_PLAYER_LEVEL   = 0xD022
_ADDR_PLAYER_MAX_HP  = 0xD023  # 2 bytes, big-endian
_ADDR_PLAYER_ATK     = 0xD025  # 2 bytes, big-endian (in-battle stat)
_ADDR_PLAYER_DEF     = 0xD027
_ADDR_PLAYER_SPD     = 0xD029
_ADDR_PLAYER_SPC     = 0xD02B
_ADDR_PLAYER_PP      = 0xD02D  # 4 bytes (PP per move slot)
_ADDR_PLAYER_TYPE1   = 0xD019
_ADDR_PLAYER_TYPE2   = 0xD01A

# Enemy's active Pokémon (wEnemyMon)
_ADDR_ENEMY_SPECIES  = 0xCFE5
_ADDR_ENEMY_HP       = 0xCFE6  # 2 bytes, big-endian
_ADDR_ENEMY_STATUS   = 0xCFE9
_ADDR_ENEMY_TYPE1    = 0xCFEA
_ADDR_ENEMY_TYPE2    = 0xCFEB
_ADDR_ENEMY_MOVES    = 0xCFED  # 4 bytes
_ADDR_ENEMY_LEVEL    = 0xCFF3
_ADDR_ENEMY_MAX_HP   = 0xCFF4  # 2 bytes, big-endian
_ADDR_ENEMY_ATK      = 0xCFF6
_ADDR_ENEMY_DEF      = 0xCFF8
_ADDR_ENEMY_SPD      = 0xCFFA
_ADDR_ENEMY_SPC      = 0xCFFC
_ADDR_ENEMY_PP       = 0xCFFE  # 4 bytes (PP per move slot)

# CC2F is dual-purpose in battle: it stores the party index of the currently-sent-out
# Pokémon (0-5) for the send-out flow, AND the last A-confirmed fight-menu slot (0-3).
# We clamp to num_moves-1 and pass it to _fight_nav_presses as `current`
# for relative navigation (the fight menu wraps, so absolute reset doesn't work).
_ADDR_PLAYER_MOVE_LIST_IDX = 0xCC2F

# Safari Zone
_SAFARI_ZONE_MAPS      = frozenset({0xD9, 0xDA, 0xDB, 0xDC})  # wCurMap values (outdoor areas only)
_ADDR_SAFARI_STEPS     = 0xD21B  # wSafariSteps: 2-byte big-endian, total steps remaining
_ADDR_NUM_SAFARI_BALLS = 0xD21D  # wNumSafariBalls: Safari Balls left (0-30)
_ADDR_SAFARI_BAIT      = 0xD21E  # wSafariBaitThrowCount: bait throws on current encounter
_ADDR_SAFARI_ROCK      = 0xD21F  # wSafariRockThrowCount: rock throws on current encounter

# Battle turn state
_ADDR_BATTLE_TURN_COUNT  = 0xCCD5  # Turns elapsed in current battle
_ADDR_MOVE_MENU_TYPE     = 0xCCDB  # Move menu type: 0=regular, 1=mimic, other=text/PP-fill
_ADDR_PLAYER_MOVE_CHOSEN = 0xCCDC  # Move the player confirmed this turn (0-3 index)
_ADDR_ENEMY_MOVE_CHOSEN  = 0xCCDD  # Move the enemy confirmed this turn (0-3 index)
_ADDR_BATTLE_WHOSE_TURN  = 0xFFF3  # Current battle half-turn: 0=player, 1=opponent

# In-battle stat stage modifiers — stored as 0-12 where 7 = neutral; stage = value - 7
_ADDR_PLAYER_ATK_MOD = 0xCD1A
_ADDR_PLAYER_DEF_MOD = 0xCD1B
_ADDR_PLAYER_SPD_MOD = 0xCD1C
_ADDR_PLAYER_SPC_MOD = 0xCD1D
_ADDR_PLAYER_ACC_MOD = 0xCD1E
_ADDR_PLAYER_EVA_MOD = 0xCD1F
# CD2D = engaged trainer class / legendary Pokémon ID (not a stage modifier)
_ADDR_ENEMY_ATK_MOD  = 0xCD2E  # Enemy ATK stage (or trainer roster ID outside battle)
_ADDR_ENEMY_DEF_MOD  = 0xCD2F
_ADDR_ENEMY_SPD_MOD  = 0xCD30
_ADDR_ENEMY_SPC_MOD  = 0xCD31
_ADDR_ENEMY_ACC_MOD  = 0xCD32
_ADDR_ENEMY_EVA_MOD  = 0xCD33

# Bag / party constants (battle-specific)
# Regular Poke Balls only (Safari Ball 0x08 excluded — Safari Zone uses
# its own counter at _ADDR_NUM_SAFARI_BALLS, not the bag).
_BATTLE_BALL_IDS            = {0x01, 0x02, 0x03, 0x04}  # Master, Ultra, Great, Poke
_PARTY_HP_OFFSET     = 1       # HP is 2-byte big-endian at offset 1
_PARTY_LEVEL_OFFSET  = 0x21   # 1 byte — actual level

# ---------------------------------------------------------------------------
# Gen 1 type system
# ---------------------------------------------------------------------------

def _type_effectiveness(move_type: str, defend_types: List[str]) -> float:
    """Compute total type effectiveness multiplier for a move vs defender types."""
    mult = 1.0
    for dt in defend_types:
        mult *= TYPE_CHART.get((move_type, dt), 1.0)
    return mult



def _effective_power(
    move_slot_pair,
    enemy_types=None,
    player_stats: Optional[Dict[str, int]] = None,
    enemy_stats: Optional[Dict[str, int]] = None,
    player_types: Optional[List[str]] = None,
    player_status: str = "OK",
) -> float:
    """Estimated damage score accounting for Gen 1 mechanics:
    - Type effectiveness vs defender
    - STAB (1.5x when move type matches user's type)
    - Physical/Special stat split (Attack+Defense vs Special+Special)
    - Burn penalty (halves physical offense in Gen 1 damage calc)
    """
    m = move_slot_pair[0]
    base = m["power"]
    if base <= 0:
        return 0.0
    # Fixed-damage moves (power=1) like NIGHT SHADE, SEISMIC TOSS, DRAGON RAGE
    # ignore type effectiveness entirely in Gen 1 — they always deal fixed damage
    name = m.get("name", "")
    if base == 1 and any(k in name for k in ("NIGHT SHADE", "SEISMIC", "DRAGON RAGE", "SONICBOOM", "PSYWAVE")):
        return 1.0  # flat score; _estimate_damage handles actual values
    eff = _type_effectiveness(m["type"], enemy_types) if enemy_types else 1.0
    stab = 1.5 if (player_types and m["type"] in player_types) else 1.0
    if player_stats and enemy_stats:
        is_special = m["type"] in SPECIAL_TYPES
        offense = player_stats["spc"] if is_special else player_stats["atk"]
        # Gen 1: burn halves physical damage during the damage formula, not in
        # the shown stat — so we apply it manually here.
        if not is_special and player_status == "BRN":
            offense = offense // 2
        defense = enemy_stats["spc"] if is_special else enemy_stats["def"]
        if defense > 0:
            return base * eff * stab * offense / defense
    return base * eff * stab


# Absolute navigation — reaches target from ANY main-menu cursor position.
# Extra presses at boundaries are no-ops (cursor doesn't wrap in Gen 1).
_ABS_NAV_FIGHT = "U L"
_ABS_NAV_ITEM  = "D L"
_ABS_NAV_PKMN  = "U R"
_ABS_NAV_RUN   = "D R"

def _is_dex_owned(pyboy: "PyBoy", species_id: int) -> bool:
    """Return True if the species is already registered as caught in the Pokédex.

    wPokedexOwned (0xD2F7) is a 19-byte bitfield indexed by national dex number.
    Dex #N → byte offset (N-1)//8, bit (N-1)%8.
    """
    dex_num = INTERNAL_TO_DEX.get(species_id)
    if dex_num is None:
        return False
    idx = dex_num - 1  # 0-based
    byte_val = pyboy.memory[ADDR_POKEDEX_OWNED + idx // 8]
    return bool(byte_val & (1 << (idx % 8)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_stage(base: int, stage: int) -> int:
    """Apply a Gen 1 stat stage multiplier to a base in-battle stat."""
    return base * STAGE_MULTS[max(0, min(12, stage + 6))] // 100


def _estimate_damage(
    move: Dict[str, Any],
    attacker_level: int,
    attacker_stats: Dict[str, int],
    defender_stats: Dict[str, int],
    attacker_types: List[str],
    defender_types: List[str],
    attacker_status: str = "OK",
    stat_mods: Optional[Dict[str, Dict[str, int]]] = None,
) -> Tuple[int, int]:
    """Estimate min/max damage a move would deal using the Gen 1 formula.

    Returns (min_damage, max_damage).  Status moves return (0, 0).
    OHKO/fixed-damage moves (power <= 1) return a rough fixed estimate.
    """
    power = move.get("power", 0)
    if power == 0:
        return (0, 0)
    if power == 1:
        # Fixed/OHKO moves — rough estimates
        name = move.get("name", "")
        if "SEISMIC" in name or "NIGHT SHADE" in name:
            return (attacker_level, attacker_level)
        if "DRAGON RAGE" in name:
            return (40, 40)
        if "SONICBOOM" in name:
            return (20, 20)
        if "SUPER FANG" in name:
            # Halves current HP — we don't know enemy current HP here,
            # but it's safe for catching (never KOs).
            return (1, 1)
        if "PSYWAVE" in name:
            return (1, int(attacker_level * 1.5))
        # OHKO moves (Fissure, Guillotine, Horn Drill) — would KO
        return (9999, 9999)

    is_special = move["type"] in SPECIAL_TYPES
    pmods = (stat_mods or {}).get("player", {})
    if is_special:
        offense = _apply_stage(attacker_stats.get("spc", 50), pmods.get("spc", 0))
        defense = defender_stats.get("spc", 50)
    else:
        offense = _apply_stage(attacker_stats.get("atk", 50), pmods.get("atk", 0))
        defense = defender_stats.get("def", 50)
    if not is_special and attacker_status == "BRN":
        offense = offense // 2

    eff = _type_effectiveness(move["type"], defender_types) if defender_types else 1.0
    stab = 1.5 if move["type"] in attacker_types else 1.0
    if eff == 0.0:
        return (0, 0)

    # Gen 1 formula: ((2*Level/5+2) * Power * Atk/Def) / 50 + 2
    base_dmg = ((2 * attacker_level // 5 + 2) * power * offense // max(defense, 1)) // 50 + 2
    modified = base_dmg * eff * stab
    # Random roll: 217/255 to 255/255
    return (int(modified * 217 / 255), int(modified))


def _pick_catch_move(
    player: Dict[str, Any],
    enemy: Dict[str, Any],
    enemy_types: List[str],
    stat_mods: Optional[Dict[str, Dict[str, int]]] = None,
) -> Optional[Tuple[Dict[str, Any], str]]:
    """Pick the best move for catching: status move > gentle damage move.

    Returns (move_dict, strategy) where strategy is one of:
      "sleep"    — puts enemy to sleep (best catch bonus)
      "paralyze" — paralyzes enemy (good catch bonus, no wakeup risk)
      "weaken"   — weakest damage move that won't KO from current HP
      None       — no safe move available (everything would KO)

    Returns None if no suitable move exists.
    """
    enemy_hp = enemy.get("hp", 0)
    enemy_status = enemy.get("status", "OK")
    p_level = player.get("level", 50)
    ps = player.get("stats") or {}
    es = enemy.get("stats") or {}
    ptypes = player.get("types") or []
    pstatus = player.get("status", "OK")

    # Phase 1: If enemy has no status, prefer sleep > paralyze
    if enemy_status == "OK":
        # Look for sleep moves first (2x catch bonus)
        for m in player.get("moves", []):
            if m.get("id") in SLEEP_MOVE_IDS and m["pp"] > 0:
                # Check type immunity (e.g. Grass-type immune to powder moves
                # isn't a thing in Gen 1, but Ghost immune to Normal is)
                eff = _type_effectiveness(m["type"], enemy_types) if enemy_types else 1.0
                if eff > 0.0:
                    return (m, "sleep")
        # Then paralyze moves (1.5x catch bonus)
        for m in player.get("moves", []):
            if m.get("id") in PARALYZE_MOVE_IDS and m["pp"] > 0:
                eff = _type_effectiveness(m["type"], enemy_types) if enemy_types else 1.0
                if eff > 0.0:
                    return (m, "paralyze")

    # Phase 2: Pick the weakest damage move that won't KO
    damage_candidates = []
    for m in player.get("moves", []):
        if m["power"] > 0 and m["pp"] > 0:
            min_dmg, max_dmg = _estimate_damage(
                m, p_level, ps, es, ptypes, enemy_types, pstatus, stat_mods,
            )
            if max_dmg == 0:
                continue  # immune
            # Safe if max damage < enemy current HP (won't KO even on high roll)
            if max_dmg < enemy_hp:
                damage_candidates.append((m, max_dmg))

    if damage_candidates:
        # Pick the weakest safe move (lowest max damage)
        best = min(damage_candidates, key=lambda x: x[1])
        return (best[0], "weaken")

    # Phase 3: No safe damage move — everything might KO.
    # If enemy HP is low enough to attempt a catch, return None (caller throws ball).
    # Otherwise, caller should still try the gentlest option with a warning.
    return None


def _read_pokemon(
    pyboy: PyBoy,
    species_addr: int,
    hp_addr: int,
    status_addr: int,
    moves_addr: int,
    level_addr: int,
    max_hp_addr: int,
    pp_addr: int,
    atk_addr: int = 0,
    def_addr: int = 0,
    spd_addr: int = 0,
    spc_addr: int = 0,
    type1_addr: int = 0,
    type2_addr: int = 0,
) -> Optional[Dict[str, Any]]:
    """Read a battle Pokémon's data from RAM."""
    species_id = pyboy.memory[species_addr]
    if species_id == 0:
        return None  # slot empty or data not ready

    name = POKEMON_NAMES.get(species_id, f"???({species_id:#04x})")
    hp = read_word(pyboy, hp_addr)
    max_hp = read_word(pyboy, max_hp_addr)
    level = pyboy.memory[level_addr]
    status = decode_status(pyboy.memory[status_addr])

    # Stats (in-battle values, already modified by stat stages)
    stats = {}
    if atk_addr:
        stats = {
            "atk": read_word(pyboy, atk_addr),
            "def": read_word(pyboy, def_addr),
            "spd": read_word(pyboy, spd_addr),
            "spc": read_word(pyboy, spc_addr),
        }

    moves: List[Dict[str, Any]] = []
    hm_moves: List[str] = []
    for i in range(4):
        move_id = pyboy.memory[moves_addr + i]
        if move_id == 0:
            break
        pp = pyboy.memory[pp_addr + i]
        move_name, move_type, move_power, base_pp = MOVE_DATA.get(
            move_id, (f"Move#{move_id}", "???", 0, 0)
        )
        is_hm = move_id in HM_MOVE_IDS
        if is_hm:
            hm_moves.append(HM_MOVE_IDS[move_id])
        moves.append({
            "id": move_id,
            "name": move_name,
            "type": move_type,
            "power": move_power,
            "pp": pp,
            "base_pp": base_pp,
            "slot": i,
            "is_hm": is_hm,
        })

    # Types (decoded from RAM byte → name string)
    types: List[str] = []
    if type1_addr:
        t1 = TYPE_NAMES.get(pyboy.memory[type1_addr])
        if t1:
            types.append(t1)
        if type2_addr:
            t2 = TYPE_NAMES.get(pyboy.memory[type2_addr])
            if t2 and t2 != t1:  # Gen 1: single-type mons have same byte twice
                types.append(t2)

    return {
        "species_id": species_id,
        "name": name,
        "hp": hp,
        "max_hp": max_hp,
        "level": level,
        "status": status,
        "stats": stats,
        "moves": moves,
        "hm_moves": hm_moves,
        "types": types,
    }


# ---------------------------------------------------------------------------
# Menu detection
# ---------------------------------------------------------------------------

# Main battle menu items (2x2 grid, cursor 0-3)
_MAIN_MENU_ITEMS = ["FIGHT", "ITEM", "PKMN", "RUN"]


def _detect_battle_submenu(pyboy: PyBoy, player_hp: int = -1) -> str:
    """Detect which battle sub-menu is active from cursor position metadata.

    Returns "main", "fight", "faint", "pkmn", or "unknown".
    """
    top_y = pyboy.memory[ADDR_MENU_TOP_Y]
    top_x = pyboy.memory[ADDR_MENU_TOP_X]

    # If a text message is being printed (e.g. "No! There's no running!"),
    # wCurrentMenuItem holds a transient text-engine value, not the real battle
    # menu cursor. Detect via wStatusFlags5 bit 0 (TEXT_BOX_OPEN) and return
    # "unknown" so the agent knows to press A to advance the text.
    text_box_active = bool(pyboy.memory[ADDR_STATUS_FLAGS5] & 0x01)

    # Main battle menu (FIGHT/ITEM/PKMN/RUN): top item around Y=14, X=9
    if top_y >= 14 and top_x >= 8:
        if text_box_active:
            return "unknown"  # Battle message active — press A to advance
        return "main"
    # Move selection menu: top item around Y=12, X=4-5
    if 10 <= top_y <= 13 and top_x <= 6:
        if text_box_active:
            return "unknown"  # Fight submenu message (e.g. "No PP left!") — press A to advance
        return "fight"
    # Faint flow: player HP is 0 and we're in an unknown menu state
    # (YES/NO prompt or party select screen)
    if player_hp == 0:
        return "faint"
    # Party switch screen (opened via PKMN from main battle menu): top_y is
    # very small (~2) because the party list is drawn near the top of the
    # screen. This is distinct from text messages — text_box_active may be set
    # when "CHARMANDER is already out!" dismissal text is showing, but the
    # underlying state is still the party menu. In both cases B escapes back
    # to main; pressing A re-selects the already-active mon → infinite loop.
    if top_y <= 8 and top_x <= 3 and not text_box_active:
        return "pkmn"
    return "unknown"


def _fight_nav_presses(target: int, num_moves: int = 4, current: int = 0) -> str:
    """Navigate fight submenu reliably from known cursor position to target slot.

    Gen 1 fight menu is a vertical list that WRAPS (U at slot 0 → last slot).
    U×num_moves is a full cycle = no-op, so we use relative navigation:
      U×current  → slot 0  (works with wrapping: current steps up from current = 0)
      D×target   → target  (then go down to desired slot)
    """
    parts = ["U"] * current + ["D"] * target
    return " ".join(parts) if parts else ""


def _count_alive_party(pyboy: PyBoy, party_count: int) -> int:
    """Count party members with HP > 0 from the party data RAM block.

    Note: the active battler's HP in the party block may not be synced to 0
    immediately on faint — callers should account for this (alive_count may be
    inflated by 1 while the active mon is in the process of fainting).
    """
    alive = 0
    for i in range(min(party_count, 6)):
        base = ADDR_PARTY_BASE + i * PARTY_MON_SIZE
        hp = (pyboy.memory[base + _PARTY_HP_OFFSET] << 8) | pyboy.memory[base + _PARTY_HP_OFFSET + 1]
        if hp > 0:
            alive += 1
    return alive


def _read_party_levels(pyboy: PyBoy, party_count: int) -> List[int]:
    """Read levels of all alive party members for leveling decisions."""
    levels = []
    for i in range(min(party_count, 6)):
        base = ADDR_PARTY_BASE + i * PARTY_MON_SIZE
        hp = (pyboy.memory[base + _PARTY_HP_OFFSET] << 8) | pyboy.memory[base + _PARTY_HP_OFFSET + 1]
        if hp > 0:
            level = pyboy.memory[base + _PARTY_LEVEL_OFFSET]
            levels.append(level)
    return levels


def _count_pokeballs(pyboy: PyBoy) -> int:
    """Count total Poke Balls in bag (all ball types)."""
    count = pyboy.memory[ADDR_NUM_BAG_ITEMS]
    if count == 0 or count > 20:
        return 0
    total = 0
    for i in range(count):
        addr = ADDR_BAG_ITEMS + (i * 2)
        item_id = pyboy.memory[addr]
        if item_id == 0xFF:
            break
        if item_id in _BATTLE_BALL_IDS:
            total += pyboy.memory[addr + 1]
    return total


# Ball quality ranking: higher = better catch rate modifier.
_BALL_RANK = {0x01: 4, 0x02: 3, 0x03: 2, 0x04: 1}  # Master, Ultra, Great, Poke
_BALL_NAMES = {0x01: "MASTER BALL", 0x02: "ULTRA BALL", 0x03: "GREAT BALL", 0x04: "POKE BALL"}


def _find_best_ball(pyboy: PyBoy) -> Optional[Tuple[str, int]]:
    """Find the best Poke Ball in the bag, preferring Ultra > Great > Poke.

    Master Ball is excluded from auto-use (too precious for non-legendaries).
    Returns (ball_name, bag_slot_1indexed) or None if no balls found.
    """
    count = pyboy.memory[ADDR_NUM_BAG_ITEMS]
    if count == 0 or count > 20:
        return None
    best: Optional[Tuple[int, str, int]] = None  # (rank, name, slot)
    for i in range(count):
        addr = ADDR_BAG_ITEMS + (i * 2)
        item_id = pyboy.memory[addr]
        if item_id == 0xFF:
            break
        qty = pyboy.memory[addr + 1]
        if item_id in _BATTLE_BALL_IDS and qty > 0 and item_id != 0x01:  # skip Master Ball
            rank = _BALL_RANK[item_id]
            if best is None or rank > best[0]:
                best = (rank, _BALL_NAMES[item_id], i + 1)
    return (best[1], best[2]) if best else None


def _read_battle_items(pyboy: PyBoy) -> Dict[str, Any]:
    """Scan the bag for HP healing and status cure items.

    Returns:
        {
          "best_hp_item": (name, heals, bag_slot) | None,
          "status_cures": {status_key: (name, bag_slot)},  # status_key: "PSN","BRN",etc.
        }
    Bag slots are 1-indexed (slot 1 = top of bag).
    """
    count = pyboy.memory[ADDR_NUM_BAG_ITEMS]
    best_hp: Optional[Tuple[str, int, int]] = None   # (name, heal, slot)
    status_cures: Dict[str, Tuple[str, int]] = {}

    if count == 0 or count > 20:
        return {"best_hp_item": None, "status_cures": {}}

    for i in range(count):
        addr = ADDR_BAG_ITEMS + (i * 2)
        item_id = pyboy.memory[addr]
        if item_id == 0xFF:
            break
        qty = pyboy.memory[addr + 1]
        if qty == 0:
            continue
        slot = i + 1  # 1-indexed

        # HP healing
        if item_id in HP_ITEMS:
            name, heals = HP_ITEMS[item_id]
            if best_hp is None or heals > best_hp[1]:
                best_hp = (name, heals, slot)

        # Status cures
        if item_id in STATUS_CURE_ITEMS:
            name, cures_set = STATUS_CURE_ITEMS[item_id]
            for status_key in cures_set:
                # Prefer the item that cures more (Full Heal > specific cure)
                if status_key not in status_cures:
                    status_cures[status_key] = (name, slot)

    return {"best_hp_item": best_hp, "status_cures": status_cures, "item_count": count}


def _read_safari_state(pyboy: PyBoy) -> Dict[str, int]:
    """Read Safari Zone encounter state from RAM.

    Returns dict with balls, steps, bait_count, rock_count.
    """
    steps_hi = pyboy.memory[_ADDR_SAFARI_STEPS]
    steps_lo = pyboy.memory[_ADDR_SAFARI_STEPS + 1]
    return {
        "balls":      pyboy.memory[_ADDR_NUM_SAFARI_BALLS],
        "steps":      (steps_hi << 8) | steps_lo,
        "bait_count": pyboy.memory[_ADDR_SAFARI_BAIT],
        "rock_count": pyboy.memory[_ADDR_SAFARI_ROCK],
    }


def _read_stat_modifiers(pyboy: PyBoy) -> Dict[str, Dict[str, int]]:
    """Read in-battle stat stage modifiers for player and enemy.

    Returns stages as integers (-6 to +6), where 0 = neutral.
    Raw RAM values are 0-12 (neutral=7); we subtract 7 before returning.
    """
    def s(addr: int) -> int:
        return pyboy.memory[addr] - 7
    return {
        "player": {
            "atk": s(_ADDR_PLAYER_ATK_MOD),
            "def": s(_ADDR_PLAYER_DEF_MOD),
            "spd": s(_ADDR_PLAYER_SPD_MOD),
            "spc": s(_ADDR_PLAYER_SPC_MOD),
            "acc": s(_ADDR_PLAYER_ACC_MOD),
            "eva": s(_ADDR_PLAYER_EVA_MOD),
        },
        "enemy": {
            "atk": s(_ADDR_ENEMY_ATK_MOD),
            "def": s(_ADDR_ENEMY_DEF_MOD),
            "spd": s(_ADDR_ENEMY_SPD_MOD),
            "spc": s(_ADDR_ENEMY_SPC_MOD),
            "acc": s(_ADDR_ENEMY_ACC_MOD),
            "eva": s(_ADDR_ENEMY_EVA_MOD),
        },
    }


# ---------------------------------------------------------------------------
# Battle tip
# ---------------------------------------------------------------------------

def _throw_ball_compound(battle_items: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """Build compound input to throw the best available Poke Ball.

    Returns (ball_name, compound_input_string).
    Uses _item_use_compound with the best ball's bag slot if known,
    otherwise falls back to selecting the first bag item.
    """
    items = battle_items or {}
    best_ball = items.get("best_ball")  # (name, slot) or None
    n_items = items.get("item_count", 20)
    if best_ball:
        bname, bslot = best_ball
        return (bname, _item_use_compound(bslot, n_items))
    # Fallback: open bag, select first item (hope it's a ball)
    return ("POKE BALL", f"B {_ABS_NAV_ITEM} A W A")


def _item_use_compound(bag_slot: int, total_items: int = 20) -> str:
    """Compound input to open the bag and navigate to a specific slot.

    Sequence: B (→main) D L (→ITEM) A (open bag) W (wait) + U×total_items (reset to slot 1)
    + D×(slot-1) (navigate to target) + A (select) A (use on active mon).

    Gen 1 bag cursor persists between opens (wBagSavedMenuItem).  Resetting with
    U×total_items is safe because U at slot 1 is a no-op.
    """
    reset = " ".join(["U"] * max(1, total_items))
    nav = " ".join(["D"] * (bag_slot - 1)) if bag_slot > 1 else ""
    inner = f"{reset} {nav}".strip()
    return f"B {_ABS_NAV_ITEM} A W {inner} A A"


def _generate_battle_tip(
    player: Dict[str, Any],
    enemy: Dict[str, Any],
    menu_type: str,
    cursor: int,
    battle_type: int = 0,
    pokeball_count: int = 0,
    party_count: int = 6,
    alive_count: int = 0,
    enemy_types: Optional[List[str]] = None,
    fight_cursor: int = 0,
    enemy_owned: bool = False,
    battle_items: Optional[Dict[str, Any]] = None,
    stat_mods: Optional[Dict[str, Dict[str, int]]] = None,
    min_party_level: Optional[int] = None,
) -> Optional[str]:
    """Generate a short tactical recommendation.

    Key design: when on the main menu, produce a COMPOUND input string
    that selects FIGHT and the best move in ONE send_inputs call so the
    agent doesn't waste a full turn navigating each sub-menu separately.
    """
    # Enemy already fainted — press A to clear EXP/level-up/move-learned text
    if enemy["hp"] == 0:
        return "Enemy fainted! Press A a few times to advance EXP/level-up text."

    # Player fainted — give specific YES/NO and party-select guidance
    if player["hp"] == 0:
        if battle_type == 1:  # wild battle — can run via NO
            return (
                "FAINT FLOW: 'Use next POKEMON?' prompt. "
                "To send next mon: A, then D/U to pick one with HP > 0, then A. "
                "To flee: D A (D=move to NO, A=confirm). "
                "DO NOT press A first — it selects YES immediately!"
            )
        else:  # trainer battle — must continue
            return (
                "FAINT FLOW: 'Use next POKEMON?' prompt. Trainer battle — must send out another. "
                "Send: A, then D/U to find a Pokémon with HP > 0, then A to select it. "
                "Skip fainted mons — selecting one shows 'There's no will to fight!'."
            )

    # Party switch screen — agent can intentionally switch, or press B to cancel.
    # Warn about the already-active-mon loop so they navigate off that slot first.
    if menu_type == "pkmn":
        return (
            f"PKMN SWITCH SCREEN (cursor on slot {cursor+1}). "
            "D/U to navigate party, A to switch in that Pokémon, B to cancel. "
            "WARNING: pressing A on the already-active Pokémon shows 'already out!' "
            "and loops back here — navigate to a different slot first."
        )

    # Unknown battle state (text message over an unrecognised menu).
    # B is the safe escape when stuck; A may confirm an unintended selection.
    if menu_type == "unknown" and player["hp"] > 0 and enemy["hp"] > 0:
        # Critical HP wild battle — try running through the overlay immediately.
        if battle_type == 1 and player["max_hp"] > 0:
            hp_pct = player["hp"] * 100 // player["max_hp"]
            if hp_pct <= 20:
                return (
                    f"HP critical ({hp_pct}%) + unknown state — RUN immediately! "
                    f"Use the run_from_battle tool (handles menu state automatically)."
                )
        return (
            "Unknown battle state — likely a submenu or text overlay. "
            "Press A to advance text, or B to return to main battle menu if stuck. Send: B"
        )

    # --- All TIPs below use B prefix for robustness ---
    # B is a no-op on the main battle menu but returns to main from the
    # fight submenu.  This sidesteps the unreliable main-vs-fight detection
    # (wTopMenuItemY/X are stale RAM after submenu transitions).

    pstatus = player.get("status", "OK")

    items = battle_items or {}
    status_cures = items.get("status_cures", {})
    best_hp_item = items.get("best_hp_item")  # (name, heals, bag_slot) or None
    n_items = items.get("item_count", 20)      # total bag items — used to reset cursor to slot 1

    # Sleep/Freeze: player cannot act — suggest cure item if available, else press A.
    if pstatus.startswith("SLP"):
        turns_left = pstatus[4:-1] if len(pstatus) > 3 else "?"
        cure = status_cures.get("SLP")
        if cure and menu_type in ("main", "fight"):
            cname, cslot = cure
            return (f"YOU ARE ASLEEP ({turns_left} turns left)! Use {cname} (bag slot {cslot}) to wake up now "
                    f"— send: {_item_use_compound(cslot, n_items)}")
        return f"YOU ARE ASLEEP ({turns_left} turns left) — can't use moves! Press A to advance the turn. Send: A"
    if pstatus == "FRZ":
        cure = status_cures.get("FRZ")
        if cure and menu_type in ("main", "fight"):
            cname, cslot = cure
            return (f"YOU ARE FROZEN! Use {cname} (bag slot {cslot}) to thaw immediately "
                    f"— send: {_item_use_compound(cslot, n_items)}")
        return "YOU ARE FROZEN — can't move until thawed (random each turn)! Press A to advance. Send: A"

    # For other serious statuses in trainer battles, suggest curing with an item
    # (BRN halves physical damage; PAR causes 25% paralysis; PSN adds HP pressure)
    if menu_type in ("main", "fight") and pstatus in ("BRN", "PAR", "PSN"):
        cure = status_cures.get(pstatus)
        if cure and battle_type != 1:  # trainer battle — conserving HP matters more
            cname, cslot = cure
            status_desc = {"BRN": "BURNED (physical moves halved!)", "PAR": "PARALYZED (25% skip chance!)", "PSN": "POISONED (chip damage each turn)"}[pstatus]
            return (f"You are {status_desc} Use {cname} (bag slot {cslot}) to cure it "
                    f"— send: {_item_use_compound(cslot, n_items)}")

    # Low HP in trainer battle — suggest healing item if available
    if (menu_type in ("main", "fight") and battle_type != 1
            and best_hp_item and player["max_hp"] > 0):
        hp_pct = player["hp"] * 100 // player["max_hp"]
        if hp_pct <= 40:
            hname, heals, hslot = best_hp_item
            heals_str = "full HP" if heals >= 9999 else f"+{heals} HP"
            return (f"HP LOW ({hp_pct}%, trainer battle) — use {hname} ({heals_str}, bag slot {hslot}) "
                    f"— send: {_item_use_compound(hslot, n_items)}")

    # Catch logic: wild battle + have balls.
    # Rare Pokemon get aggressive catch strategy (status → weaken → throw).
    # Common Pokemon use the original conservative thresholds.
    if battle_type == 1 and pokeball_count > 0 and enemy["max_hp"] > 0:
        enemy_hp_pct = 100 * enemy["hp"] // enemy["max_hp"]
        enemy_status = enemy.get("status", "OK")
        enemy_asleep = enemy_status.startswith("SLP") or enemy_status == "FRZ"
        is_rare = enemy["name"] in RARE_POKEMON

        # --- Rare Pokemon: multi-phase catch strategy ---
        if is_rare and not enemy_owned and menu_type in ("main", "fight"):
            catch_move = _pick_catch_move(
                player, enemy, enemy_types or [],
                stat_mods=stat_mods,
            )
            num_moves = len(player.get("moves", [])) or 4

            # Phase A: Enemy at full/high HP with no status → inflict status first
            if catch_move and catch_move[1] in ("sleep", "paralyze"):
                m, strategy = catch_move
                nav = _fight_nav_presses(m["slot"], num_moves, current=fight_cursor)
                compound = f"B {_ABS_NAV_FIGHT} A W32 {nav} A"
                label = "Put it to SLEEP" if strategy == "sleep" else "PARALYZE it"
                return (f"RARE: {enemy['name']}! {label} first for catch bonus! "
                        f"Use {m['name']} — send: {compound}")

            # Phase B: Enemy has status or no status moves available.
            # If HP is high, weaken with gentlest move.
            if enemy_hp_pct > 30 and catch_move and catch_move[1] == "weaken":
                m, _ = catch_move
                nav = _fight_nav_presses(m["slot"], num_moves, current=fight_cursor)
                compound = f"B {_ABS_NAV_FIGHT} A W32 {nav} A"
                status_tag = f" [{enemy_status}]" if enemy_status != "OK" else ""
                return (f"RARE: {enemy['name']}! Weaken gently{status_tag} — "
                        f"use {m['name']} ({m['power']}pwr, won't KO) "
                        f"— send: {compound}")

            # Phase C: No safe weakening move — all attacks would KO.
            # If HP is still high, suggest switching to a weaker party member.
            if catch_move is None and enemy_hp_pct > 30 and alive_count > 1:
                return (f"RARE: {enemy['name']}! Your moves are too strong — "
                        f"SWITCH to a weaker Pokémon to weaken it safely! "
                        f"Send: B {_ABS_NAV_PKMN} A W, then D/U to pick a lower-level mon, then A.")

            # Phase D: HP ≤30% or no safe move and can't switch → throw ball!
            status_tag = ""
            if enemy_asleep:
                status_tag = " SLP/FRZ bonus!"
            elif enemy_status in ("PAR", "BRN", "PSN"):
                status_tag = f" {enemy_status} bonus!"
            bname, bcmd = _throw_ball_compound(battle_items)
            return (f"RARE: Catch {enemy['name']} NOW! "
                    f"(HP {enemy_hp_pct}%{status_tag}, {pokeball_count} {bname}s) "
                    f"— send: {bcmd}")

        # --- Standard catch logic (non-rare or already owned) ---
        should_catch = False
        reason = ""
        if enemy_asleep and not enemy_owned:
            should_catch = True
            reason = "SLP/FRZ = max catch rate bonus!"
        elif party_count < 6 and enemy_hp_pct <= 40 and not enemy_owned:
            should_catch = True
            reason = f"party {party_count}/6, new dex entry"
        elif enemy_hp_pct <= 20 and not enemy_owned:
            should_catch = True
            reason = "HP very low, new dex entry"

        if should_catch and menu_type in ("main", "fight"):
            bname, bcmd = _throw_ball_compound(battle_items)
            return (f"Catch {enemy['name']}! ({reason}, {pokeball_count} {bname}s) "
                    f"— send: {bcmd}")

    # Wild battle + critically low HP → running is safer than fighting.
    # Gen 1 run can fail — send the sequence twice so a single failure doesn't
    # cost an extra turn (extra inputs are no-ops after the battle ends).
    if battle_type == 1 and player["hp"] > 0 and player["max_hp"] > 0:
        hp_pct = player["hp"] * 100 // player["max_hp"]
        if hp_pct <= 20 and menu_type in ("main", "fight"):
            return (f"HP critical ({hp_pct}%) — RUN from this wild battle! "
                    f"Use the run_from_battle tool (handles menu navigation and retries automatically).")

    # Find the strongest usable damage move, weighted by Gen 1 damage mechanics
    etypes = enemy_types or []
    ps = player.get("stats") or {}
    es = enemy.get("stats") or {}
    ptypes = player.get("types") or []
    # pstatus already defined above
    _ep = lambda pair: _effective_power(pair, etypes, ps or None, es or None, ptypes, pstatus)

    damage_moves = [
        (m, m["slot"]) for m in player["moves"]
        if m["power"] > 1 and m["pp"] > 0  # >1 excludes OHKO/fixed-dmg quirks
    ]
    if not damage_moves:
        # Fall back to any move with power (including OHKO/fixed)
        damage_moves = [
            (m, m["slot"]) for m in player["moves"]
            if m["power"] > 0 and m["pp"] > 0
        ]

    # Filter out moves that do 0 effective damage (type immunity, e.g. Electric
    # vs Ground) so the TIP recommends RUN/switch instead of a wasted attack.
    if damage_moves:
        damage_moves = [(m, s) for m, s in damage_moves if _ep((m, s)) > 0.0]

    if menu_type in ("main", "fight") and damage_moves:
        best_move, best_slot = max(damage_moves, key=_ep)
        # U×num_moves resets to slot 0 (safe no-op at top), then D×best_slot
        # navigates to the target.  Ignores wPlayerMoveListIndex — the fight
        # submenu retains its previous cursor position on re-entry.
        num_moves = len(player.get("moves", [])) or 4
        nav = _fight_nav_presses(best_slot, num_moves, current=fight_cursor)
        compound = f"B {_ABS_NAV_FIGHT} A W32 {nav} A"
        eff = _type_effectiveness(best_move["type"], etypes) if etypes else 1.0
        eff_tag = f", {eff:g}x vs {'/'.join(etypes)}" if eff != 1.0 and etypes else ""
        is_special = best_move["type"] in SPECIAL_TYPES
        cat = "Special" if is_special else "Physical"
        stab_tag = " STAB" if (ptypes and best_move["type"] in ptypes) else ""
        burn_tag = " [BRN→physical halved!]" if (pstatus == "BRN" and not is_special) else ""

        # Speed tier: who attacks first this turn?
        # Apply stat stage modifiers first, then PAR halving (Gen 1 order).
        spd_note = ""
        if ps and es:
            pmods = (stat_mods or {}).get("player", {})
            emods = (stat_mods or {}).get("enemy", {})
            p_spd = _apply_stage(ps["spd"], pmods.get("spd", 0))
            e_spd = _apply_stage(es["spd"], emods.get("spd", 0))
            if pstatus == "PAR":
                p_spd //= 4
            if enemy.get("status", "OK") == "PAR":
                e_spd //= 4
            if p_spd > e_spd:
                spd_note = " [YOU go first]"
            elif e_spd > p_spd:
                spd_note = " [ENEMY goes first]"
            else:
                spd_note = " [speed tie→random]"

        # Enemy status advantage notes
        estatus = enemy.get("status", "OK")
        estatus_note = ""
        if estatus.startswith("SLP"):
            estatus_note = " [enemy asleep — great time to catch!]"
        elif estatus == "FRZ":
            estatus_note = " [enemy frozen — free hits!]"
        elif estatus == "PAR":
            estatus_note = " [enemy PAR — 25% skip chance]"
        elif estatus == "PSN":
            estatus_note = " [enemy PSN — taking chip damage each turn]"
        elif estatus == "BRN":
            estatus_note = " [enemy BRN — physical moves halved + chip damage]"

        # Annotate with TRAIN tag when party has underleveled members
        train_tag = ""
        if (battle_type == 1 and min_party_level is not None
                and min_party_level < enemy.get("level", 99)):
            train_tag = f"TRAIN: Team needs XP (min Lv{min_party_level} vs enemy Lv{enemy['level']}) — FIGHT! "

        return (f"{train_tag}Use {best_move['name']} ({best_move['power']}pwr, {cat}{stab_tag}{eff_tag}{burn_tag})"
                f"{spd_note}{estatus_note} — send: {compound}")

    if menu_type in ("main", "fight") and not damage_moves:
        if battle_type == 1:  # wild — RUN is an option
            return "No usable damage moves — RUN from this wild battle! Use the run_from_battle tool."
        # Trainer battle: cannot RUN. Check if switching to a mon with damage moves is viable.
        can_switch = alive_count > 1
        if can_switch:
            return (f"Trainer battle — no damage moves on this mon. "
                    f"Switch to one with damage moves! Send: B {_ABS_NAV_PKMN} A W, "
                    f"then D/U to pick a mon with HP > 0, then A.")
        # Unwinnable: only status moves, no switchable mons. Use first move to advance.
        first_move = player["moves"][0]["name"] if player["moves"] else "STRUGGLE"
        num_moves = len(player.get("moves", [])) or 4
        nav = _fight_nav_presses(0, num_moves, current=fight_cursor)
        compound = f"B {_ABS_NAV_FIGHT} A W32 {nav} A"
        return (f"Unwinnable: only {first_move} (status). Use it to let the battle end "
                f"→ blackout → free heal at Pokémon Center. Send: {compound}")

    # Low HP
    if player["hp"] > 0 and player["hp"] <= player["max_hp"] // 4:
        return "HP critical! Consider healing or switching."

    return None


def _generate_safari_tip(
    enemy: Dict[str, Any],
    menu_type: str,
    safari_state: Dict[str, int],
) -> Optional[str]:
    """Generate a tactical tip for Safari Zone battles.

    Safari menu cursor layout (same 2×2 grid as normal battle menu):
      BALL(0)=U L A  |  BAIT(2)=U R A
      THROW ROCK(1)=D L A  |  RUN(3)=D R A

    Safari mechanics:
      THROW ROCK: raises catch rate, but also raises the wild Pokémon's flee chance.
      BAIT:       lowers flee chance, but also lowers the catch rate.
      BALL:       attempt capture using current modified catch rate.

    Args:
        enemy:        Enemy Pokémon data dict (name, level, status, etc.)
        menu_type:    "main", "unknown", etc. (from _detect_battle_submenu)
        safari_state: Dict from _read_safari_state (balls, steps, bait_count, rock_count).

    Returns:
        Tip string, or None if no actionable advice.
    """
    balls      = safari_state["balls"]
    steps      = safari_state["steps"]
    bait_count = safari_state["bait_count"]
    rock_count = safari_state["rock_count"]

    if menu_type not in ("main", "unknown"):
        return None

    if balls == 0:
        return "No Safari Balls left — must RUN! Send: D R A"

    if steps <= 10:
        return f"Only {steps} steps left before ejected — throw BALL now! Send: U L A"

    # TODO: implement Safari catch strategy (5-10 lines)
    # Decide the best action given rock_count, bait_count, and balls remaining.
    # Trade-offs to consider:
    #   - rock_count == 0: catch rate is base; throwing rock improves it but adds flee risk
    #   - rock_count >= 1: catch rate is already boosted; may be good time to throw BALL
    #   - bait_count > 0 and rock_count == 0: catch rate is reduced; may want rock to recover
    #   - With few balls (e.g. <= 5), prioritise the throw-immediately approach
    #   - enemy already in dex (enemy_owned not available here, consider passing it if needed)
    # Replace the fallback line below with your strategy.

    return (
        f"Safari: {balls} balls, {steps} steps. "
        f"THROW ROCK (raises catch, raises flee): D L A | "
        f"BAIT (lowers flee, lowers catch): U R A | "
        f"BALL (catch attempt): U L A"
    )


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

def _format_battle_text(
    battle_type: int,
    player: Dict[str, Any],
    enemy: Dict[str, Any],
    menu_type: str,
    cursor: int,
    pokeball_count: int = 0,
    party_count: int = 6,
    alive_count: int = 0,
    fight_cursor: int = 0,
    enemy_owned: bool = False,
    battle_items: Optional[Dict[str, Any]] = None,
    safari_state: Optional[Dict[str, int]] = None,
    stat_mods: Optional[Dict[str, Dict[str, int]]] = None,
    turn_count: int = 0,
    whose_turn: int = 0,
    player_move_chosen: Optional[Dict[str, Any]] = None,
    enemy_move_chosen: Optional[Dict[str, Any]] = None,
    min_party_level: Optional[int] = None,
) -> str:
    """Assemble the battle context text block."""
    if safari_state:
        kind = "Safari Zone"
        wild_prefix = "wild "
    else:
        kind = "Wild" if battle_type == 1 else "Trainer"
        wild_prefix = "wild " if battle_type == 1 else ""

    turn_str = f" | Turn {turn_count}" if turn_count > 0 else ""
    whose_str = " | Opponent acting" if whose_turn == 1 else ""
    lines = [f"=== BATTLE CONTEXT{turn_str}{whose_str} ==="]
    lines.append(
        f"{kind} battle — {player['name']} Lv{player['level']} "
        f"vs {wild_prefix}{enemy['name']} Lv{enemy['level']}"
    )

    if safari_state:
        # Safari Zone: show resource state and menu navigation instead of moves
        balls      = safari_state["balls"]
        steps      = safari_state["steps"]
        bait_count = safari_state["bait_count"]
        rock_count = safari_state["rock_count"]
        lines.append(f"  Safari Balls: {balls} | Steps left: {steps}")
        modifiers = []
        if bait_count > 0:
            modifiers.append(f"Bait thrown: {bait_count}x (flee chance ↓, catch rate ↓)")
        if rock_count > 0:
            modifiers.append(f"Rock thrown: {rock_count}x (flee chance ↑, catch rate ↑)")
        if modifiers:
            lines.append("  " + " | ".join(modifiers))
        # Safari menu cursor (same 2×2 layout: BALL=0, THROW ROCK=1, BAIT=2, RUN=3)
        _safari_items = {0: "BALL", 1: "THROW ROCK", 2: "BAIT", 3: "RUN"}
        cur_name = _safari_items.get(cursor, f"#{cursor}")
        lines.append(
            f"  → Safari menu: cursor on {cur_name} | "
            f"BALL: U L A | THROW ROCK: D L A | BAIT: U R A | RUN: D R A"
        )
    else:
        # Player Pokémon
        status_str = f" {player['status']}" if player["status"] != "OK" else ""
        player_type_str = f" [{'/'.join(player.get('types', []))}]" if player.get("types") else ""
        lines.append(
            f"YOUR: {player['name']} Lv{player['level']}{player_type_str} "
            f"HP:{player['hp']}/{player['max_hp']}{status_str}"
        )

        # Stats (show stage modifier in parens when non-neutral)
        ps = player.get("stats", {})
        if ps:
            pmods = (stat_mods or {}).get("player", {})
            def _fmt(key: str, val: int) -> str:
                st = pmods.get(key, 0)
                return f"{key.capitalize()}:{val}" + (f"({'+' if st > 0 else ''}{st})" if st else "")
            lines.append(f"  Stats: {_fmt('atk', ps['atk'])} {_fmt('def', ps['def'])} "
                         f"{_fmt('spd', ps['spd'])} {_fmt('spc', ps['spc'])}")
            p_acc = pmods.get("acc", 0)
            p_eva = pmods.get("eva", 0)
            if p_acc or p_eva:
                lines.append(f"  Accuracy stage: {'+' if p_acc > 0 else ''}{p_acc} | "
                             f"Evasion stage: {'+' if p_eva > 0 else ''}{p_eva}")

        # Moves (mark HMs)
        move_parts = []
        for m in player["moves"]:
            pwr = f"{m['power']}pwr" if m["power"] > 0 else "status"
            hm_tag = " [HM]" if m.get("is_hm") else ""
            move_parts.append(f"{m['name']} ({m['type']},{pwr},{m['pp']}/{m['base_pp']}pp){hm_tag}")
        lines.append(f"  Moves: {' | '.join(move_parts)}")

        if turn_count > 0 and (player_move_chosen or enemy_move_chosen):
            last_parts = []
            if player_move_chosen:
                last_parts.append(f"You used {player_move_chosen['name']}")
            if enemy_move_chosen:
                last_parts.append(f"Enemy used {enemy_move_chosen['name']}")
            lines.append(f"  Last turn: {' | '.join(last_parts)}")

        # HM summary
        if player.get("hm_moves"):
            lines.append(f"  HMs: {', '.join(player['hm_moves'])}")

        # Cursor
        if menu_type == "main":
            item_name = _MAIN_MENU_ITEMS[cursor] if cursor < 4 else f"#{cursor}"
            # Show absolute nav paths that work from ANY cursor position; always
            # prefix with B which clears battle-start text overlays (and is a
            # no-op on the main menu itself).  Cursor-relative paths are omitted
            # deliberately — they mislead the model into skipping the B prefix.
            lines.append(
                f"  → Main menu: cursor on {item_name} | "
                f"Absolute nav (always prefix B to clear text overlays): "
                f"FIGHT:B {_ABS_NAV_FIGHT} | ITEM:B {_ABS_NAV_ITEM} | "
                f"PKMN:B {_ABS_NAV_PKMN} | RUN:B {_ABS_NAV_RUN}"
            )
        elif menu_type == "fight":
            if cursor < len(player["moves"]):
                lines.append(f"  → Fight menu: cursor on slot {cursor+1} ({player['moves'][cursor]['name']})")
            else:
                lines.append(f"  → Fight menu: cursor at slot {cursor+1}")
        elif menu_type == "faint":
            lines.append(f"  → FAINT FLOW — cursor on party slot {cursor+1} (0-indexed {cursor}). 'Use next POKEMON?' or party select. DO NOT mash A blindly!")
        elif menu_type == "pkmn":
            lines.append(f"  → PKMN SWITCH SCREEN — party slot cursor={cursor+1}. D/U to navigate, A to switch, B to cancel")
        else:
            lines.append(f"  → In submenu/text (not main battle menu) — press A to advance text, B to escape if stuck")

    # VS separator
    lines.append("──────────── VS ────────────")

    # Enemy Pokémon
    enemy_status = f" {enemy['status']}" if enemy["status"] != "OK" else ""
    enemy_type_str = f" [{'/'.join(enemy.get('types', []))}]" if enemy.get("types") else ""
    lines.append(
        f"ENEMY: {enemy['name']} Lv{enemy['level']}{enemy_type_str} "
        f"HP:{enemy['hp']}/{enemy['max_hp']}{enemy_status}"
    )
    es = enemy.get("stats", {})
    if es:
        emods = (stat_mods or {}).get("enemy", {})
        def _efmt(key: str, val: int) -> str:
            st = emods.get(key, 0)
            return f"{key.capitalize()}:{val}" + (f"({'+' if st > 0 else ''}{st})" if st else "")
        lines.append(f"  Stats: {_efmt('atk', es['atk'])} {_efmt('def', es['def'])} "
                     f"{_efmt('spd', es['spd'])} {_efmt('spc', es['spc'])}")
        e_acc = emods.get("acc", 0)
        e_eva = emods.get("eva", 0)
        if e_acc or e_eva:
            lines.append(f"  Accuracy stage: {'+' if e_acc > 0 else ''}{e_acc} | "
                         f"Evasion stage: {'+' if e_eva > 0 else ''}{e_eva}")
    if not safari_state:
        # Enemy moves are only shown in normal battles (Safari uses different mechanics)
        enemy_move_parts = []
        for m in enemy.get("moves", []):
            pwr = f"{m['power']}pwr" if m["power"] > 0 else "status"
            enemy_move_parts.append(f"{m['name']} ({m['type']},{pwr},{m['pp']}/{m['base_pp']}pp)")
        if enemy_move_parts:
            lines.append(f"  Moves: {' | '.join(enemy_move_parts)}")

    # Tip
    if safari_state:
        tip = _generate_safari_tip(enemy, menu_type, safari_state)
    else:
        tip = _generate_battle_tip(player, enemy, menu_type, cursor,
                                    battle_type=battle_type,
                                    pokeball_count=pokeball_count,
                                    party_count=party_count,
                                    alive_count=alive_count,
                                    enemy_types=enemy.get("types"),
                                    fight_cursor=fight_cursor,
                                    enemy_owned=enemy_owned,
                                    battle_items=battle_items,
                                    stat_mods=stat_mods,
                                    min_party_level=min_party_level)
    if tip:
        lines.append(f"TIP: {tip}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_battle_context(pyboy: PyBoy, just_entered_battle: bool = False) -> Optional[Dict[str, Any]]:
    """Extract battle context from RAM.

    Must be called on the main thread (PyBoy access is not thread-safe).

    Args:
        just_entered_battle: True only on the very first turn of a new battle.
            wCurrentMenuItem holds a stale overworld value at battle start, so
            we clamp the main-menu cursor to 0 (FIGHT) in that case only.
            Once inside an ongoing battle the cursor persists correctly in RAM.

    Returns dict with "text" key (formatted string) and structured data,
    or None if not in battle or data isn't ready.
    """
    try:
        battle_type = pyboy.memory[ADDR_IS_IN_BATTLE]
        if battle_type == 0:
            return None

        is_safari = pyboy.memory[ADDR_CUR_MAP] in _SAFARI_ZONE_MAPS
        safari_state = _read_safari_state(pyboy) if is_safari else None

        player = _read_pokemon(
            pyboy,
            _ADDR_PLAYER_SPECIES, _ADDR_PLAYER_HP, _ADDR_PLAYER_STATUS,
            _ADDR_PLAYER_MOVES, _ADDR_PLAYER_LEVEL, _ADDR_PLAYER_MAX_HP,
            _ADDR_PLAYER_PP,
            _ADDR_PLAYER_ATK, _ADDR_PLAYER_DEF, _ADDR_PLAYER_SPD, _ADDR_PLAYER_SPC,
            _ADDR_PLAYER_TYPE1, _ADDR_PLAYER_TYPE2,
        )
        enemy = _read_pokemon(
            pyboy,
            _ADDR_ENEMY_SPECIES, _ADDR_ENEMY_HP, _ADDR_ENEMY_STATUS,
            _ADDR_ENEMY_MOVES, _ADDR_ENEMY_LEVEL, _ADDR_ENEMY_MAX_HP,
            _ADDR_ENEMY_PP,
            _ADDR_ENEMY_ATK, _ADDR_ENEMY_DEF, _ADDR_ENEMY_SPD, _ADDR_ENEMY_SPC,
            _ADDR_ENEMY_TYPE1, _ADDR_ENEMY_TYPE2,
        )

        if not player or not enemy:
            logger.debug("Battle context: Pokémon data not ready")
            return None

        cursor = pyboy.memory[ADDR_MENU_ITEM]
        menu_type = _detect_battle_submenu(pyboy, player_hp=player["hp"])
        # wCurrentMenuItem is shared with overworld menus and holds a stale value
        # at the very start of a new battle. Clamp to 0 (FIGHT) only on that first
        # turn. In subsequent turns the cursor genuinely persists to whatever the
        # player last selected (FIGHT, ITEM, PKMN, or RUN).
        if menu_type == "main" and just_entered_battle:
            cursor = 0

        # CC2F: last A-confirmed move slot in the fight submenu (0-3), clamped to num_moves-1.
        num_moves = len(player.get("moves", []))
        raw_fight_cursor = pyboy.memory[_ADDR_PLAYER_MOVE_LIST_IDX]
        actual_fight_cursor = min(raw_fight_cursor, max(num_moves - 1, 0))

        stat_mods = _read_stat_modifiers(pyboy)
        turn_count = pyboy.memory[_ADDR_BATTLE_TURN_COUNT]
        whose_turn = pyboy.memory[_ADDR_BATTLE_WHOSE_TURN]
        raw_player_move_idx = pyboy.memory[_ADDR_PLAYER_MOVE_CHOSEN]
        raw_enemy_move_idx = pyboy.memory[_ADDR_ENEMY_MOVE_CHOSEN]
        player_moves = player.get("moves", [])
        enemy_moves = enemy.get("moves", [])
        player_move_chosen = player_moves[raw_player_move_idx] if raw_player_move_idx < len(player_moves) else None
        enemy_move_chosen = enemy_moves[raw_enemy_move_idx] if raw_enemy_move_idx < len(enemy_moves) else None

        pokeball_count = _count_pokeballs(pyboy)
        battle_items = _read_battle_items(pyboy)
        battle_items["best_ball"] = _find_best_ball(pyboy)  # (name, slot) or None
        party_count = min(pyboy.memory[ADDR_PARTY_COUNT], 6)
        alive_count = _count_alive_party(pyboy, party_count)
        party_levels = _read_party_levels(pyboy, party_count)
        min_plevel = min(party_levels) if party_levels else None
        enemy_owned = _is_dex_owned(pyboy, enemy["species_id"])

        # Determine best move slot
        damage_moves = [(m, m["slot"]) for m in player.get("moves", [])
                        if m["power"] > 1 and m["pp"] > 0]
        if not damage_moves:
            damage_moves = [(m, m["slot"]) for m in player.get("moves", [])
                            if m["power"] > 0 and m["pp"] > 0]
        enemy_types = enemy.get("types", [])
        _ps = player.get("stats") or {}
        _es = enemy.get("stats") or {}
        _ptypes = player.get("types") or []
        _pstatus = player.get("status", "OK")
        best_slot = max(damage_moves, key=lambda p: _effective_power(p, enemy_types, _ps or None, _es or None, _ptypes, _pstatus))[1] if damage_moves else None

        text = _format_battle_text(battle_type, player, enemy, menu_type, cursor,
                                   pokeball_count=pokeball_count,
                                   party_count=party_count,
                                   alive_count=alive_count,
                                   fight_cursor=actual_fight_cursor,
                                   enemy_owned=enemy_owned,
                                   battle_items=battle_items,
                                   safari_state=safari_state,
                                   stat_mods=stat_mods,
                                   turn_count=turn_count,
                                   whose_turn=whose_turn,
                                   player_move_chosen=player_move_chosen,
                                   enemy_move_chosen=enemy_move_chosen,
                                   min_party_level=min_plevel)

        # Log rare Pokemon encounters prominently for debugging
        if battle_type == 1 and enemy["name"] in RARE_POKEMON and not enemy_owned:
            logger.warning(f"RARE ENCOUNTER: {enemy['name']} Lv{enemy['level']} "
                           f"HP:{enemy['hp']}/{enemy['max_hp']} — "
                           f"balls={pokeball_count}, party={party_count}/6")

        logger.info(f"Battle context: {player['name']} Lv{player['level']} "
                     f"HP:{player['hp']}/{player['max_hp']} vs "
                     f"{enemy['name']} Lv{enemy['level']} "
                     f"HP:{enemy['hp']}/{enemy['max_hp']} "
                     f"[menu={menu_type}, cursor={cursor}]")

        return {
            "text": text,
            "player": player,
            "enemy": enemy,
            "menu_type": menu_type,
            "cursor": cursor,
            "battle_type": battle_type,
            "best_slot": best_slot,
            "num_moves": num_moves,
            "is_safari": is_safari,
            "safari_state": safari_state,
            "stat_mods": stat_mods,
            "turn_count": turn_count,
            "whose_turn": whose_turn,
            "player_move_chosen": player_move_chosen,
            "enemy_move_chosen": enemy_move_chosen,
        }
    except Exception as e:
        logger.error(f"Error extracting battle context: {e}", exc_info=True)
        return None
