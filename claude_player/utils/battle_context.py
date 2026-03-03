"""Pokemon Red battle context reader.

Reads battle RAM to provide structured data about the current fight:
both Pokemon's stats, available moves with power/PP, and menu cursor
position.  Injected as text context during battles, replacing the
spatial grid (which is useless on the battle screen).

RAM addresses and data tables sourced from the pret/pokered disassembly.
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from pyboy import PyBoy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RAM addresses
# ---------------------------------------------------------------------------

# Battle state
_ADDR_IS_IN_BATTLE = 0xD057  # 0=no, 1=wild, 2=trainer

# Player's active Pokemon (wBattleMon)
_ADDR_PLAYER_SPECIES = 0xD014
_ADDR_PLAYER_HP      = 0xD015  # 2 bytes, big-endian
_ADDR_PLAYER_STATUS  = 0xD018
_ADDR_PLAYER_MOVES   = 0xD01C  # 4 bytes (move IDs)
_ADDR_PLAYER_LEVEL   = 0xD022
_ADDR_PLAYER_MAX_HP  = 0xD023  # 2 bytes, big-endian
_ADDR_PLAYER_PP      = 0xD02D  # 4 bytes (PP per move slot)

# Enemy's active Pokemon (wEnemyMon)
_ADDR_ENEMY_SPECIES  = 0xCFE5
_ADDR_ENEMY_HP       = 0xCFE6  # 2 bytes, big-endian
_ADDR_ENEMY_STATUS   = 0xCFE9
_ADDR_ENEMY_MOVES    = 0xCFED  # 4 bytes
_ADDR_ENEMY_LEVEL    = 0xCFF3
_ADDR_ENEMY_MAX_HP   = 0xCFF4  # 2 bytes, big-endian
_ADDR_ENEMY_PP       = 0xCFFE  # 4 bytes (PP per move slot)

# Menu cursor
_ADDR_MENU_ITEM      = 0xCC26  # wCurrentMenuItem (0-based)
_ADDR_MENU_TOP_Y     = 0xCC24  # wTopMenuItemY (screen tile row)
_ADDR_MENU_TOP_X     = 0xCC25  # wTopMenuItemX (screen tile col)

# ---------------------------------------------------------------------------
# Gen 1 internal Pokemon ID → display name
# Sourced from pret/pokered constants/pokemon_constants.asm
# ---------------------------------------------------------------------------

_POKEMON_NAMES: Dict[int, str] = {
    0x01: "RHYDON",      0x02: "KANGASKHAN",  0x03: "NIDORAN♂",
    0x04: "CLEFAIRY",    0x05: "SPEAROW",     0x06: "VOLTORB",
    0x07: "NIDOKING",    0x08: "SLOWBRO",     0x09: "IVYSAUR",
    0x0A: "EXEGGUTOR",   0x0B: "LICKITUNG",   0x0C: "EXEGGCUTE",
    0x0D: "GRIMER",      0x0E: "GENGAR",      0x0F: "NIDORAN♀",
    0x10: "NIDOQUEEN",   0x11: "CUBONE",      0x12: "RHYHORN",
    0x13: "LAPRAS",      0x14: "ARCANINE",    0x15: "MEW",
    0x16: "GYARADOS",    0x17: "SHELLDER",    0x18: "TENTACOOL",
    0x19: "GASTLY",      0x1A: "SCYTHER",     0x1B: "STARYU",
    0x1C: "BLASTOISE",   0x1D: "PINSIR",      0x1E: "TANGELA",
    0x21: "GROWLITHE",   0x22: "ONIX",        0x23: "FEAROW",
    0x24: "PIDGEY",      0x25: "SLOWPOKE",    0x26: "KADABRA",
    0x27: "GRAVELER",    0x28: "CHANSEY",     0x29: "MACHOKE",
    0x2A: "MR.MIME",     0x2B: "HITMONLEE",   0x2C: "HITMONCHAN",
    0x2D: "ARBOK",       0x2E: "PARASECT",    0x2F: "PSYDUCK",
    0x30: "DROWZEE",     0x31: "GOLEM",       0x33: "MAGMAR",
    0x35: "ELECTABUZZ",  0x36: "MAGNETON",    0x37: "KOFFING",
    0x39: "MANKEY",      0x3A: "SEEL",        0x3B: "DIGLETT",
    0x3C: "TAUROS",      0x40: "FARFETCH'D",  0x41: "VENONAT",
    0x42: "DRAGONITE",   0x46: "DODUO",       0x47: "POLIWAG",
    0x48: "JYNX",        0x49: "MOLTRES",     0x4A: "ARTICUNO",
    0x4B: "ZAPDOS",      0x4C: "DITTO",       0x4D: "MEOWTH",
    0x4E: "KRABBY",      0x52: "VULPIX",      0x53: "NINETALES",
    0x54: "PIKACHU",     0x55: "RAICHU",      0x58: "DRATINI",
    0x59: "DRAGONAIR",   0x5A: "KABUTO",      0x5B: "KABUTOPS",
    0x5C: "HORSEA",      0x5D: "SEADRA",      0x60: "SANDSHREW",
    0x61: "SANDSLASH",   0x62: "OMANYTE",     0x63: "OMASTAR",
    0x64: "JIGGLYPUFF",  0x65: "WIGGLYTUFF",  0x66: "EEVEE",
    0x67: "FLAREON",     0x68: "JOLTEON",     0x69: "VAPOREON",
    0x6A: "MACHOP",      0x6B: "ZUBAT",       0x6C: "EKANS",
    0x6D: "PARAS",       0x6E: "POLIWHIRL",   0x6F: "POLIWRATH",
    0x70: "WEEDLE",      0x71: "KAKUNA",      0x72: "BEEDRILL",
    0x74: "DODRIO",      0x75: "PRIMEAPE",    0x76: "DUGTRIO",
    0x77: "VENOMOTH",    0x78: "DEWGONG",     0x7B: "CATERPIE",
    0x7C: "METAPOD",     0x7D: "BUTTERFREE",  0x7E: "MACHAMP",
    0x80: "GOLDUCK",     0x81: "HYPNO",       0x82: "GOLBAT",
    0x83: "MEWTWO",      0x84: "SNORLAX",     0x85: "MAGIKARP",
    0x88: "MUK",         0x8A: "KINGLER",     0x8B: "CLOYSTER",
    0x8D: "ELECTRODE",   0x8E: "CLEFABLE",    0x8F: "WEEZING",
    0x90: "PERSIAN",     0x91: "MAROWAK",     0x93: "HAUNTER",
    0x94: "ABRA",        0x95: "ALAKAZAM",    0x96: "PIDGEOTTO",
    0x97: "PIDGEOT",     0x98: "STARMIE",     0x99: "BULBASAUR",
    0x9A: "VENUSAUR",    0x9B: "TENTACRUEL",  0x9D: "GOLDEEN",
    0x9E: "SEAKING",     0xA3: "PONYTA",      0xA4: "RAPIDASH",
    0xA5: "RATTATA",     0xA6: "RATICATE",    0xA7: "NIDORINO",
    0xA8: "NIDORINA",    0xA9: "GEODUDE",     0xAA: "PORYGON",
    0xAB: "AERODACTYL",  0xAD: "MAGNEMITE",   0xB0: "CHARMANDER",
    0xB1: "SQUIRTLE",    0xB2: "CHARMELEON",  0xB3: "WARTORTLE",
    0xB4: "CHARIZARD",   0xB9: "ODDISH",      0xBA: "GLOOM",
    0xBB: "VILEPLUME",   0xBC: "BELLSPROUT",  0xBD: "WEEPINBELL",
    0xBE: "VICTREEBEL",
}

# ---------------------------------------------------------------------------
# Gen 1 move data: ID → (name, type, power, base_pp)
# power=0 means status move (no damage).  OHKO/fixed-damage moves use power=1.
# Sourced from pret/pokered data/moves/moves.asm
# ---------------------------------------------------------------------------

_MOVE_DATA: Dict[int, Tuple[str, str, int, int]] = {
    0x01: ("POUND",        "Normal",   40, 35),
    0x02: ("KARATE CHOP",  "Normal",   50, 25),  # Normal-type in Gen 1
    0x03: ("DOUBLESLAP",   "Normal",   15, 10),
    0x04: ("COMET PUNCH",  "Normal",   18, 15),
    0x05: ("MEGA PUNCH",   "Normal",   80, 20),
    0x06: ("PAY DAY",      "Normal",   40, 20),
    0x07: ("FIRE PUNCH",   "Fire",     75, 15),
    0x08: ("ICE PUNCH",    "Ice",      75, 15),
    0x09: ("THUNDERPUNCH", "Electric", 75, 15),
    0x0A: ("SCRATCH",      "Normal",   40, 35),
    0x0B: ("VICEGRIP",     "Normal",   55, 30),
    0x0C: ("GUILLOTINE",   "Normal",    1,  5),  # OHKO
    0x0D: ("RAZOR WIND",   "Normal",   80, 10),
    0x0E: ("SWORDS DANCE", "Normal",    0, 30),
    0x0F: ("CUT",          "Normal",   50, 30),
    0x10: ("GUST",         "Normal",   40, 35),
    0x11: ("WING ATTACK",  "Flying",   35, 35),
    0x12: ("WHIRLWIND",    "Normal",    0, 20),
    0x13: ("FLY",          "Flying",   70, 15),
    0x14: ("BIND",         "Normal",   15, 20),
    0x15: ("SLAM",         "Normal",   80, 20),
    0x16: ("VINE WHIP",    "Grass",    35, 10),
    0x17: ("STOMP",        "Normal",   65, 20),
    0x18: ("DOUBLE KICK",  "Fighting", 30, 30),
    0x19: ("MEGA KICK",    "Normal",  120,  5),
    0x1A: ("JUMP KICK",    "Fighting", 70, 25),
    0x1B: ("ROLLING KICK", "Fighting", 60, 15),
    0x1C: ("SAND ATTACK",  "Normal",    0, 15),
    0x1D: ("HEADBUTT",     "Normal",   70, 15),
    0x1E: ("HORN ATTACK",  "Normal",   65, 25),
    0x1F: ("FURY ATTACK",  "Normal",   15, 20),
    0x20: ("HORN DRILL",   "Normal",    1,  5),  # OHKO
    0x21: ("TACKLE",       "Normal",   35, 35),
    0x22: ("BODY SLAM",    "Normal",   85, 15),
    0x23: ("WRAP",         "Normal",   15, 20),
    0x24: ("TAKE DOWN",    "Normal",   90, 20),
    0x25: ("THRASH",       "Normal",   90, 20),
    0x26: ("DOUBLE-EDGE",  "Normal",  100, 15),
    0x27: ("TAIL WHIP",    "Normal",    0, 30),
    0x28: ("POISON STING", "Poison",   15, 35),
    0x29: ("TWINEEDLE",    "Bug",      25, 20),
    0x2A: ("PIN MISSILE",  "Bug",      14, 20),
    0x2B: ("LEER",         "Normal",    0, 30),
    0x2C: ("BITE",         "Normal",   60, 25),
    0x2D: ("GROWL",        "Normal",    0, 40),
    0x2E: ("ROAR",         "Normal",    0, 20),
    0x2F: ("SING",         "Normal",    0, 15),
    0x30: ("SUPERSONIC",   "Normal",    0, 20),
    0x31: ("SONICBOOM",    "Normal",    1, 20),  # Fixed 20 damage
    0x32: ("DISABLE",      "Normal",    0, 20),
    0x33: ("ACID",         "Poison",   40, 30),
    0x34: ("EMBER",        "Fire",     40, 25),
    0x35: ("FLAMETHROWER", "Fire",     95, 15),
    0x36: ("MIST",         "Ice",       0, 30),
    0x37: ("WATER GUN",    "Water",    40, 25),
    0x38: ("HYDRO PUMP",   "Water",   120,  5),
    0x39: ("SURF",         "Water",    95, 15),
    0x3A: ("ICE BEAM",     "Ice",      95, 10),
    0x3B: ("BLIZZARD",     "Ice",     120,  5),
    0x3C: ("PSYBEAM",      "Psychic",  65, 20),
    0x3D: ("BUBBLEBEAM",   "Water",    65, 20),
    0x3E: ("AURORA BEAM",  "Ice",      65, 20),
    0x3F: ("HYPER BEAM",   "Normal",  150,  5),
    0x40: ("PECK",         "Flying",   35, 35),
    0x41: ("DRILL PECK",   "Flying",   80, 20),
    0x42: ("SUBMISSION",   "Fighting", 80, 25),
    0x43: ("LOW KICK",     "Fighting", 50, 20),
    0x44: ("COUNTER",      "Fighting",  1, 20),  # Reflects damage
    0x45: ("SEISMIC TOSS", "Fighting",  1, 20),  # Level-based damage
    0x46: ("STRENGTH",     "Normal",   80, 15),
    0x47: ("ABSORB",       "Grass",    20, 20),
    0x48: ("MEGA DRAIN",   "Grass",    40, 10),
    0x49: ("LEECH SEED",   "Grass",     0, 10),
    0x4A: ("GROWTH",       "Normal",    0, 40),
    0x4B: ("RAZOR LEAF",   "Grass",    55, 25),
    0x4C: ("SOLARBEAM",    "Grass",   120, 10),
    0x4D: ("POISONPOWDER", "Poison",    0, 35),
    0x4E: ("STUN SPORE",   "Grass",     0, 30),
    0x4F: ("SLEEP POWDER", "Grass",     0, 15),
    0x50: ("PETAL DANCE",  "Grass",    70, 20),
    0x51: ("STRING SHOT",  "Bug",       0, 40),
    0x52: ("DRAGON RAGE",  "Dragon",    1, 10),  # Fixed 40 damage
    0x53: ("FIRE SPIN",    "Fire",     15, 15),
    0x54: ("THUNDERSHOCK", "Electric", 40, 30),
    0x55: ("THUNDERBOLT",  "Electric", 95, 15),
    0x56: ("THUNDER WAVE", "Electric",  0, 20),
    0x57: ("THUNDER",      "Electric",120, 10),
    0x58: ("ROCK THROW",   "Rock",     50, 15),
    0x59: ("EARTHQUAKE",   "Ground",  100, 10),
    0x5A: ("FISSURE",      "Ground",    1,  5),  # OHKO
    0x5B: ("DIG",          "Ground",  100, 10),
    0x5C: ("TOXIC",        "Poison",    0, 10),
    0x5D: ("CONFUSION",    "Psychic",  50, 25),
    0x5E: ("PSYCHIC",      "Psychic",  90, 10),
    0x5F: ("HYPNOSIS",     "Psychic",   0, 20),
    0x60: ("MEDITATE",     "Psychic",   0, 40),
    0x61: ("AGILITY",      "Psychic",   0, 30),
    0x62: ("QUICK ATTACK", "Normal",   40, 30),
    0x63: ("RAGE",         "Normal",   20, 20),
    0x64: ("TELEPORT",     "Psychic",   0, 20),
    0x65: ("NIGHT SHADE",  "Ghost",     0, 15),  # Level-based damage
    0x66: ("MIMIC",        "Normal",    0, 10),
    0x67: ("SCREECH",      "Normal",    0, 40),
    0x68: ("DOUBLE TEAM",  "Normal",    0, 15),
    0x69: ("RECOVER",      "Normal",    0, 20),
    0x6A: ("HARDEN",       "Normal",    0, 30),
    0x6B: ("MINIMIZE",     "Normal",    0, 20),
    0x6C: ("SMOKESCREEN",  "Normal",    0, 20),
    0x6D: ("CONFUSE RAY",  "Ghost",     0, 10),
    0x6E: ("WITHDRAW",     "Water",     0, 40),
    0x6F: ("DEFENSE CURL", "Normal",    0, 40),
    0x70: ("BARRIER",      "Psychic",   0, 30),
    0x71: ("LIGHT SCREEN", "Psychic",   0, 30),
    0x72: ("HAZE",         "Ice",       0, 30),
    0x73: ("REFLECT",      "Psychic",   0, 20),
    0x74: ("FOCUS ENERGY", "Normal",    0, 30),
    0x75: ("BIDE",         "Normal",    0, 10),
    0x76: ("METRONOME",    "Normal",    0, 10),
    0x77: ("MIRROR MOVE",  "Flying",    0, 20),
    0x78: ("SELFDESTRUCT", "Normal",  130,  5),
    0x79: ("EGG BOMB",     "Normal",  100, 10),
    0x7A: ("LICK",         "Ghost",    20, 30),
    0x7B: ("SMOG",         "Poison",   20, 20),
    0x7C: ("SLUDGE",       "Poison",   65, 20),
    0x7D: ("BONE CLUB",    "Ground",   65, 20),
    0x7E: ("FIRE BLAST",   "Fire",    120,  5),
    0x7F: ("WATERFALL",    "Water",    80, 15),
    0x80: ("CLAMP",        "Water",    35, 10),
    0x81: ("SWIFT",        "Normal",   60, 20),
    0x82: ("SKULL BASH",   "Normal",  100, 15),
    0x83: ("SPIKE CANNON", "Normal",   20, 15),
    0x84: ("CONSTRICT",    "Normal",   10, 35),
    0x85: ("AMNESIA",      "Psychic",   0, 20),
    0x86: ("KINESIS",      "Psychic",   0, 15),
    0x87: ("SOFTBOILED",   "Normal",    0, 10),
    0x88: ("HI JUMP KICK", "Fighting", 85, 20),
    0x89: ("GLARE",        "Normal",    0, 30),
    0x8A: ("DREAM EATER",  "Psychic", 100, 15),
    0x8B: ("POISON GAS",   "Poison",    0, 40),
    0x8C: ("BARRAGE",      "Normal",   15, 20),
    0x8D: ("LEECH LIFE",   "Bug",      20, 15),
    0x8E: ("LOVELY KISS",  "Normal",    0, 10),
    0x8F: ("SKY ATTACK",   "Flying",  140,  5),
    0x90: ("TRANSFORM",    "Normal",    0, 10),
    0x91: ("BUBBLE",       "Water",    20, 30),
    0x92: ("DIZZY PUNCH",  "Normal",   70, 10),
    0x93: ("SPORE",        "Grass",     0, 15),
    0x94: ("FLASH",        "Normal",    0, 20),
    0x95: ("PSYWAVE",      "Psychic",   1, 15),  # Random damage
    0x96: ("SPLASH",       "Normal",    0, 40),
    0x97: ("ACID ARMOR",   "Poison",    0, 40),
    0x98: ("CRABHAMMER",   "Water",    90, 10),
    0x99: ("EXPLOSION",    "Normal",  170,  5),
    0x9A: ("FURY SWIPES",  "Normal",   18, 15),
    0x9B: ("BONEMERANG",   "Ground",   50, 10),
    0x9C: ("REST",         "Psychic",   0, 10),
    0x9D: ("ROCK SLIDE",   "Rock",     75, 10),
    0x9E: ("HYPER FANG",   "Normal",   80, 15),
    0x9F: ("SHARPEN",      "Normal",    0, 30),
    0xA0: ("CONVERSION",   "Normal",    0, 30),
    0xA1: ("TRI ATTACK",   "Normal",   80, 10),
    0xA2: ("SUPER FANG",   "Normal",    1, 10),  # Halves HP
    0xA3: ("SLASH",        "Normal",   70, 20),
    0xA4: ("SUBSTITUTE",   "Normal",    0, 10),
    0xA5: ("STRUGGLE",     "Normal",   50, 10),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_word(pyboy: PyBoy, addr: int) -> int:
    """Read a 2-byte big-endian value."""
    return (pyboy.memory[addr] << 8) | pyboy.memory[addr + 1]


def _decode_status(status_byte: int) -> str:
    """Decode the Gen 1 status condition byte."""
    if status_byte == 0:
        return "OK"
    if status_byte & 0x40:
        return "PAR"
    if status_byte & 0x20:
        return "FRZ"
    if status_byte & 0x10:
        return "BRN"
    if status_byte & 0x08:
        return "PSN"
    if 1 <= status_byte <= 7:
        return f"SLP({status_byte})"
    return f"???(0x{status_byte:02X})"


def _read_pokemon(
    pyboy: PyBoy,
    species_addr: int,
    hp_addr: int,
    status_addr: int,
    moves_addr: int,
    level_addr: int,
    max_hp_addr: int,
    pp_addr: int,
) -> Optional[Dict[str, Any]]:
    """Read a battle Pokemon's data from RAM."""
    species_id = pyboy.memory[species_addr]
    if species_id == 0:
        return None  # slot empty or data not ready

    name = _POKEMON_NAMES.get(species_id, f"???({species_id:#04x})")
    hp = _read_word(pyboy, hp_addr)
    max_hp = _read_word(pyboy, max_hp_addr)
    level = pyboy.memory[level_addr]
    status = _decode_status(pyboy.memory[status_addr])

    moves: List[Dict[str, Any]] = []
    for i in range(4):
        move_id = pyboy.memory[moves_addr + i]
        if move_id == 0:
            break
        pp = pyboy.memory[pp_addr + i]
        move_name, move_type, move_power, base_pp = _MOVE_DATA.get(
            move_id, (f"Move#{move_id}", "???", 0, 0)
        )
        moves.append({
            "name": move_name,
            "type": move_type,
            "power": move_power,
            "pp": pp,
            "base_pp": base_pp,
            "slot": i,
        })

    return {
        "species_id": species_id,
        "name": name,
        "hp": hp,
        "max_hp": max_hp,
        "level": level,
        "status": status,
        "moves": moves,
    }


# ---------------------------------------------------------------------------
# Menu detection
# ---------------------------------------------------------------------------

# Main battle menu items (2x2 grid, cursor 0-3)
_MAIN_MENU_ITEMS = ["FIGHT", "ITEM", "PKMN", "RUN"]


def _detect_battle_submenu(pyboy: PyBoy) -> str:
    """Detect which battle sub-menu is active from cursor position metadata.

    Returns "main", "fight", or "unknown".
    """
    top_y = pyboy.memory[_ADDR_MENU_TOP_Y]
    top_x = pyboy.memory[_ADDR_MENU_TOP_X]

    # Main battle menu (FIGHT/ITEM/PKMN/RUN): top item around Y=14, X=9
    if top_y >= 14 and top_x >= 8:
        return "main"
    # Move selection menu: top item around Y=12, X=4-5
    if 10 <= top_y <= 13 and top_x <= 6:
        return "fight"
    return "unknown"


def _calc_move_nav(current: int, target: int) -> str:
    """Calculate button presses to navigate the 2x2 fight move grid.

    Layout:  Move0  Move1
             Move2  Move3
    """
    cur_row, cur_col = divmod(current, 2)
    tgt_row, tgt_col = divmod(target, 2)
    presses = []
    if tgt_row < cur_row:
        presses.append("U")
    elif tgt_row > cur_row:
        presses.append("D")
    if tgt_col < cur_col:
        presses.append("L")
    elif tgt_col > cur_col:
        presses.append("R")
    nav = " ".join(presses)
    return f"press {nav} then A" if nav else "press A"


# ---------------------------------------------------------------------------
# Battle tip
# ---------------------------------------------------------------------------

def _generate_battle_tip(
    player: Dict[str, Any],
    enemy: Dict[str, Any],
    menu_type: str,
    cursor: int,
) -> Optional[str]:
    """Generate a short tactical recommendation."""
    # Find the strongest usable damage move
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

    if menu_type == "main":
        # On the main battle menu — need to select FIGHT first
        if cursor == 0:
            return "Press A to select FIGHT, then pick your strongest move."
        else:
            nav = []
            cur_r, cur_c = divmod(cursor, 2)
            if cur_r > 0:
                nav.append("U")
            if cur_c > 0:
                nav.append("L")
            return f"Navigate to FIGHT ({' '.join(nav)} then A), then pick a move."

    if menu_type == "fight" and damage_moves:
        best_move, best_slot = max(damage_moves, key=lambda x: x[0]["power"])
        if cursor == best_slot:
            return f"Use {best_move['name']} ({best_move['power']}pwr) — press A."
        else:
            nav = _calc_move_nav(cursor, best_slot)
            return f"Use {best_move['name']} ({best_move['power']}pwr) — {nav}."

    # Low HP
    if player["hp"] > 0 and player["hp"] <= player["max_hp"] // 4:
        return "HP critical! Consider healing or switching."

    return None


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

def _format_battle_text(
    battle_type: int,
    player: Dict[str, Any],
    enemy: Dict[str, Any],
    menu_type: str,
    cursor: int,
) -> str:
    """Assemble the battle context text block."""
    kind = "Wild" if battle_type == 1 else "Trainer"
    wild_prefix = "wild " if battle_type == 1 else ""

    lines = ["=== BATTLE CONTEXT ==="]
    lines.append(
        f"{kind} battle — {player['name']} Lv{player['level']} "
        f"vs {wild_prefix}{enemy['name']} Lv{enemy['level']}"
    )

    # Player Pokemon
    lines.append(
        f"YOUR: {player['name']} Lv{player['level']} "
        f"HP:{player['hp']}/{player['max_hp']} [{player['status']}]"
    )

    # Moves
    move_parts = []
    for m in player["moves"]:
        pwr = f"{m['power']}pwr" if m["power"] > 0 else "status"
        move_parts.append(f"{m['name']} ({m['type']},{pwr},{m['pp']}pp)")
    lines.append(f"  Moves: {' | '.join(move_parts)}")

    # Cursor
    if menu_type == "main":
        item_name = _MAIN_MENU_ITEMS[cursor] if cursor < 4 else f"#{cursor}"
        lines.append(f"  → Main menu: cursor on {item_name}")
    elif menu_type == "fight":
        if cursor < len(player["moves"]):
            lines.append(f"  → Fight menu: cursor on move {cursor+1} ({player['moves'][cursor]['name']})")
        else:
            lines.append(f"  → Fight menu: cursor at position {cursor}")
    else:
        lines.append(f"  → Menu cursor: {cursor} (animation/text — press A)")

    # Enemy Pokemon
    lines.append(
        f"ENEMY: {enemy['name']} Lv{enemy['level']} "
        f"HP:{enemy['hp']}/{enemy['max_hp']} [{enemy['status']}]"
    )

    # Tip
    tip = _generate_battle_tip(player, enemy, menu_type, cursor)
    if tip:
        lines.append(f"TIP: {tip}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_battle_context(pyboy: PyBoy) -> Optional[Dict[str, Any]]:
    """Extract battle context from RAM.

    Must be called on the main thread (PyBoy access is not thread-safe).

    Returns dict with "text" key (formatted string) and structured data,
    or None if not in battle or data isn't ready.
    """
    try:
        battle_type = pyboy.memory[_ADDR_IS_IN_BATTLE]
        if battle_type == 0:
            return None

        player = _read_pokemon(
            pyboy,
            _ADDR_PLAYER_SPECIES, _ADDR_PLAYER_HP, _ADDR_PLAYER_STATUS,
            _ADDR_PLAYER_MOVES, _ADDR_PLAYER_LEVEL, _ADDR_PLAYER_MAX_HP,
            _ADDR_PLAYER_PP,
        )
        enemy = _read_pokemon(
            pyboy,
            _ADDR_ENEMY_SPECIES, _ADDR_ENEMY_HP, _ADDR_ENEMY_STATUS,
            _ADDR_ENEMY_MOVES, _ADDR_ENEMY_LEVEL, _ADDR_ENEMY_MAX_HP,
            _ADDR_ENEMY_PP,
        )

        if not player or not enemy:
            logger.debug("Battle context: Pokemon data not ready")
            return None

        menu_type = _detect_battle_submenu(pyboy)
        cursor = pyboy.memory[_ADDR_MENU_ITEM]

        text = _format_battle_text(battle_type, player, enemy, menu_type, cursor)

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
        }
    except Exception as e:
        logger.error(f"Error extracting battle context: {e}", exc_info=True)
        return None
