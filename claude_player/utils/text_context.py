"""Screen text reader: decodes visible text from Pokemon Red's wTileMap.

Pokemon Red renders all on-screen text (dialogue, menus, signs, item pickups)
by writing Gen 1 character tile indices directly into wTileMap (0xC3A0,
20x18 bytes in WRAM).  These indices use the same encoding as G1_CHARS —
no OCR or image processing needed.

The text engine processes control codes (<PLAYER>, <RIVAL>, line breaks)
BEFORE writing to wTileMap, so by the time we read, all text is resolved
into display-ready character tiles.

Terrain tiles occupy 0x00-0x60; text characters are 0x7F+.  Minimal overlap
means we can reliably distinguish text from terrain by charset membership.

References:
  - pret/pokered engine/menus/text_box.asm (TextBoxBorder)
  - pret/pokered home/text.asm (PlaceString)
  - pret/pokered charmap.asm (character encoding)
"""

import logging
from typing import Any, Dict, List, Optional

from pyboy import PyBoy

from claude_player.data.pokemon import G1_CHARS
from claude_player.utils.ram_constants import ADDR_STATUS_FLAGS5, ADDR_WINDOW_Y

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# wTileMap layout: 20 columns x 18 rows = 360 bytes
# ---------------------------------------------------------------------------
_ADDR_TILE_MAP = 0xC3A0
_SCREEN_W = 20  # tiles per row
_SCREEN_H = 18  # tile rows

# ---------------------------------------------------------------------------
# Extended character map for screen text decoding.
# Builds on G1_CHARS (A-Z, a-z, 0-9, basic punctuation) with additional
# characters from pokered's charmap.asm that appear in dialogue/menus.
# ---------------------------------------------------------------------------
_TEXT_CHARS: Dict[int, str] = {
    **G1_CHARS,
    # Punctuation block: 0x9A-0x9F
    0x9A: "(",
    0x9B: ")",
    0x9C: ":",
    0x9D: ";",
    0x9E: "[",
    0x9F: "]",
    # Accented vowel (POKéMON)
    0xBA: "é",
    # Contractions: 0xBB-0xC1 (e.g. "it's", "you'd", "they're")
    0xBB: "'d",
    0xBC: "'l",
    0xBD: "'s",
    0xBE: "'t",
    0xBF: "'v",
    0xC0: "'r",
    0xC1: "'m",
    # Arrows and indicators
    0xEC: "▷",     # scroll indicator
    0xED: "▶",     # menu cursor
    0xEE: "▼",     # continuation arrow (more text available)
    # Symbols
    0xEF: "♂",     # male
    0xF0: "¥",     # Pokédollar
    0xF1: "×",     # multiplication (e.g. ×99)
    0xF2: ".",     # period variant
    0xF3: "/",     # slash
    0xF5: "♀",     # female
    # Ellipsis tile (used in dialogue pauses)
    0x75: "...",
}

# Border tiles drawn by TextBoxBorder — not text content.
# 0x79=┌  0x7A=─  0x7B=┐  0x7C=│(left)  0x7D=│(right)  0x7E=└
# 0x7F (space) is intentionally NOT excluded — it's the space char inside boxes.
_BORDER_TILES = frozenset({0x79, 0x7A, 0x7B, 0x7C, 0x7D, 0x7E})


def extract_text_context(pyboy: PyBoy) -> Optional[Dict[str, Any]]:
    """Read and decode visible on-screen text from wTileMap.

    Only activates when the Window layer is visible (text box, dialogue,
    menu, or full-screen UI).  Uses WY register to determine which screen
    rows contain text.

    Returns:
        None when no text is on screen.  Otherwise a dict with:
            text: Formatted <screen_text> block for turn context injection
            lines: List of decoded text lines
            has_more: True if ▼ continuation arrow is visible
    """
    try:
        status5 = pyboy.memory[ADDR_STATUS_FLAGS5]
        wy = pyboy.memory[ADDR_WINDOW_Y]

        # Gate: text box active (wStatusFlags5 bit 0) OR Window visible (WY < 144)
        if not (status5 & 0x01) and wy >= 144:
            return None

        # Determine text region from Window Y position.
        # WY < 8  → full-screen text (PC, Pokédex, options menu)
        # WY >= 8 → partial overlay (dialogue box starts at row WY//8)
        if wy >= 144:
            # Window not visible but status flag set — scan bottom half as fallback
            start_row = _SCREEN_H // 2
        elif wy < 8:
            start_row = 0
        else:
            start_row = wy // 8

        # Read and decode wTileMap rows in the text region
        lines: List[str] = []
        has_more = False

        for row in range(start_row, _SCREEN_H):
            row_chars: List[str] = []
            for col in range(_SCREEN_W):
                byte = pyboy.memory[_ADDR_TILE_MAP + row * _SCREEN_W + col]

                # Skip border decoration tiles
                if byte in _BORDER_TILES:
                    continue

                # Detect continuation arrow
                if byte == 0xEE:
                    has_more = True

                char = _TEXT_CHARS.get(byte)
                if char is not None:
                    row_chars.append(char)

            line = "".join(row_chars).strip()
            if line:
                lines.append(line)

        if not lines:
            return None

        # Format for injection into turn context
        text_body = "\n".join(lines)
        more_hint = " [press A for more]" if has_more else ""
        formatted = f"<screen_text>{more_hint}\n{text_body}\n</screen_text>"

        return {
            "text": formatted,
            "lines": lines,
            "has_more": has_more,
        }

    except Exception as e:
        logger.debug(f"Text extraction failed: {e}")
        return None
