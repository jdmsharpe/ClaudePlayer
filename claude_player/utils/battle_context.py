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

from claude_player.utils.ram_constants import (
    ADDR_IS_IN_BATTLE as _ADDR_IS_IN_BATTLE,
    ADDR_STATUS_FLAGS5 as _ADDR_STATUS_FLAGS5,
    ADDR_PARTY_COUNT as _ADDR_PARTY_COUNT,
    ADDR_PARTY_BASE as _ADDR_PARTY_BASE,
    PARTY_MON_SIZE as _PARTY_SIZE,
    ADDR_NUM_BAG_ITEMS as _ADDR_NUM_BAG_ITEMS,
    ADDR_BAG_ITEMS as _ADDR_BAG_ITEMS,
    ADDR_MENU_ITEM as _ADDR_MENU_ITEM,
    ADDR_MENU_TOP_Y as _ADDR_MENU_TOP_Y,
    ADDR_MENU_TOP_X as _ADDR_MENU_TOP_X,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RAM addresses (battle-specific, not shared)
# ---------------------------------------------------------------------------

# Player's active Pokemon (wBattleMon)
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

# Enemy's active Pokemon (wEnemyMon)
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

_ADDR_PLAYER_MOVE_LIST_IDX = 0xCC2F  # wPlayerMoveListIndex: last A-confirmed fight slot (0-3)

# Bag / party constants (battle-specific)
_BALL_IDS            = {0x01, 0x02, 0x03, 0x04}  # Master, Ultra, Great, Poke
_PARTY_HP_OFFSET     = 1       # HP is 2-byte big-endian at offset 1

# ---------------------------------------------------------------------------
# Gen 1 type system
# ---------------------------------------------------------------------------

_TYPE_NAMES: Dict[int, str] = {
    0x00: "Normal", 0x01: "Fighting", 0x02: "Flying", 0x03: "Poison",
    0x04: "Ground", 0x05: "Rock", 0x07: "Bug", 0x08: "Ghost",
    0x14: "Fire", 0x15: "Water", 0x16: "Grass", 0x17: "Electric",
    0x18: "Psychic", 0x19: "Ice", 0x1A: "Dragon",
}

# Gen 1 type effectiveness: (attack_type, defend_type) → multiplier
# Only non-1.0 entries stored. Includes the Gen 1 Ghost/Psychic bug (0x).
_TYPE_CHART: Dict[Tuple[str, str], float] = {
    # Normal
    ("Normal", "Rock"): 0.5, ("Normal", "Ghost"): 0.0,
    # Fire
    ("Fire", "Fire"): 0.5, ("Fire", "Water"): 0.5, ("Fire", "Grass"): 2.0,
    ("Fire", "Ice"): 2.0, ("Fire", "Bug"): 2.0, ("Fire", "Rock"): 0.5,
    ("Fire", "Dragon"): 0.5,
    # Water
    ("Water", "Fire"): 2.0, ("Water", "Water"): 0.5, ("Water", "Grass"): 0.5,
    ("Water", "Ground"): 2.0, ("Water", "Rock"): 2.0, ("Water", "Dragon"): 0.5,
    # Electric
    ("Electric", "Water"): 2.0, ("Electric", "Electric"): 0.5,
    ("Electric", "Grass"): 0.5, ("Electric", "Ground"): 0.0,
    ("Electric", "Flying"): 2.0, ("Electric", "Dragon"): 0.5,
    # Grass
    ("Grass", "Fire"): 0.5, ("Grass", "Water"): 2.0, ("Grass", "Grass"): 0.5,
    ("Grass", "Poison"): 0.5, ("Grass", "Ground"): 2.0, ("Grass", "Flying"): 0.5,
    ("Grass", "Bug"): 0.5, ("Grass", "Rock"): 2.0, ("Grass", "Dragon"): 0.5,
    # Ice
    ("Ice", "Fire"): 0.5, ("Ice", "Water"): 0.5, ("Ice", "Grass"): 2.0,
    ("Ice", "Ice"): 0.5, ("Ice", "Ground"): 2.0, ("Ice", "Flying"): 2.0,
    ("Ice", "Dragon"): 2.0,
    # Fighting
    ("Fighting", "Normal"): 2.0, ("Fighting", "Ice"): 2.0,
    ("Fighting", "Poison"): 0.5, ("Fighting", "Flying"): 0.5,
    ("Fighting", "Psychic"): 0.5, ("Fighting", "Bug"): 0.5,
    ("Fighting", "Rock"): 2.0, ("Fighting", "Ghost"): 0.0,
    # Poison
    ("Poison", "Grass"): 2.0, ("Poison", "Poison"): 0.5,
    ("Poison", "Ground"): 0.5, ("Poison", "Rock"): 0.5,
    ("Poison", "Bug"): 2.0, ("Poison", "Ghost"): 0.5,
    # Ground
    ("Ground", "Fire"): 2.0, ("Ground", "Electric"): 2.0,
    ("Ground", "Grass"): 0.5, ("Ground", "Poison"): 2.0,
    ("Ground", "Bug"): 0.5, ("Ground", "Rock"): 2.0, ("Ground", "Flying"): 0.0,
    # Flying
    ("Flying", "Electric"): 0.5, ("Flying", "Grass"): 2.0,
    ("Flying", "Fighting"): 2.0, ("Flying", "Bug"): 2.0, ("Flying", "Rock"): 0.5,
    # Psychic
    ("Psychic", "Fighting"): 2.0, ("Psychic", "Poison"): 2.0,
    ("Psychic", "Psychic"): 0.5,
    # Bug
    ("Bug", "Fire"): 0.5, ("Bug", "Grass"): 2.0, ("Bug", "Fighting"): 0.5,
    ("Bug", "Flying"): 0.5, ("Bug", "Poison"): 2.0, ("Bug", "Psychic"): 2.0,
    ("Bug", "Ghost"): 0.5,
    # Rock
    ("Rock", "Fire"): 2.0, ("Rock", "Ice"): 2.0, ("Rock", "Fighting"): 0.5,
    ("Rock", "Ground"): 0.5, ("Rock", "Flying"): 2.0, ("Rock", "Bug"): 2.0,
    # Ghost  (Gen 1 bug: Ghost has 0x effect on Psychic instead of 2x)
    ("Ghost", "Normal"): 0.0, ("Ghost", "Ghost"): 2.0,
    ("Ghost", "Psychic"): 0.0,
    # Dragon
    ("Dragon", "Dragon"): 2.0,
}


def _type_effectiveness(move_type: str, defend_types: List[str]) -> float:
    """Compute total type effectiveness multiplier for a move vs defender types."""
    mult = 1.0
    for dt in defend_types:
        mult *= _TYPE_CHART.get((move_type, dt), 1.0)
    return mult


def _effective_power(move_slot_pair, enemy_types=None) -> float:
    """Weighted move power accounting for type effectiveness."""
    m = move_slot_pair[0]
    base = m["power"]
    eff = _type_effectiveness(m["type"], enemy_types) if enemy_types else 1.0
    return base * eff


# Main battle menu nav: column-major layout
#   FIGHT(0)  PKMN(2)
#   ITEM(1)   RUN(3)
# U/D = vertical within column; L/R = switch columns
_NAV_TO_ITEM: Dict[int, str] = {
    0: "D",      # FIGHT → ITEM
    1: "",       # already on ITEM
    2: "L D",    # PKMN → ITEM
    3: "L",      # RUN → ITEM
}

# Navigation from each main menu cursor position to every other option
_MAIN_MENU_NAV: Dict[int, Dict[str, str]] = {
    0: {"ITEM": "D",   "PKMN": "R",   "RUN": "R D"},
    1: {"FIGHT": "U",  "PKMN": "R U", "RUN": "R"},
    2: {"FIGHT": "L",  "ITEM": "L D", "RUN": "D"},
    3: {"FIGHT": "U L","ITEM": "L",   "PKMN": "U"},
}

# Absolute navigation — reaches target from ANY main-menu cursor position.
# Extra presses at boundaries are no-ops (cursor doesn't wrap in Gen 1).
_ABS_NAV_FIGHT = "U L"
_ABS_NAV_ITEM  = "D L"
_ABS_NAV_PKMN  = "U R"
_ABS_NAV_RUN   = "D R"

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


# HM move IDs → display label (shared with party_context via import)
_HM_MOVE_IDS: Dict[int, str] = {
    0x0F: "HM01 Cut",
    0x13: "HM02 Fly",
    0x39: "HM03 Surf",
    0x46: "HM04 Strength",
    0x94: "HM05 Flash",
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
    atk_addr: int = 0,
    def_addr: int = 0,
    spd_addr: int = 0,
    spc_addr: int = 0,
    type1_addr: int = 0,
    type2_addr: int = 0,
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

    # Stats (in-battle values, already modified by stat stages)
    stats = {}
    if atk_addr:
        stats = {
            "atk": _read_word(pyboy, atk_addr),
            "def": _read_word(pyboy, def_addr),
            "spd": _read_word(pyboy, spd_addr),
            "spc": _read_word(pyboy, spc_addr),
        }

    moves: List[Dict[str, Any]] = []
    hm_moves: List[str] = []
    for i in range(4):
        move_id = pyboy.memory[moves_addr + i]
        if move_id == 0:
            break
        pp = pyboy.memory[pp_addr + i]
        move_name, move_type, move_power, base_pp = _MOVE_DATA.get(
            move_id, (f"Move#{move_id}", "???", 0, 0)
        )
        is_hm = move_id in _HM_MOVE_IDS
        if is_hm:
            hm_moves.append(_HM_MOVE_IDS[move_id])
        moves.append({
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
        t1 = _TYPE_NAMES.get(pyboy.memory[type1_addr])
        if t1:
            types.append(t1)
        if type2_addr:
            t2 = _TYPE_NAMES.get(pyboy.memory[type2_addr])
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

    Returns "main", "fight", "faint", or "unknown".
    """
    top_y = pyboy.memory[_ADDR_MENU_TOP_Y]
    top_x = pyboy.memory[_ADDR_MENU_TOP_X]

    # If a text message is being printed (e.g. "No! There's no running!"),
    # wCurrentMenuItem holds a transient text-engine value, not the real battle
    # menu cursor. Detect via wStatusFlags5 bit 0 (TEXT_BOX_OPEN) and return
    # "unknown" so the agent knows to press A to advance the text.
    text_box_active = bool(pyboy.memory[_ADDR_STATUS_FLAGS5] & 0x01)

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
    return "unknown"


def _fight_nav_presses(current: int, target: int) -> str:
    """Raw D/U presses to navigate fight submenu from current slot to target slot.

    Gen 1 fight submenu is a single-column vertical list (D/U only):
        slot 0 (top)
        slot 1
        slot 2
        slot 3 (bottom)

    Returns empty string if already at target slot.
    """
    diff = target - current
    if diff < 0:
        return " ".join(["U"] * abs(diff))
    elif diff > 0:
        return " ".join(["D"] * diff)
    return ""


def _count_alive_party(pyboy: PyBoy, party_count: int) -> int:
    """Count party members with HP > 0 from the party data RAM block.

    Note: the active battler's HP in the party block may not be synced to 0
    immediately on faint — callers should account for this (alive_count may be
    inflated by 1 while the active mon is in the process of fainting).
    """
    alive = 0
    for i in range(min(party_count, 6)):
        base = _ADDR_PARTY_BASE + i * _PARTY_SIZE
        hp = (pyboy.memory[base + _PARTY_HP_OFFSET] << 8) | pyboy.memory[base + _PARTY_HP_OFFSET + 1]
        if hp > 0:
            alive += 1
    return alive


def _count_pokeballs(pyboy: PyBoy) -> int:
    """Count total Poke Balls in bag (all ball types)."""
    count = pyboy.memory[_ADDR_NUM_BAG_ITEMS]
    if count == 0 or count > 20:
        return 0
    total = 0
    for i in range(count):
        addr = _ADDR_BAG_ITEMS + (i * 2)
        item_id = pyboy.memory[addr]
        if item_id == 0xFF:
            break
        if item_id in _BALL_IDS:
            total += pyboy.memory[addr + 1]
    return total


# ---------------------------------------------------------------------------
# Battle tip
# ---------------------------------------------------------------------------

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
        # alive_count from party data may be inflated by 1 (the current battler's
        # party slot HP may not have synced to 0 yet), so threshold is > 1.
        has_others = alive_count > 1
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
                "Send: A, then D/U to find a Pokemon with HP > 0, then A to select it. "
                "Skip fainted mons — selecting one shows 'There's no will to fight!'."
            )

    # Catch suggestion: wild battle + have balls + favorable conditions
    if battle_type == 1 and pokeball_count > 0 and enemy["max_hp"] > 0:
        enemy_hp_pct = 100 * enemy["hp"] // enemy["max_hp"]
        should_catch = False
        reason = ""
        if party_count < 6 and enemy_hp_pct <= 40:
            should_catch = True
            reason = f"party {party_count}/6"
        elif enemy_hp_pct <= 20:
            should_catch = True
            reason = "HP very low"

        if should_catch:
            if menu_type == "main":
                return (f"Catch {enemy['name']}! ({reason}, {pokeball_count} balls) "
                        f"— send: {_ABS_NAV_ITEM} A A")
            elif menu_type == "fight":
                return (f"Catch {enemy['name']}! ({reason}, {pokeball_count} balls) "
                        f"— send: B {_ABS_NAV_ITEM} A A")

    # Wild battle + critically low HP → running is safer than fighting
    if battle_type == 1 and player["hp"] > 0 and player["max_hp"] > 0:
        hp_pct = player["hp"] * 100 // player["max_hp"]
        if hp_pct <= 20:
            if menu_type == "main":
                return (f"HP critical ({hp_pct}%) — RUN from this wild battle! "
                        f"Send: {_ABS_NAV_RUN} A")
            elif menu_type == "fight":
                return (f"HP critical ({hp_pct}%) — send: B {_ABS_NAV_RUN} A "
                        f"(B=back, navigate to RUN, confirm).")

    # Find the strongest usable damage move, weighted by type effectiveness
    etypes = enemy_types or []
    _ep = lambda pair: _effective_power(pair, etypes)

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

    if menu_type == "main" and damage_moves:
        best_move, best_slot = max(damage_moves, key=_ep)
        # U L A enters fight submenu. Cursor lands on last-confirmed slot (fight_cursor).
        # Then navigate from fight_cursor to best_slot using D/U.
        nav = _fight_nav_presses(fight_cursor, best_slot)
        compound = f"{_ABS_NAV_FIGHT} A" + (f" {nav} A" if nav else " A")
        eff = _type_effectiveness(best_move["type"], etypes) if etypes else 1.0
        eff_tag = f", {eff:g}x vs {'/'.join(etypes)}" if eff != 1.0 and etypes else ""
        return f"Use {best_move['name']} ({best_move['power']}pwr{eff_tag}) — send: {compound}"

    if menu_type == "main" and not damage_moves:
        if battle_type == 1:  # wild — RUN is an option
            return f"No usable damage moves — RUN from this wild battle! Send: {_ABS_NAV_RUN} A"
        # Trainer battle: cannot RUN. Check if switching to a mon with damage moves is viable.
        can_switch = alive_count > 1
        if can_switch:
            return (f"Trainer battle — no damage moves on this mon. "
                    f"Switch to one with damage moves! Send: {_ABS_NAV_PKMN} A, "
                    f"then D/U to pick a mon with HP > 0, then A.")
        # Unwinnable: only status moves, no switchable mons. Use first move to advance.
        first_move = player["moves"][0]["name"] if player["moves"] else "STRUGGLE"
        compound = f"{_ABS_NAV_FIGHT} A A"  # FIGHT, select move 0
        return (f"Unwinnable: only {first_move} (status). Use it to let the battle end "
                f"→ blackout → free heal at Pokemon Center. Send: {compound}")

    if menu_type == "fight" and damage_moves:
        best_move, best_slot = max(damage_moves, key=_ep)
        eff = _type_effectiveness(best_move["type"], etypes) if etypes else 1.0
        eff_tag = f", {eff:g}x vs {'/'.join(etypes)}" if eff != 1.0 and etypes else ""
        # cursor = wCurrentMenuItem = actual current slot in the single-column fight list.
        # Navigate directly from current cursor position to best slot.
        nav = _fight_nav_presses(cursor, best_slot)
        inputs = (nav + " A").strip() if nav else "A"
        return f"Use {best_move['name']} ({best_move['power']}pwr{eff_tag}) — cursor at slot {cursor+1}, send: {inputs}"

    if menu_type == "fight" and not damage_moves:
        if battle_type == 1:
            return f"No usable damage moves in fight menu — press B then RUN. Send: B {_ABS_NAV_RUN} A"
        # Trainer + no damage: use first available move
        first_move = player["moves"][0]["name"] if player["moves"] else "STRUGGLE"
        return f"No damage moves — use {first_move} to continue. Press A."

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
    pokeball_count: int = 0,
    party_count: int = 6,
    alive_count: int = 0,
    fight_cursor: int = 0,
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
    status_str = f" {player['status']}" if player["status"] != "OK" else ""
    player_type_str = f" [{'/'.join(player.get('types', []))}]" if player.get("types") else ""
    lines.append(
        f"YOUR: {player['name']} Lv{player['level']}{player_type_str} "
        f"HP:{player['hp']}/{player['max_hp']}{status_str}"
    )

    # Stats
    ps = player.get("stats", {})
    if ps:
        lines.append(f"  Stats: Atk:{ps['atk']} Def:{ps['def']} Spd:{ps['spd']} Spc:{ps['spc']}")

    # Moves (mark HMs)
    move_parts = []
    for m in player["moves"]:
        pwr = f"{m['power']}pwr" if m["power"] > 0 else "status"
        hm_tag = " [HM]" if m.get("is_hm") else ""
        move_parts.append(f"{m['name']} ({m['type']},{pwr},{m['pp']}/{m['base_pp']}pp){hm_tag}")
    lines.append(f"  Moves: {' | '.join(move_parts)}")

    # HM summary
    if player.get("hm_moves"):
        lines.append(f"  HMs: {', '.join(player['hm_moves'])}")

    # Cursor
    if menu_type == "main":
        item_name = _MAIN_MENU_ITEMS[cursor] if cursor < 4 else f"#{cursor}"
        nav_hints = _MAIN_MENU_NAV.get(cursor, {})
        nav_str = " | ".join(f"{k}:{v}" for k, v in nav_hints.items())
        lines.append(f"  → Main menu: cursor on {item_name} (to reach: {nav_str})")
    elif menu_type == "fight":
        if cursor < len(player["moves"]):
            lines.append(f"  → Fight menu: cursor on slot {cursor+1} ({player['moves'][cursor]['name']})")
        else:
            lines.append(f"  → Fight menu: cursor at slot {cursor+1}")
    elif menu_type == "faint":
        lines.append(f"  → FAINT FLOW — cursor: {cursor}. 'Use next POKEMON?' or party select. DO NOT mash A blindly!")
    else:
        lines.append(f"  → In submenu/text (not main battle menu) — press B to go back, or A to advance text")

    # VS separator
    lines.append("──────────── VS ────────────")

    # Enemy Pokemon
    enemy_status = f" {enemy['status']}" if enemy["status"] != "OK" else ""
    enemy_type_str = f" [{'/'.join(enemy.get('types', []))}]" if enemy.get("types") else ""
    lines.append(
        f"ENEMY: {enemy['name']} Lv{enemy['level']}{enemy_type_str} "
        f"HP:{enemy['hp']}/{enemy['max_hp']}{enemy_status}"
    )
    es = enemy.get("stats", {})
    if es:
        lines.append(f"  Stats: Atk:{es['atk']} Def:{es['def']} Spd:{es['spd']} Spc:{es['spc']}")
    enemy_move_parts = []
    for m in enemy.get("moves", []):
        pwr = f"{m['power']}pwr" if m["power"] > 0 else "status"
        enemy_move_parts.append(f"{m['name']} ({m['type']},{pwr},{m['pp']}/{m['base_pp']}pp)")
    if enemy_move_parts:
        lines.append(f"  Moves: {' | '.join(enemy_move_parts)}")

    # Tip
    tip = _generate_battle_tip(player, enemy, menu_type, cursor,
                                battle_type=battle_type,
                                pokeball_count=pokeball_count,
                                party_count=party_count,
                                alive_count=alive_count,
                                enemy_types=enemy.get("types"),
                                fight_cursor=fight_cursor)
    if tip:
        lines.append(f"TIP: {tip}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_battle_context(pyboy: PyBoy, just_entered_battle: bool = False, fight_cursor: int = 0) -> Optional[Dict[str, Any]]:
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
        battle_type = pyboy.memory[_ADDR_IS_IN_BATTLE]
        if battle_type == 0:
            return None

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
            logger.debug("Battle context: Pokemon data not ready")
            return None

        cursor = pyboy.memory[_ADDR_MENU_ITEM]
        menu_type = _detect_battle_submenu(pyboy, player_hp=player["hp"])
        # wCurrentMenuItem is shared with overworld menus and holds a stale value
        # at the very start of a new battle. Clamp to 0 (FIGHT) only on that first
        # turn. In subsequent turns the cursor genuinely persists to whatever the
        # player last selected (FIGHT, ITEM, PKMN, or RUN).
        if menu_type == "main" and just_entered_battle:
            cursor = 0

        # wPlayerMoveListIndex: last A-confirmed move slot in the fight submenu (0-3).
        # Read directly from RAM — more reliable than fight_cursor (which was
        # optimistically set to best_slot regardless of what the agent actually chose).
        num_moves = len(player.get("moves", []))
        raw_fight_cursor = pyboy.memory[_ADDR_PLAYER_MOVE_LIST_IDX]
        actual_fight_cursor = min(raw_fight_cursor, max(num_moves - 1, 0))

        pokeball_count = _count_pokeballs(pyboy)
        party_count = min(pyboy.memory[_ADDR_PARTY_COUNT], 6)
        alive_count = _count_alive_party(pyboy, party_count)

        # Determine best move slot
        damage_moves = [(m, m["slot"]) for m in player.get("moves", [])
                        if m["power"] > 1 and m["pp"] > 0]
        if not damage_moves:
            damage_moves = [(m, m["slot"]) for m in player.get("moves", [])
                            if m["power"] > 0 and m["pp"] > 0]
        enemy_types = enemy.get("types", [])
        best_slot = max(damage_moves, key=lambda p: _effective_power(p, enemy_types))[1] if damage_moves else None

        text = _format_battle_text(battle_type, player, enemy, menu_type, cursor,
                                   pokeball_count=pokeball_count,
                                   party_count=party_count,
                                   alive_count=alive_count,
                                   fight_cursor=actual_fight_cursor)

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
        }
    except Exception as e:
        logger.error(f"Error extracting battle context: {e}", exc_info=True)
        return None
