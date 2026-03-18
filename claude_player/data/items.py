"""Pokemon Red item data: names, categories, badges, HM requirements.

Sourced from pret/pokered constants/item_constants.asm.
"""

from typing import Dict

# ---------------------------------------------------------------------------
# Item ID -> display name (complete Gen 1 inventory)
# ---------------------------------------------------------------------------

ITEM_NAMES: Dict[int, str] = {
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
    # TMs (0xC9 = TM01 through 0xFA = TM50)
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
KEY_ITEMS = {
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
BADGE_NAMES = [
    "Boulder",    # bit 0 -- Brock (Pewter)
    "Cascade",    # bit 1 -- Misty (Cerulean)
    "Thunder",    # bit 2 -- Lt. Surge (Vermilion)
    "Rainbow",    # bit 3 -- Erika (Celadon)
    "Soul",       # bit 4 -- Koga (Fuchsia)
    "Marsh",      # bit 5 -- Sabrina (Saffron)
    "Volcano",    # bit 6 -- Blaine (Cinnabar)
    "Earth",      # bit 7 -- Giovanni (Viridian)
]

# HM item ID -> (display name, required badge bit for field use)
HM_BADGE_REQS: Dict[int, tuple] = {
    0xC4: ("Cut",      1),  # Cascade Badge (bit 1)
    0xC5: ("Fly",      2),  # Thunder Badge (bit 2)
    0xC6: ("Surf",     4),  # Soul Badge (bit 4)
    0xC7: ("Strength", 3),  # Rainbow Badge (bit 3)
    0xC8: ("Flash",    0),  # Boulder Badge (bit 0)
}

# Item categories for grouping
BALL_IDS = {0x01, 0x02, 0x03, 0x04, 0x08}  # Master, Ultra, Great, Poke, Safari
MEDICINE_IDS = {
    0x0B, 0x0C, 0x0D, 0x0E, 0x0F,  # Status heals
    0x10, 0x11, 0x12, 0x13, 0x14,  # HP restores
    0x34, 0x35, 0x36,              # Full Heal, Revive, Max Revive
    0x50, 0x51, 0x52, 0x53,        # Ethers/Elixirs
}
BATTLE_ITEM_IDS = {
    0x2E, 0x37, 0x3A,  # X Accuracy, Guard Spec., Dire Hit
    0x41, 0x42, 0x43, 0x44,  # X Attack/Defend/Speed/Special
    0x33,  # Poke Doll
}
