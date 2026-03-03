import logging
import base64
from io import BytesIO
from pyboy import PyBoy
from pyboy.utils import WindowEvent

# Define button rules documentation
button_rules = """Buttons: A, B, U (UP), D (DOWN), L (LEFT), R (RIGHT), S (START), E (SELECT).
Format: BUTTON + FRAMES. Separate with spaces. A bare letter (no number) = 4-frame press.
Examples: A = press A. U16 = up 1 tile. "D D A" = menu cursor down twice, confirm.

MOVEMENT: 1 tile = 16 frames. Count tiles, multiply by 16.
  U16 = 1 tile up, R32 = 2 tiles right, D48 = 3 tiles down.
CRITICAL: Counts under 16 (e.g. D10) will NOT complete a tile move. Always use multiples of 16.
BUTTONS: Use bare A, B, S, E for single presses (4 frames). Avoid A1/B1 — 1-frame presses can be missed.
"""

def press_and_release_buttons(pyboy: PyBoy, input_string: str, settle_frames: int = 0):
    """
    Parse a button input string and execute the button presses.

    Args:
        pyboy: The PyBoy instance
        input_string: String of button inputs in the format "A5 B2 R3 L1"
        settle_frames: Extra frames to tick after all inputs, letting
                       animations (dialog boxes, screen fades) finish
                       before a screenshot is captured.
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

        # Define button mappings
        button_map = {
            'A': WindowEvent.PRESS_BUTTON_A,
            'B': WindowEvent.PRESS_BUTTON_B,
            'U': WindowEvent.PRESS_ARROW_UP,
            'D': WindowEvent.PRESS_ARROW_DOWN,
            'L': WindowEvent.PRESS_ARROW_LEFT,
            'R': WindowEvent.PRESS_ARROW_RIGHT,
            'S': WindowEvent.PRESS_BUTTON_START,
            'E': WindowEvent.PRESS_BUTTON_SELECT
        }
        
        release_map = {
            'A': WindowEvent.RELEASE_BUTTON_A,
            'B': WindowEvent.RELEASE_BUTTON_B,
            'U': WindowEvent.RELEASE_ARROW_UP,
            'D': WindowEvent.RELEASE_ARROW_DOWN,
            'L': WindowEvent.RELEASE_ARROW_LEFT,
            'R': WindowEvent.RELEASE_ARROW_RIGHT,
            'S': WindowEvent.RELEASE_BUTTON_START,
            'E': WindowEvent.RELEASE_BUTTON_SELECT
        }
        
        for button_input in inputs:
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
            
            # Verify the button is valid
            if button not in button_map:
                logging.warning(f"Unknown button: {button}, skipping")
                continue
            
            # Press the button
            pyboy.send_input(button_map[button])

            # Hold for the specified duration
            for _ in range(duration):
                # Tick the emulator for each frame of hold time
                pyboy.tick()

            # Release the button and tick once so the game sees
            # the released state before any subsequent press
            pyboy.send_input(release_map[button])
            pyboy.tick()
            
        # Tick extra frames so animations settle before the next screenshot
        for _ in range(settle_frames):
            pyboy.tick()

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