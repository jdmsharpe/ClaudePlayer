"""Map- and sprite-slot-specific NPC name overrides.

Keyed by (map_number, sprite_slot) where sprite_slot matches the hardware
sprite index n (1-based, since slot 0 is the player).  The slot order matches
the object_event listing order in pret/pokered data/maps/objects/*.asm.

Used to identify named characters who share a generic overworld sprite with
ordinary trainer classes (e.g. Brock uses SPRITE_SUPER_NERD = 0x0C, the same
sprite as random Nerd trainers).

Sprite slot assignments derived from the pret/pokered disassembly:
  https://github.com/pret/pokered
"""

from typing import Dict, Tuple

# (map_number, sprite_slot): display_name
NPC_NAME_OVERRIDES: Dict[Tuple[int, int], str] = {

    # -------------------------------------------------------------------------
    # GYM LEADERS
    # -------------------------------------------------------------------------
    # Koga (0x30) and Giovanni (0x17) already have unique sprites in
    # _SPRITE_NAMES and need no override here.

    # Brock — Pewter Gym (0x36), slot 1: SPRITE_SUPER_NERD
    (0x36, 1): "Brock",

    # Misty — Cerulean Gym (0x41), slot 1: SPRITE_BRUNETTE_GIRL
    (0x41, 1): "Misty",

    # Lt. Surge — Vermilion Gym (0x5C), slot 1: SPRITE_ROCKER
    (0x5C, 1): "Lt. Surge",

    # Erika — Celadon Gym (0x86), slot 1: SPRITE_SILPH_WORKER_F
    (0x86, 1): "Erika",

    # Sabrina — Saffron Gym (0xB2), slot 1: SPRITE_GIRL
    (0xB2, 1): "Sabrina",

    # Blaine — Cinnabar Gym (0xA6), slot 1: SPRITE_MIDDLE_AGED_MAN
    (0xA6, 1): "Blaine",

    # -------------------------------------------------------------------------
    # OTHER STORY-CRITICAL NPCs
    # -------------------------------------------------------------------------

    # Bill — Bill's House (0x58)
    # Slot 1 = SPRITE_MONSTER (Bill transformed as Pokemon — "Monster" label is fine, press A)
    # Slots 2 & 3 = SPRITE_SUPER_NERD (Bill restored to human form; gives S.S. Ticket)
    (0x58, 2): "Bill (transformed)",
    (0x58, 3): "Bill",

    # Pokemon Fan Club Chairman — Pokemon Fan Club (0x5A), slot 5: SPRITE_GENTLEMAN
    # Gives the Bike Voucher needed to get a Bicycle from Cerulean Bike Shop.
    # Slots 1-4 are fan members and decorative Pokemon sprites.
    (0x5A, 5): "Fan Club Chairman",

    # Fossil Scientist — Mt. Moon B2F (0x3D), slot 1: SPRITE_SUPER_NERD
    # Offers choice between Dome Fossil (Kabuto) and Helix Fossil (Omanyte).
    (0x3D, 1): "Fossil Scientist",

    # Copycat — Copycat's House 2F (0xB0), slot 1: SPRITE_BRUNETTE_GIRL
    (0xB0, 1): "Copycat",

    # Bill's Grandpa — Fuchsia Bill's Grandpa House (0x99), slot 2: SPRITE_GAMBLER
    # Gives Eevee as a gift. Slot 1 is a generic woman (his wife).
    (0x99, 2): "Bill's Grandpa",
}
