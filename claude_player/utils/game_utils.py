import re
import logging
import base64
from io import BytesIO
from pyboy import PyBoy
from pyboy.utils import WindowEvent
from claude_player.utils.ram_constants import ADDR_STATUS_FLAGS5, ADDR_WINDOW_Y

# Define button rules documentation
button_rules = """Buttons: A, B, U (UP), D (DOWN), L (LEFT), R (RIGHT), S (START), X (SELECT), W (WAIT), T (TALK).
Format: BUTTON[FRAMES]. Separate tokens with spaces. A bare letter (no number) = 8-frame press.
  Single press: A — press A once.  Hold: A16 — hold A for 16 frames.
  Simultaneous: AB — press A and B together (8 frames). AB2 — hold both for 2 frames.
  Wait: W — pause 8 frames. W16 — pause 16 frames with no button pressed.
  Talk: T — auto-advance ALL dialogue (keeps pressing A until text box closes). Use for NPC conversations, sign reading, item pickups, nurse healing — any multi-line text. Saves turns vs manual A A A A A.

MOVEMENT: 1 tile = 16 frames. Count tiles, multiply by 16. Max 128 frames/token (8 tiles), 256 total/turn.
  U16 = 1 tile, R48 = 3 tiles, D96 = 6 tiles, L128 = 8 tiles (max single token).
  Chain tokens for long moves: "R128 R64" = 12 tiles right (192 frames). Use the full 256 budget!
CRITICAL: Counts under 16 (e.g. D10) will NOT complete a tile move. Always use multiples of 16.
FACING: U2/D2/L2/R2 = turn to face that direction without moving (2-frame tap). Used in [path:] hints for NPC interaction — e.g. "U32 L2 A" = walk up 2 tiles, face left, press A.
BUTTONS: Use bare A, B, S, X for single presses (8 frames). Avoid A1/B1 — 1-frame presses can be missed.
TALK: Use T after facing an NPC or sign to auto-clear all dialogue. Example: "U32 L2 A T" = walk up, face left, interact, then auto-advance all text. T replaces "A A A A A" for dialogue. Do NOT use T during battle (use explicit A presses for battle menus).
"""

# Button mappings — module-level constants (avoid rebuilding per call)
_BUTTON_MAP = {
    'A': WindowEvent.PRESS_BUTTON_A,
    'B': WindowEvent.PRESS_BUTTON_B,
    'U': WindowEvent.PRESS_ARROW_UP,
    'D': WindowEvent.PRESS_ARROW_DOWN,
    'L': WindowEvent.PRESS_ARROW_LEFT,
    'R': WindowEvent.PRESS_ARROW_RIGHT,
    'S': WindowEvent.PRESS_BUTTON_START,
    'X': WindowEvent.PRESS_BUTTON_SELECT,
    'E': WindowEvent.PRESS_BUTTON_SELECT,  # legacy alias
}

_RELEASE_MAP = {
    'A': WindowEvent.RELEASE_BUTTON_A,
    'B': WindowEvent.RELEASE_BUTTON_B,
    'U': WindowEvent.RELEASE_ARROW_UP,
    'D': WindowEvent.RELEASE_ARROW_DOWN,
    'L': WindowEvent.RELEASE_ARROW_LEFT,
    'R': WindowEvent.RELEASE_ARROW_RIGHT,
    'S': WindowEvent.RELEASE_BUTTON_START,
    'X': WindowEvent.RELEASE_BUTTON_SELECT,
    'E': WindowEvent.RELEASE_BUTTON_SELECT,  # legacy alias
}

_DIR_BUTTONS = frozenset('UDLR')
_TOKEN_RE = re.compile(r'^([A-Za-z]+)(\d+)?$')
_MAX_DIR_FRAMES = 256  # total directional frames allowed per turn
_MAX_SINGLE_DIR = 128  # cap per directional token (8 tiles)


def press_and_release_buttons(pyboy: PyBoy, input_string: str, settle_frames: int = 0, stop_event=None, frame_callback=None, sound_callback=None):
    """
    Parse a button input string and execute the button presses.

    Args:
        pyboy: The PyBoy instance
        input_string: String of button inputs in the format "A5 B2 R3 L1"
        settle_frames: Extra frames to tick after all inputs, letting
                       animations (dialog boxes, screen fades) finish
                       before a screenshot is captured.
        stop_event: Optional threading.Event; if set, abort input execution
                    immediately (for clean shutdown on Ctrl+C).
    """
    if not input_string.strip():
        logging.warning("Received empty input string")
        return

    try:
        _stopping = lambda: stop_event is not None and stop_event.is_set()
        _sound = sound_callback is not None
        _tick = lambda: (pyboy.tick(sound=_sound), frame_callback and frame_callback(pyboy.screen.image), sound_callback and sound_callback())
        total_dir_frames = 0

        for raw_token in input_string.strip().split():
            if _stopping():
                break

            m = _TOKEN_RE.match(raw_token)
            if not m:
                logging.warning(f"Invalid token: {raw_token!r}, skipping")
                continue

            buttons = m.group(1).upper()
            duration = int(m.group(2)) if m.group(2) else 8

            # W = wait (no button press, just tick)
            if buttons == 'W':
                for _ in range(duration):
                    if _stopping():
                        break
                    _tick()
                continue

            # T = auto-advance dialogue (press A until text box closes)
            if buttons == 'T':
                _MAX_TALK_FRAMES = 600  # ~10s safety cap
                _A_HOLD = 8             # frames per A press
                _A_GAP = 12             # frames between presses (let game process)
                _STABLE_BAIL = 3        # bail after N unchanged A presses
                _TILE_MAP = 0xC3A0
                frames_used = 0
                unchanged_count = 0
                prev_snapshot = None
                a_presses = 0
                while frames_used < _MAX_TALK_FRAMES:
                    if _stopping():
                        break
                    # Check if text box is still active
                    status5 = pyboy.memory[ADDR_STATUS_FLAGS5]
                    wy = pyboy.memory[ADDR_WINDOW_Y]
                    text_active = bool(status5 & 0x01) or wy < 144
                    if not text_active and a_presses > 0:
                        # Text cleared — we're done
                        break
                    # Snapshot visible text tiles for stale-detection
                    snapshot = bytes(pyboy.memory[_TILE_MAP + i] for i in range(360))
                    if prev_snapshot is not None and snapshot == prev_snapshot:
                        unchanged_count += 1
                        if unchanged_count >= _STABLE_BAIL:
                            # Text isn't advancing (YES/NO prompt, shop menu, etc.)
                            logging.debug(f"T token: text stale after {a_presses} presses, bailing")
                            break
                    else:
                        unchanged_count = 0
                    prev_snapshot = snapshot
                    # Press A
                    pyboy.send_input(WindowEvent.PRESS_BUTTON_A)
                    for _ in range(_A_HOLD):
                        if _stopping():
                            break
                        _tick()
                    pyboy.send_input(WindowEvent.RELEASE_BUTTON_A)
                    _tick()
                    frames_used += _A_HOLD + 1
                    a_presses += 1
                    # Gap between presses
                    for _ in range(_A_GAP):
                        if _stopping():
                            break
                        _tick()
                    frames_used += _A_GAP
                logging.info(f"T token: {a_presses} A-presses in {frames_used} frames")
                continue

            # Validate all button chars
            invalid = [b for b in buttons if b not in _BUTTON_MAP]
            if invalid:
                logging.warning(f"Unknown button(s) {invalid!r} in {raw_token!r}, skipping")
                continue

            # Cap directional inputs (single dir button only)
            if len(buttons) == 1 and buttons in _DIR_BUTTONS:
                if duration > _MAX_SINGLE_DIR:
                    duration = _MAX_SINGLE_DIR
                remaining = _MAX_DIR_FRAMES - total_dir_frames
                if remaining <= 0:
                    logging.warning(f"Turn frame cap ({_MAX_DIR_FRAMES}) reached — skipping: {raw_token}")
                    continue
                duration = min(duration, remaining)
                total_dir_frames += duration

            # Press all buttons simultaneously
            for b in buttons:
                pyboy.send_input(_BUTTON_MAP[b])

            # Hold for the specified duration
            for _ in range(duration):
                if _stopping():
                    break
                _tick()

            # Release all buttons, then tick so game sees released state
            for b in buttons:
                pyboy.send_input(_RELEASE_MAP[b])
            _tick()

        # Tick extra frames so animations settle before the next screenshot
        for _ in range(settle_frames):
            if _stopping():
                break
            _tick()

    except Exception as e:
        logging.error(f"Error executing button inputs: {str(e)}")

def take_screenshot(pyboy: PyBoy, as_claude_content: bool = False) -> dict:
    """
    Take a screenshot of the current PyBoy screen.

    Args:
        pyboy: The PyBoy instance
        as_claude_content: Whether to format as Claude content block

    Returns:
        Screenshot as image data or Claude content block
    """
    try:
        # PyBoy v2 - screen.image returns a PIL Image directly (160x144)
        screen_image = pyboy.screen.image

        # Convert to proper PNG with PIL and encode as base64
        buffer = BytesIO()
        screen_image.save(buffer, format="PNG")
        buffer.seek(0)
        
        # Convert to base64 string
        base64_string = base64.b64encode(buffer.getvalue()).decode('utf-8')
        
        # For Claude API format
        if as_claude_content:
            return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64_string}}
        
        return screen_image
        
    except Exception as e:
        logging.error(f"Error taking screenshot: {str(e)}")
        # Return placeholder for Claude
        if as_claude_content:
            return {"type": "text", "text": "Error capturing screenshot"}
        return None 