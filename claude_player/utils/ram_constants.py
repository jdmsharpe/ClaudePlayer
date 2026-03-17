"""Shared Pokemon Red/Blue RAM address constants.

Addresses sourced from the pret/pokered disassembly:
  https://github.com/pret/pokered
  - wram.asm (WRAM symbols)
  - hram.asm (HRAM / hardware registers)

All context modules import from here to avoid duplication.
"""

# ---------------------------------------------------------------------------
# Battle / game state detection
# ---------------------------------------------------------------------------
ADDR_IS_IN_BATTLE   = 0xD057   # wIsInBattle: 0=overworld, 1=wild, 2=trainer
ADDR_CUR_MAP        = 0xD35E   # wCurMap: current map number
ADDR_STATUS_FLAGS5  = 0xD730   # wStatusFlags5: bit0=text box, bit5=joypad off, bit7=scripted
ADDR_WINDOW_Y       = 0xFF4A   # WY register: window Y position (< 144 = visible)

# ---------------------------------------------------------------------------
# Party / bag
# ---------------------------------------------------------------------------
ADDR_PARTY_COUNT    = 0xD163   # wPartyCount (0-6)
ADDR_PARTY_BASE     = 0xD16B   # wPartyMon1 (44 bytes each)
PARTY_MON_SIZE      = 44       # bytes per party mon struct

ADDR_NUM_BAG_ITEMS  = 0xD31D   # wNumBagItems (max 20)
ADDR_BAG_ITEMS      = 0xD31E   # wBagItems: (item_id, qty) pairs, 2 bytes each

# ---------------------------------------------------------------------------
# Menu cursor
# ---------------------------------------------------------------------------
ADDR_MENU_ITEM      = 0xCC26   # wCurrentMenuItem (0-based)
ADDR_MENU_TOP_Y     = 0xCC24   # wTopMenuItemY (screen tile row)
ADDR_MENU_TOP_X     = 0xCC25   # wTopMenuItemX (screen tile col)

# ---------------------------------------------------------------------------
# HRAM addresses (hram.asm — 0xFF80-0xFFFE, accessed via LDH instructions)
# ---------------------------------------------------------------------------
ADDR_TILE_PLAYER_ON  = 0xFF93  # hTilePlayerStandingOn: metatile block ID currently under player
ADDR_DISABLE_JOYPAD  = 0xFFF9  # hDisableJoypadPolling: nonzero = joypad ISR skips hardware read
