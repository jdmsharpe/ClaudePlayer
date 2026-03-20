import logging
from typing import List, Optional


class GameState:
    """Manages the state of the game being played."""

    def __init__(self):
        self.identified_game = None
        self.cartridge_title = ""
        # Two-tier goal system: strategic (milestone) + tactical (map-specific)
        self.strategic_goal: Optional[str] = None
        self.tactical_goal: Optional[str] = None
        self._tactical_goal_override: bool = False  # True when agent manually set tactical goal
        self.turn_count = 0
        self.memory_turn = 0  # Turn when memory was last written
        self.complete_message_history = []  # Store ALL messages without truncation
        self.runtime_thinking_enabled = True  # Store the runtime thinking state
        self.story_progress = None   # Updated each turn from event flags
        self.party_summary: Optional[str] = None  # Latest 1-line party status from RAM
        self.auto_goal_enabled = True
        self.fight_cursor: int = 0   # Tracked fight-submenu cursor; updated each battle turn
        self.visited_maps: set = set()  # Map IDs ever visited; used for visit-check milestones
        self.side_objectives: List[str] = []  # Persistent side goals (heal, catch, buy items)

    @property
    def current_goal(self) -> Optional[str]:
        """Backward-compatible accessor: returns tactical goal if set, else strategic."""
        return self.tactical_goal or self.strategic_goal

    @current_goal.setter
    def current_goal(self, value: Optional[str]):
        """Backward-compatible setter: writes to strategic_goal."""
        self.strategic_goal = value

    def get_current_state_header(self, compact: bool = False) -> str:
        """Get a brief state header for the user message.

        Args:
            compact: If True, omit game/goal lines (spatial context already
                     provides GAME STATE and PROGRESS lines).
        """
        parts = []
        if not compact:
            parts.append(f"Current game: {self.identified_game or 'Not identified'}")
            parts.append(f"Strategic goal: {self.strategic_goal or 'Not set'}")
            if self.tactical_goal:
                parts.append(f"Tactical goal: {self.tactical_goal}")
            if self.side_objectives:
                parts.append(f"Side objectives: {' | '.join(self.side_objectives)}")
        return "\n".join(parts)

    def log_state(self, map_id=None, map_name=None, player_pos=None,
                   in_battle=False):
        """Log the current game state.

        Args:
            map_id: Current map ID (hex), or None if unknown.
            map_name: Human-readable map name, or None.
            player_pos: (x, y) player position tuple, or None.
            in_battle: Whether the agent is currently in battle.
        """
        logging.info(f"GAME: {self.identified_game or 'Not identified'}")
        logging.info(f"STRATEGIC GOAL: {self.strategic_goal or 'Not set'}")
        if self.tactical_goal:
            logging.info(f"TACTICAL GOAL: {self.tactical_goal}")
        if self.side_objectives:
            logging.info(f"SIDE OBJECTIVES: {' | '.join(self.side_objectives)}")
        # Location context: map + position in one line for easy grepping
        loc_parts = []
        if map_id is not None:
            loc_parts.append(f"map=0x{map_id:02X}")
        if map_name:
            loc_parts.append(f"({map_name})")
        if player_pos:
            loc_parts.append(f"pos=({player_pos[0]},{player_pos[1]})")
        if in_battle:
            loc_parts.append("IN_BATTLE")
        if loc_parts:
            logging.info(f"LOCATION: {' '.join(loc_parts)}")
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