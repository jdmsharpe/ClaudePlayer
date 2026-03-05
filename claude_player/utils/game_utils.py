import logging
import base64
from io import BytesIO
from pyboy import PyBoy
from pyboy.utils import WindowEvent

# Define button rules documentation
button_rules = """Buttons: A, B, U (UP), D (DOWN), L (LEFT), R (RIGHT), S (START), E (SELECT).
Format: BUTTON + FRAMES. Separate with spaces. A bare letter (no number) = 4-frame press.
Examples: A = press A. U16 = up 1 tile. "D D A" = menu cursor down twice, confirm.

MOVEMENT: 1 tile = 16 frames. Count tiles, multiply by 16. Max 128 frames/token (8 tiles), 256 total/turn.
  U16 = 1 tile, R48 = 3 tiles, D96 = 6 tiles, L128 = 8 tiles (max single token).
  Chain tokens for long moves: "R128 R64" = 12 tiles right (192 frames). Use the full 256 budget!
CRITICAL: Counts under 16 (e.g. D10) will NOT complete a tile move. Always use multiples of 16.
FACING: U2/D2/L2/R2 = turn to face that direction without moving (2-frame tap). Used in [path:] hints for NPC interaction — e.g. "U32 L2 A" = walk up 2 tiles, face left, press A.
BUTTONS: Use bare A, B, S, E for single presses (4 frames). Avoid A1/B1 — 1-frame presses can be missed.
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
    'E': WindowEvent.PRESS_BUTTON_SELECT,
}

_RELEASE_MAP = {
    'A': WindowEvent.RELEASE_BUTTON_A,
    'B': WindowEvent.RELEASE_BUTTON_B,
    'U': WindowEvent.RELEASE_ARROW_UP,
    'D': WindowEvent.RELEASE_ARROW_DOWN,
    'L': WindowEvent.RELEASE_ARROW_LEFT,
    'R': WindowEvent.RELEASE_ARROW_RIGHT,
    'S': WindowEvent.RELEASE_BUTTON_START,
    'E': WindowEvent.RELEASE_BUTTON_SELECT,
}

_DIR_BUTTONS = frozenset('UDLR')
_MAX_DIR_FRAMES = 256  # total directional frames allowed per turn
_MAX_SINGLE_DIR = 128  # cap per directional token (8 tiles)


def press_and_release_buttons(pyboy: PyBoy, input_string: str, settle_frames: int = 0, stop_event=None, frame_callback=None):
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
        # Parse input string — normalize bare letters (e.g. "A" → "A4")
        # Bare letter = 4-frame press (enough for the game to register).
        # 1-frame presses can be missed; 16 frames wastes time.
        raw_tokens = input_string.strip().split()
        inputs = [t + "4" if len(t) == 1 and t.isalpha() else t for t in raw_tokens]

        _stopping = lambda: stop_event is not None and stop_event.is_set()
        _tick = lambda: (pyboy.tick(), frame_callback and frame_callback(pyboy.screen.image))
        total_dir_frames = 0

        for button_input in inputs:
            if _stopping():
                break

            # Extract button and duration
            if len(button_input) == 1:
                # Single character means press for 1 frame
                button = button_input
                duration = 1
            else:
                # Otherwise parse the button and duration
                button = button_input[0]
                try:
                    duration = int(button_input[1:])
                except ValueError:
                    logging.warning(f"Invalid button input: {button_input}, using duration of 1")
                    duration = 1

            # Cap directional inputs to 8 tiles to force re-evaluation
            if button in _DIR_BUTTONS and duration > _MAX_SINGLE_DIR:
                duration = _MAX_SINGLE_DIR

            # Cap total directional frames per turn to prevent FPS drops and
            # force re-evaluation after a full screen's worth of movement.
            if button in _DIR_BUTTONS:
                remaining = _MAX_DIR_FRAMES - total_dir_frames
                if remaining <= 0:
                    logging.warning(f"Turn frame cap ({_MAX_DIR_FRAMES}) reached — skipping: {button_input}")
                    continue
                duration = min(duration, remaining)
                total_dir_frames += duration

            # Verify the button is valid
            if button not in _BUTTON_MAP:
                logging.warning(f"Unknown button: {button}, skipping")
                continue

            # Press the button
            pyboy.send_input(_BUTTON_MAP[button])

            # Hold for the specified duration
            for _ in range(duration):
                if _stopping():
                    break
                _tick()

            # Release the button and tick once so the game sees
            # the released state before any subsequent press
            pyboy.send_input(_RELEASE_MAP[button])
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