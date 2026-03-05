import logging
import os
from typing import Dict, Any, List, Optional
from pyboy import PyBoy
from claude_player.state.game_state import GameState
from claude_player.tools.tool_registry import ToolRegistry
from claude_player.utils.game_utils import press_and_release_buttons
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
    
    # Register set_current_goal tool
    @registry.register(
        name="set_current_goal",
        description="Set the current goal in the game. Note: goals are automatically set based on story progress milestones. Only use this to override the auto-goal with a specific sub-task (e.g., 'buy Pokeballs' or 'heal at Pokemon Center').",
        input_schema={
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Current goal"
                }
            },
            "required": ["goal"]
        }
    )
    def handle_set_current_goal(self, tool_input: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.game_state.current_goal = tool_input["goal"]
        logging.info(f"GOAL SET TO: {self.game_state.current_goal}")
        return [{"type": "text", "text": f"Current goal set to {self.game_state.current_goal}"}]

    # --- Memory tools (read-only for main agent; subagent handles writes) ---
    memory_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "saves", "MEMORY.md")

    @registry.register(
        name="read_from_memory",
        description="Read your persistent memory file. Use this when stuck, entering a familiar area, or needing to recall routes, puzzle progress, or past mistakes. Memory is updated automatically in the background.",
        input_schema={
            "type": "object",
            "properties": {},
        }
    )
    def handle_read_from_memory(self, tool_input: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not os.path.exists(memory_path):
            return [{"type": "text", "text": "No memory file exists yet. It will be created automatically after a few turns."}]
        with open(memory_path, "r") as f:
            content = f.read()
        line_count = len(content.split("\n"))
        logging.info(f"Memory read ({line_count} lines)")
        return [{"type": "text", "text": content}]

    @registry.register(
        name="delete_memory",
        description="⚠ DELETE the entire memory file permanently. This cannot be undone. Only use if memory has become corrupted or counterproductive.",
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
    def handle_delete_memory(self, tool_input: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not tool_input.get("confirm"):
            return [{"type": "text", "text": "Deletion not confirmed. Pass confirm=true to delete."}]
        if os.path.exists(memory_path):
            os.remove(memory_path)
            logging.warning("Memory file DELETED")
            return [{"type": "text", "text": "Memory file deleted."}]
        return [{"type": "text", "text": "No memory file to delete."}]

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