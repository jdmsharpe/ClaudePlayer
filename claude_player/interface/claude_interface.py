import os
import logging
from typing import List, Dict, Any
from dotenv import load_dotenv
import anthropic
from claude_player.config.config_class import ConfigClass
from claude_player.utils.game_utils import button_rules


class ClaudeInterface:
    """Interface for interacting with the Claude API."""
    
    def __init__(self, config: ConfigClass = None):
        """Initialize the Claude interface."""
        load_dotenv()

        # Build default headers for beta features
        headers = {}
        if config and config.MODEL_DEFAULTS.get("EFFICIENT_TOOLS", False):
            headers["anthropic-beta"] = "token-efficient-tools-2025-02-19"

        self.client = anthropic.Client(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            default_headers=headers if headers else None,
        )
        self.config = config  # Store the config object
        self._logged_config = False
    
    def generate_system_prompt(self) -> str:
        """Generate the system prompt for Claude."""

        # Dynamic thinking control
        thinking_info = ""
        if self.config.ACTION.get("DYNAMIC_THINKING", False):
            thinking_info = """
<thinking_control>
Use toggle_thinking to turn thinking on/off. OFF = faster but less reasoning. Only disable for simple tasks; re-enable at decision points.
</thinking_control>
"""

        # Spatial context guidance
        spatial_info = ""
        if self.config and getattr(self.config, 'ENABLE_SPATIAL_CONTEXT', False):
            spatial_info = """
<spatial_context>
Each turn includes a SPATIAL CONTEXT grid: . = walkable, # = blocked, W = warp, @ = player, 1-9 = NPC, i = item, o = object.
1 cell = 1 tile = 16 frames (3 cells right = R48). GAME STATE line is RAM-derived and can be stale after transitions.
PROGRESS line shows milestones and auto-sets your current_goal.

MAP EDGES vs DOORS: There are TWO ways to change maps:
- "Map edges" = walk off the edge of the current map (e.g. walk UP off Pallet Town to reach Route 1). No warp tile needed — just keep walking in that direction.
- "Doors/Warps" (W tiles) = building entrances, stairs, caves. Step ONTO the W tile to enter.
To reach a route or city listed under "Map edges", walk toward that edge of the map. Do NOT look for a W tile.
NAVIGATION: Follow [path: ...] suggestions — they route around walls and avoid accidental warps.
If [no path found], try 1-tile exploratory moves in different directions.
For long paths (5+ tiles), execute the first 3-4 tiles then re-check spatial context after screen scrolls.
If "Player didn't move" — you hit a wall, try different direction.
If GAME STATE says dialogue/menu but no text/menu is visible, treat it as stale and try movement.
NAME ENTRY: On "YOUR NAME?" / "RIVAL'S NAME?" keyboard screens when an alphabet is visible, A selects letters (can cause loops). Use START to finalize current name quickly, or choose a preset name menu option then A.
DOORS: Walk ONTO W tiles (no A press). To exit houses: walk DOWN (D16) onto door-mat W tile.
On a W tile but didn't warp? Move UP first, then D16 back onto it.
NPCs/ITEMS: Walk to an adjacent tile and press A while facing them. Follow [path: ...] hints which include the face+interact step. If an NPC blocks your path, go around them.
</spatial_context>
"""

        # Custom instructions from config
        custom_instructions = ""
        if self.config and hasattr(self.config, 'CUSTOM_INSTRUCTIONS') and self.config.CUSTOM_INSTRUCTIONS:
            custom_instructions = f"\n{self.config.CUSTOM_INSTRUCTIONS}\n"

        return f"""You are an AI agent playing a video game running in real-time. The game continues between your analyses. Your inputs are queued and executed as soon as possible — make them robust to slight state changes.

<notation>
{button_rules}
</notation>
{thinking_info}
{spatial_info}
{custom_instructions}
Always use the tools provided to you to interact with the game.
"""
    
    def send_request(
            self,
            mode_config: Dict[str, Any],
            system_prompt: str, 
            chat_history: List[Dict[str, Any]], 
            tools: List[Dict[str, Any]]
        ) -> Any:
        """Send a request to the Claude API using mode configuration."""
        try:
            thinking_enabled = mode_config.get("THINKING", False)

            # Log config once on first request
            if not self._logged_config:
                logging.info(f"API Request Configuration:")
                logging.info(f"  Model: {mode_config.get('MODEL', 'default')}")
                logging.info(f"  Thinking enabled: {thinking_enabled}")
                if thinking_enabled:
                    logging.info(f"  Thinking budget: {mode_config.get('THINKING_BUDGET', 'default')}")
                logging.info(f"  Efficient tools: {mode_config.get('EFFICIENT_TOOLS', False)}")
                logging.info(f"  Max tokens: {mode_config.get('MAX_TOKENS', 'default')}")
                self._logged_config = True

            # Create API request params
            request_params = {
                "model": mode_config["MODEL"],
                "max_tokens": mode_config["MAX_TOKENS"],
                "tools": tools,
                "system": system_prompt,
                "messages": chat_history,
            }

            if thinking_enabled:
                request_params["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": mode_config["THINKING_BUDGET"]
                }

            with self.client.messages.stream(**request_params) as stream:
                return stream.get_final_message()
        except Exception as e:
            logging.error(f"ERROR in Claude API request: {str(e)}")
            raise 
