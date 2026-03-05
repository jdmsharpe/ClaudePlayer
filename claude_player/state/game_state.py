import logging
from typing import Optional


class GameState:
    """Manages the state of the game being played."""

    def __init__(self):
        self.identified_game = None
        self.cartridge_title = ""
        self.current_goal = None
        self.turn_count = 0
        self.memory_turn = 0  # Turn when memory was last written
        self.complete_message_history = []  # Store ALL messages without truncation
        self.runtime_thinking_enabled = True  # Store the runtime thinking state
        self.story_progress = None   # Updated each turn from event flags
        self.party_summary: Optional[str] = None  # Latest 1-line party status from RAM
        self.auto_goal_enabled = True
        self.fight_cursor: int = 0   # Tracked fight-submenu cursor; updated each battle turn

    def get_current_state_header(self, compact: bool = False) -> str:
        """Get a brief state header for the user message.

        Args:
            compact: If True, omit game/goal lines (spatial context already
                     provides GAME STATE and PROGRESS lines).
        """
        parts = []
        if not compact:
            parts.append(f"Current game: {self.identified_game or 'Not identified'}")
            parts.append(f"Current goal: {self.current_goal or 'Not set'}")
        return "\n".join(parts)
    
    def log_state(self):
        """Log the current game state."""
        logging.info(f"GAME: {self.identified_game or 'Not identified'}")
        logging.info(f"GOAL: {self.current_goal or 'Not set'}")
        logging.info(f"TURN: {self.turn_count}")
        logging.info(f"MEMORY LAST WRITTEN: turn {self.memory_turn}")

    def increment_turn(self):
        """Increment the turn counter."""
        self.turn_count += 1

    def add_to_complete_history(self, message):
        """Add a message to the complete history archive, capping at 120 messages."""
        self.complete_message_history.append(message)
        # Cap history to prevent unbounded RAM growth (summary generator uses last 60)
        max_complete_history = 120
        if len(self.complete_message_history) > max_complete_history:
            self.complete_message_history = self.complete_message_history[-max_complete_history:] 