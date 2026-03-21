import logging
import os
from typing import Dict, Any, List, Optional
from pyboy import PyBoy
from claude_player.state.game_state import GameState
from claude_player.tools.tool_registry import ToolRegistry
from claude_player.utils.game_utils import press_and_release_buttons
from claude_player.utils.ram_constants import ADDR_IS_IN_BATTLE, ADDR_PLAYER_X, ADDR_PLAYER_Y
from claude_player.config.config_class import ConfigClass


def setup_tool_registry(pyboy: PyBoy, game_state: GameState, config: Optional[ConfigClass] = None) -> ToolRegistry:
    """Set up the tool registry with all available tools."""
    registry = ToolRegistry(pyboy, game_state)
    
    # Register send_inputs tool
    @registry.register(
        name="send_inputs",
        description="Send a sequence of button inputs to the game emulator. Please follow the notation rules.",
        input_schema={
            "type": "object",
            "properties": {
                "inputs": {
                    "type": "string",
                    "description": "Sequence of inputs, e.g., 'R5 U2 A2'"
                }
            },
            "required": ["inputs"]
        }
    )
    def handle_send_inputs(self, tool_input: Dict[str, Any]) -> List[Dict[str, Any]]:
        inputs = tool_input["inputs"]
        logging.info(f"EXECUTING INPUTS: {inputs}")
        press_and_release_buttons(self.pyboy, inputs)
        return [{"type": "text", "text": "Inputs sent successfully"}]
    
    # Register set_strategic_goal tool
    @registry.register(
        name="set_strategic_goal",
        description="Set the high-level strategic goal (milestone objective). Auto-goal normally handles this from story flags — do NOT override unless the auto-goal is genuinely wrong. For temporary tasks like healing, buying items, or catching Pokemon, use add_side_objective instead. Clears any tactical override so map-based hints resume.",
        input_schema={
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Strategic milestone goal"
                }
            },
            "required": ["goal"]
        }
    )
    def handle_set_strategic_goal(self, tool_input: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.game_state.strategic_goal = tool_input["goal"]
        self.game_state.tactical_goal = None  # Evict stale tactical hint
        self.game_state._tactical_goal_override = False  # Resume auto-derivation
        logging.info(f"STRATEGIC GOAL SET TO: {self.game_state.strategic_goal}")
        return [{"type": "text", "text": f"Strategic goal set to: {self.game_state.strategic_goal}"}]

    # Register set_tactical_goal tool
    @registry.register(
        name="set_tactical_goal",
        description="Set the immediate tactical goal for the current map. Persists until you change maps. Use for specific in-map tasks like 'talk to NPC at north exit' or 'find hidden stairs'. Auto-cleared on map change.",
        input_schema={
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Map-specific tactical action"
                }
            },
            "required": ["goal"]
        }
    )
    def handle_set_tactical_goal(self, tool_input: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.game_state.tactical_goal = tool_input["goal"]
        self.game_state._tactical_goal_override = True
        self.game_state._tactical_override_grace = 1  # survive 1 map change (e.g. floor hop)
        logging.info(f"TACTICAL GOAL SET TO: {self.game_state.tactical_goal}")
        return [{"type": "text", "text": f"Tactical goal set to: {self.game_state.tactical_goal} (auto-clears on map change)"}]

    # Register add_side_objective tool
    @registry.register(
        name="add_side_objective",
        description="Add a side objective (persists across map changes, unlike tactical goals). Use for secondary tasks: 'Heal at Pokémon Center', 'Catch a Pikachu', 'Buy Potions'. Max 5 side objectives.",
        input_schema={
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "description": "Short side objective description"
                }
            },
            "required": ["objective"]
        }
    )
    def handle_add_side_objective(self, tool_input: Dict[str, Any]) -> List[Dict[str, Any]]:
        obj = tool_input["objective"]
        if len(self.game_state.side_objectives) >= 5:
            return [{"type": "text", "text": f"Error: Max 5 side objectives. Complete or clear one first. Current: {' | '.join(self.game_state.side_objectives)}"}]
        if obj in self.game_state.side_objectives:
            return [{"type": "text", "text": f"Already tracked: {obj}"}]
        self.game_state.side_objectives.append(obj)
        logging.info(f"SIDE OBJECTIVE ADDED: {obj}")
        return [{"type": "text", "text": f"Side objective added: {obj} (total: {len(self.game_state.side_objectives)})"}]

    # Register complete_side_objective tool
    @registry.register(
        name="complete_side_objective",
        description="Mark a side objective as done and remove it. Pass the exact text or a substring to match.",
        input_schema={
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "description": "Side objective text (or substring) to complete"
                }
            },
            "required": ["objective"]
        }
    )
    def handle_complete_side_objective(self, tool_input: Dict[str, Any]) -> List[Dict[str, Any]]:
        query = tool_input["objective"].lower()
        for i, obj in enumerate(self.game_state.side_objectives):
            if query in obj.lower():
                removed = self.game_state.side_objectives.pop(i)
                logging.info(f"SIDE OBJECTIVE COMPLETED: {removed}")
                remaining = self.game_state.side_objectives
                msg = f"Completed: {removed}"
                if remaining:
                    msg += f" | Remaining: {' | '.join(remaining)}"
                return [{"type": "text", "text": msg}]
        return [{"type": "text", "text": f"No matching side objective for '{tool_input['objective']}'. Current: {' | '.join(self.game_state.side_objectives) or '(none)'}"}]

    # --- Knowledge Base tools ---
    from claude_player.agent.knowledge_base import KnowledgeBase
    saves_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "saves")
    _kb = KnowledgeBase(saves_dir)

    @registry.register(
        name="delete_knowledge",
        description="⚠ DELETE the entire Knowledge Base permanently. This cannot be undone. Only use if KB has become corrupted or counterproductive.",
        input_schema={
            "type": "object",
            "properties": {
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true to confirm deletion"
                }
            },
            "required": ["confirm"]
        }
    )
    def handle_delete_knowledge(self, tool_input: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not tool_input.get("confirm"):
            return [{"type": "text", "text": "Deletion not confirmed. Pass confirm=true to delete."}]
        _kb.delete_all()
        return [{"type": "text", "text": "Knowledge Base deleted."}]

    # Register run_from_battle tool — generates button sequence, queued by game_agent
    @registry.register(
        name="run_from_battle",
        description="Run from the current wild battle. Handles menu navigation and text dismissal automatically with retry on failure. Cannot run from trainer battles.",
        input_schema={
            "type": "object",
            "properties": {},
            "required": []
        }
    )
    def handle_run_from_battle(self, tool_input: Dict[str, Any]) -> List[Dict[str, Any]]:
        from claude_player.utils.battle_context import _detect_battle_submenu

        battle_type = self.pyboy.memory[ADDR_IS_IN_BATTLE]
        if battle_type == 0:
            return [{"type": "text", "text": "Error: Not in a battle"}]
        if battle_type == 2:
            return [{"type": "text", "text": "Error: Cannot run from trainer battles — must fight"}]

        # Detect current battle submenu to generate the right preamble.
        # With _BATTLE_START_SETTLE=6s, analysis starts ~6s after battle start.
        # By then, intro animations are usually done and submenu detection works.
        # The "unknown" branch is a safety net for edge cases (text overlay, etc.).
        submenu = _detect_battle_submenu(self.pyboy)

        # RUN tail: navigate to RUN + retry once if escape fails
        # (state_change_event aborts remaining presses when battle ends)
        run_tail = "D R A W48 A A A B B D R A W48 A A A A"

        if submenu == "main":
            # Already at main menu — go straight to RUN
            sequence = run_tail
        elif submenu == "fight":
            # In fight submenu — B to back out, then RUN
            sequence = f"B {run_tail}"
        elif submenu == "pkmn":
            # In Pokémon submenu — B B to back out, then RUN
            sequence = f"B B {run_tail}"
        else:
            # Intro text, text overlay, or unknown state.
            # A presses advance text; W64 waits give animations time to finish.
            # 3x (A W64) covers "Wild X appeared!" + "Go! POKEMON!" + buffer.
            # W64 lets the main menu fully render before we navigate.
            sequence = f"A W64 A W64 A W64 W64 {run_tail}"

        logging.info(f"run_from_battle: submenu={submenu}, sequence length={len(sequence.split())}")
        return [{"type": "text", "text": sequence}]

    # Only register toggle_thinking tool if both THINKING and DYNAMIC_THINKING are enabled
    if config and config.MODEL_DEFAULTS.get("DYNAMIC_THINKING", False):
        @registry.register(
            name="toggle_thinking",
            description="Toggle the thinking capability on or off. Use this to control whether you want to use your thinking capabilities.",
            input_schema={
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "Set to true to enable thinking, false to disable thinking"
                    }
                },
                "required": ["enabled"]
            }
        )
        def handle_toggle_thinking(self, tool_input: Dict[str, Any]) -> List[Dict[str, Any]]:
            # Store runtime thinking state in GameState instead of modifying config
            enabled = tool_input["enabled"]
            self.game_state.runtime_thinking_enabled = enabled
            
            # Log the change
            status = "enabled" if enabled else "disabled"
            logging.info(f"Dynamic thinking control: Runtime thinking has been {status}")
            
            return [{"type": "text", "text": f"Thinking has been {status}. This will take effect on the next API request."}]

    return registry 