"""Pokemon Red game data: species names, move data, type system.

All data sourced from the pret/pokered disassembly:
  - constants/pokemon_constants.asm (species IDs)
  - data/moves/moves.asm (move stats)
  - data/types/type_matchups.asm (effectiveness chart)
"""

from typing import Dict, Tuple

# ---------------------------------------------------------------------------
# Gen 1 internal Pokemon ID -> display name
# Sourced from pret/pokered constants/pokemon_constants.asm
# ---------------------------------------------------------------------------

POKEMON_NAMES: Dict[int, str] = {
    0x01: "RHYDON",      0x02: "KANGASKHAN",  0x03: "NIDORAN\u2642",
    0x04: "CLEFAIRY",    0x05: "SPEAROW",     0x06: "VOLTORB",
    0x07: "NIDOKING",    0x08: "SLOWBRO",     0x09: "IVYSAUR",
    0x0A: "EXEGGUTOR",   0x0B: "LICKITUNG",   0x0C: "EXEGGCUTE",
    0x0D: "GRIMER",      0x0E: "GENGAR",      0x0F: "NIDORAN\u2640",
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

# Gen 1 internal species ID -> national dex number.
# Internal IDs are shuffled relative to the national order.
INTERNAL_TO_DEX: Dict[int, int] = {
    0x99: 1,   0x09: 2,   0x9A: 3,   0xB0: 4,   0xB2: 5,   0xB4: 6,
    0xB1: 7,   0xB3: 8,   0x1C: 9,   0x7B: 10,  0x7C: 11,  0x7D: 12,
    0x70: 13,  0x71: 14,  0x72: 15,  0x24: 16,  0x96: 17,  0x97: 18,
    0xA5: 19,  0xA6: 20,  0x05: 21,  0x23: 22,  0x6C: 23,  0x2D: 24,
    0x54: 25,  0x55: 26,  0x60: 27,  0x61: 28,  0x0F: 29,  0xA8: 30,
    0x10: 31,  0x03: 32,  0xA7: 33,  0x07: 34,  0x04: 35,  0x8E: 36,
    0x52: 37,  0x53: 38,  0x64: 39,  0x65: 40,  0x6B: 41,  0x82: 42,
    0xB9: 43,  0xBA: 44,  0xBB: 45,  0x6D: 46,  0x2E: 47,  0x41: 48,
    0x77: 49,  0x3B: 50,  0x76: 51,  0x4D: 52,  0x90: 53,  0x2F: 54,
    0x80: 55,  0x39: 56,  0x75: 57,  0x21: 58,  0x14: 59,  0x47: 60,
    0x6E: 61,  0x6F: 62,  0x94: 63,  0x26: 64,  0x95: 65,  0x6A: 66,
    0x29: 67,  0x7E: 68,  0xBC: 69,  0xBD: 70,  0xBE: 71,  0x18: 72,
    0x9B: 73,  0xA9: 74,  0x27: 75,  0x31: 76,  0xA3: 77,  0xA4: 78,
    0x25: 79,  0x08: 80,  0xAD: 81,  0x36: 82,  0x40: 83,  0x46: 84,
    0x74: 85,  0x3A: 86,  0x78: 87,  0x0D: 88,  0x88: 89,  0x17: 90,
    0x8B: 91,  0x19: 92,  0x93: 93,  0x0E: 94,  0x22: 95,  0x30: 96,
    0x81: 97,  0x4E: 98,  0x8A: 99,  0x06: 100, 0x8D: 101, 0x0C: 102,
    0x0A: 103, 0x11: 104, 0x91: 105, 0x2B: 106, 0x2C: 107, 0x0B: 108,
    0x37: 109, 0x8F: 110, 0x12: 111, 0x01: 112, 0x28: 113, 0x1E: 114,
    0x02: 115, 0x5C: 116, 0x5D: 117, 0x9D: 118, 0x9E: 119, 0x1B: 120,
    0x98: 121, 0x2A: 122, 0x1A: 123, 0x48: 124, 0x35: 125, 0x33: 126,
    0x1D: 127, 0x3C: 128, 0x85: 129, 0x16: 130, 0x13: 131, 0x4C: 132,
    0x66: 133, 0x69: 134, 0x68: 135, 0x67: 136, 0xAA: 137, 0x62: 138,
    0x63: 139, 0x5A: 140, 0x5B: 141, 0xAB: 142, 0x84: 143, 0x4A: 144,
    0x4B: 145, 0x49: 146, 0x58: 147, 0x59: 148, 0x42: 149, 0x83: 150,
    0x15: 151,
}

# ---------------------------------------------------------------------------
# Gen 1 type system
# ---------------------------------------------------------------------------

TYPE_NAMES: Dict[int, str] = {
    0x00: "Normal", 0x01: "Fighting", 0x02: "Flying", 0x03: "Poison",
    0x04: "Ground", 0x05: "Rock", 0x07: "Bug", 0x08: "Ghost",
    0x14: "Fire", 0x15: "Water", 0x16: "Grass", 0x17: "Electric",
    0x18: "Psychic", 0x19: "Ice", 0x1A: "Dragon",
}

# Gen 1 type effectiveness: (attack_type, defend_type) -> multiplier.
# Only non-1.0 entries stored.  Includes the Gen 1 Ghost/Psychic bug (0x).
TYPE_CHART: Dict[Tuple[str, str], float] = {
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

# In Gen 1, move damage category is determined by the move's type (not per-move).
# Special types use Special vs Special; all others use Attack vs Defense.
SPECIAL_TYPES = {"Fire", "Water", "Grass", "Electric", "Ice", "Psychic", "Dragon"}

# ---------------------------------------------------------------------------
# Gen 1 move data: ID -> (name, type, power, base_pp)
# power=0 means status move (no damage).  OHKO/fixed-damage moves use power=1.
# Sourced from pret/pokered data/moves/moves.asm
# ---------------------------------------------------------------------------

MOVE_DATA: Dict[int, Tuple[str, str, int, int]] = {
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
    0x65: ("NIGHT SHADE",  "Ghost",     1, 15),  # Level-based damage (power=1 so TIP treats as damage move)
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

# HM move IDs -> display label
HM_MOVE_IDS: Dict[int, str] = {
    0x0F: "HM01 Cut",
    0x13: "HM02 Fly",
    0x39: "HM03 Surf",
    0x46: "HM04 Strength",
    0x94: "HM05 Flash",
}

# Gen 1 stat stage multipliers (x/100), indexed by stage+6
# (0=stage-6 ... 6=neutral ... 12=stage+6)
STAGE_MULTS = [25, 28, 33, 40, 50, 66, 100, 150, 200, 250, 300, 350, 400]

# Gen 1 item IDs -> (display name, HP restored).  9999 = full HP.
HP_ITEMS: Dict[int, Tuple[str, int]] = {
    0x14: ("Potion",       20),
    0x13: ("Super Potion", 50),
    0x12: ("Hyper Potion", 200),
    0x11: ("Max Potion",   9999),
    0x10: ("Full Restore", 9999),  # also cures status
    0x3C: ("Fresh Water",  50),
    0x3D: ("Soda Pop",     60),
    0x3E: ("Lemonade",     80),
}

# Status cure items: item_id -> (name, set of statuses cured).
STATUS_CURE_ITEMS: Dict[int, Tuple[str, frozenset]] = {
    0x0B: ("Antidote",    frozenset({"PSN"})),
    0x0C: ("Burn Heal",   frozenset({"BRN"})),
    0x0D: ("Ice Heal",    frozenset({"FRZ"})),
    0x0E: ("Awakening",   frozenset({"SLP"})),
    0x0F: ("Parlyz Heal", frozenset({"PAR"})),
    0x34: ("Full Heal",   frozenset({"PSN", "BRN", "FRZ", "SLP", "PAR"})),
    0x10: ("Full Restore",frozenset({"PSN", "BRN", "FRZ", "SLP", "PAR"})),
}

# ---------------------------------------------------------------------------
# Rare / priority-catch Pokemon
# ---------------------------------------------------------------------------
# Species that the agent should always attempt to catch in wild encounters.
# Based on low encounter rates, limited availability, or strong game utility.
# Names must match POKEMON_NAMES values exactly (UPPER CASE).

RARE_POKEMON: frozenset = frozenset({
    # Low encounter rate in their areas
    "CLEFAIRY",     # Mt. Moon ~6%
    "JIGGLYPUFF",   # Route 3 ~10%
    "PIKACHU",      # Viridian Forest ~5%
    "ABRA",         # Teleports turn 1 — hard to catch
    "CHANSEY",      # Safari Zone, extremely rare
    "DRATINI",      # Safari Zone, rare
    "DRAGONAIR",    # Safari Zone, very rare
    "SCYTHER",      # Safari Zone (Red), rare
    "PINSIR",       # Safari Zone (Blue), rare
    "KANGASKHAN",   # Safari Zone, rare
    "TAUROS",       # Safari Zone, rare
    "LAPRAS",       # Gift only (Silph Co.), but flagged in case of edge cases
    "SNORLAX",      # Only 2 in the game
    "EEVEE",        # Gift only (Celadon Mansion)
    "PORYGON",      # Game Corner prize only
    "HITMONLEE",    # Gift only (Fighting Dojo)
    "HITMONCHAN",   # Gift only (Fighting Dojo)
    "MR.MIME",      # In-game trade only
    "FARFETCH'D",   # In-game trade only
    "LICKITUNG",    # Route 18 trade only (Red)
    # Legendaries
    "ARTICUNO",
    "ZAPDOS",
    "MOLTRES",
    "MEWTWO",
    "MEW",
})

# Move IDs that inflict sleep or paralysis — useful for catch strategies.
# Sleep gives 2x catch rate bonus (best), paralysis gives 1.5x.
SLEEP_MOVE_IDS: frozenset = frozenset({
    0x2F,  # SING
    0x4F,  # SLEEP POWDER
    0x5F,  # HYPNOSIS
    0x8E,  # LOVELY KISS
    0x93,  # SPORE
})

PARALYZE_MOVE_IDS: frozenset = frozenset({
    0x4E,  # STUN SPORE
    0x56,  # THUNDER WAVE
    0x89,  # GLARE
})

# Gen 1 character encoding (charmap.asm) — full charset for names and nicknames.
# Covers A-Z, a-z, 0-9, plus special characters needed for Pokemon names
# (e.g. FARFETCH'D apostrophe, PK/MN ligatures).
G1_CHARS = {
    0x7F: " ",
    **{0x80 + i: chr(ord('A') + i) for i in range(26)},
    **{0xA0 + i: chr(ord('a') + i) for i in range(26)},
    **{0xF6 + i: chr(ord('0') + i) for i in range(10)},
    0x60: "'",   # apostrophe (FARFETCH'D, MR. MIME's period is separate)
    0xE1: "PK",  # PK ligature
    0xE2: "MN",  # MN ligature
    0xE3: "-",
    0xE6: "?",
    0xE7: "!",
    0xE8: ".",
    0xF4: ",",
}
