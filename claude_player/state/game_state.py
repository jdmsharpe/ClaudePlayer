import re
import logging
from typing import Optional


class GameState:
    """Manages the state of the game being played."""

    def __init__(self):
        self.identified_game = None
        self.cartridge_title = ""
        self.current_goal = None
        self.turn_count = 0
        self.summary = ""
        self.summary_turn = 0  # Turn when summary was last generated
        self.complete_message_history = []  # Store ALL messages without truncation
        self.runtime_thinking_enabled = True  # Store the runtime thinking state
        self.story_progress = None   # Updated each turn from event flags
        self.party_summary: Optional[str] = None  # Latest 1-line party status from RAM
        self.auto_goal_enabled = True

    def get_current_state_summary(self, compact: bool = False, summary_interval: int = 30) -> str:
        """Get a summary of the current game state.

        Args:
            compact: If True, omit game/goal lines (spatial context already
                     provides GAME STATE and PROGRESS lines).
        """
        parts = []
        if not compact:
            parts.append(f"Current game: {self.identified_game or 'Not identified'}")
            parts.append(f"Current goal: {self.current_goal or 'Not set'}")

        if self.summary:
            age = self.turn_count - self.summary_turn
            age_tag = f"[{age}t ago]"
            summary_text = self.summary
            stale_warning = ""
            if age >= summary_interval - 5:
                # Very stale: redact position claims that may mislead the agent
                summary_text = re.sub(
                    r'(?:at |position |pos )\(?(\d+,\s*\d+)\)?',
                    '[pos redacted]',
                    summary_text,
                )
                stale_warning = f"\n⚠ STALE ({age}t) — trust live context only."
            elif age >= 10:
                stale_warning = "\n⚠ STALE — trust live context over this summary."
            parts.append(f"=== GAME PROGRESS SUMMARY === {age_tag}{stale_warning}\n" + summary_text)

        return "\n".join(parts)
    
    def log_state(self):
        """Log the current game state."""
        logging.info(f"GAME: {self.identified_game or 'Not identified'}")
        logging.info(f"GOAL: {self.current_goal or 'Not set'}")
        logging.info(f"TURN: {self.turn_count}")
        summary_preview = self.summary[:200] + "..." if len(self.summary) > 200 else self.summary
        logging.info(f"SUMMARY: {summary_preview}")

    def increment_turn(self):
        """Increment the turn counter."""
        self.turn_count += 1

    def update_summary(self, summary: str):
        """Update the summary."""
        self.summary = summary
        self.summary_turn = self.turn_count

    def add_to_complete_history(self, message):
        """Add a message to the complete history archive, capping at 120 messages."""
        self.complete_message_history.append(message)
        # Cap history to prevent unbounded RAM growth (summary generator uses last 60)
        max_complete_history = 120
        if len(self.complete_message_history) > max_complete_history:
            self.complete_message_history = self.complete_message_history[-max_complete_history:] 