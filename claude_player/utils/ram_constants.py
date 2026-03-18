"""Shared Pokemon Red/Blue RAM address constants and byte-level helpers.

Addresses sourced from the pret/pokered disassembly:
  https://github.com/pret/pokered
  - wram.asm (WRAM symbols)
  - hram.asm (HRAM / hardware registers)

Only addresses used by multiple modules live here.  Module-specific
addresses (e.g. the 38 battle stat/move addresses) stay in their own
files to preserve locality.

Byte-level helpers (read_word, decode_status) are shared by both
battle_context and party_context.
"""

from pyboy import PyBoy

# ---------------------------------------------------------------------------
# Battle / game state detection
# ---------------------------------------------------------------------------
ADDR_IS_IN_BATTLE   = 0xD057   # wIsInBattle: 0=overworld, 1=wild, 2=trainer
ADDR_CUR_MAP        = 0xD35E   # wCurMap: current map number
ADDR_STATUS_FLAGS5  = 0xD730   # wStatusFlags5: bit0=text box, bit5=joypad off, bit7=scripted
ADDR_WINDOW_Y       = 0xFF4A   # WY register: window Y position (< 144 = visible)

# ---------------------------------------------------------------------------
# Player position (shared by spatial_context + game_agent)
# ---------------------------------------------------------------------------
ADDR_PLAYER_Y       = 0xD361   # wYCoord: map-block Y coordinate
ADDR_PLAYER_X       = 0xD362   # wXCoord: map-block X coordinate

# ---------------------------------------------------------------------------
# Player identity
# ---------------------------------------------------------------------------
ADDR_PLAYER_NAME    = 0xD158   # wPlayerName: 11 bytes, Gen 1 charset, 0x50=terminator
ADDR_PLAYER_ID      = 0xD359   # wPlayerID: 2 bytes big-endian
ADDR_PLAYER_MONEY   = 0xD347   # wPlayerMoney: 3 bytes, BCD-encoded
ADDR_OBTAINED_BADGES = 0xD356  # wObtainedBadges: 1 byte bitfield

# ---------------------------------------------------------------------------
# Party / bag
# ---------------------------------------------------------------------------
ADDR_PARTY_COUNT    = 0xD163   # wPartyCount (0-6)
ADDR_PARTY_BASE     = 0xD16B   # wPartyMon1 (44 bytes each)
PARTY_MON_SIZE      = 44       # bytes per party mon struct

ADDR_NUM_BAG_ITEMS  = 0xD31D   # wNumBagItems (max 20)
ADDR_BAG_ITEMS      = 0xD31E   # wBagItems: (item_id, qty) pairs, 2 bytes each

# ---------------------------------------------------------------------------
# Pokédex
# ---------------------------------------------------------------------------
ADDR_POKEDEX_OWNED  = 0xD2F7   # wPokedexOwned: 19 bytes, 1 bit per species (#1-151)
ADDR_POKEDEX_SEEN   = 0xD30A   # wPokedexSeen: 19 bytes, same layout

# ---------------------------------------------------------------------------
# Event flags
# ---------------------------------------------------------------------------
ADDR_EVENT_FLAGS    = 0xD747   # wEventFlags: 320-byte bit array (2560 flags)

# ---------------------------------------------------------------------------
# Menu cursor
# ---------------------------------------------------------------------------
ADDR_MENU_ITEM      = 0xCC26   # wCurrentMenuItem (0-based)
ADDR_MENU_TOP_Y     = 0xCC24   # wTopMenuItemY (screen tile row)
ADDR_MENU_TOP_X     = 0xCC25   # wTopMenuItemX (screen tile col)

# ---------------------------------------------------------------------------
# HRAM (0xFF80-0xFFFE — accessed via LDH instructions)
# ---------------------------------------------------------------------------
ADDR_TILE_PLAYER_ON  = 0xFF93  # hTilePlayerStandingOn: metatile block ID under player
ADDR_DISABLE_JOYPAD  = 0xFFF9  # hDisableJoypadPolling: nonzero = joypad ISR skips read


# ---------------------------------------------------------------------------
# Byte-level helpers (shared by battle_context + party_context)
# ---------------------------------------------------------------------------

def read_word(pyboy: PyBoy, addr: int) -> int:
    """Read a 2-byte big-endian value from RAM."""
    return (pyboy.memory[addr] << 8) | pyboy.memory[addr + 1]


def decode_status(status_byte: int) -> str:
    """Decode the Gen 1 status condition byte.

    Returns:
        Human-readable status string: "OK", "PAR", "FRZ", "BRN",
        "PSN", "SLP(N)", or "???(0xNN)" for unknown values.
    """
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
