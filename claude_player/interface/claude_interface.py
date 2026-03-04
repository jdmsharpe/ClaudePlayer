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
    
    def generate_system_prompt(self, in_battle: bool = False, in_menu: bool = False) -> str:
        """Generate the system prompt for Claude.

        Args:
            in_battle: When True, include battle guidance instead of spatial.
            in_menu: When True, include menu navigation guidance.
        """

        # Dynamic thinking control
        thinking_info = ""
        if self.config.ACTION.get("DYNAMIC_THINKING", False):
            thinking_info = """
<thinking_control>
Use toggle_thinking to turn thinking on/off. OFF = faster but less reasoning. Only disable for simple tasks; re-enable at decision points.
</thinking_control>
"""

        # Context guidance — only include the block relevant to current state
        context_info = ""
        if self.config and getattr(self.config, 'ENABLE_SPATIAL_CONTEXT', False):
            if in_battle:
                context_info = """
<battle_context>
RULE #1: FOLLOW THE TIP. The TIP gives the exact button sequence — send it as-is. Do NOT reason about alternatives when a TIP is present.

BATTLE CONTEXT replaces the spatial grid. It shows both Pokemon's stats, your moves (power=0 = status/no damage), and a TIP.
Main menu: FIGHT(0)/ITEM(1) left, PKMN(2)/RUN(3) right. A = confirm, B = back.
In submenu/text: press B to return to main menu, or A to advance text.
FAINT FLOW: Pokemon faints → A to advance → "Use next POKEMON?" → A=YES then D/U to pick a mon with HP>0, or D A=NO (wild only). Do NOT mash A blindly.
</battle_context>
"""
            else:
                context_info = """
<spatial_context>
Grid legend: . walkable, # blocked, , grass, = water, v/>/< ledge, T cut tree, B boulder, W warp, @ player, 1-9 NPC, i item, o object. 1 tile = 16 frames.
FOLLOW [path:] hints — they route around walls and include the interaction step. If [no path found], try 1-tile steps in each direction.
NAV: A*-computed path through visible obstacles toward off-screen destinations. MOVES: immediate 1-tile walkability. Use both — NAV for multi-step routing, MOVES for quick checks.
MAP EDGES: walk off the edge (no W needed). WARPS: step ONTO W tile (no A). Exit houses: D16 onto door-mat W.
MOVEMENT: Max 128 frames/token, 256 total/turn. Chain: "R128 U128". "Player didn't move" = wall, try another direction.
NPCs/ITEMS: Walk adjacent + A while facing. Always pick up ground items (i tiles).
NAME ENTRY: Press START to finalize. GAME STATE from RAM can be stale — if it says dialogue but nothing visible, try movement.
</spatial_context>
"""

        # Party & inventory guidance — always included (these context blocks
        # are injected conditionally into user messages, not every turn)
        team_info = """
<authority>
PARTY STATUS, SPATIAL CONTEXT, BATTLE CONTEXT are AUTHORITATIVE (real-time RAM). Always trust them over the summary (which may be stale).
If HEAL line appears, prioritize Pokemon Center. WARNING lines = progression blockers — address before main goal.
</authority>
""" if self.config and getattr(self.config, 'ENABLE_SPATIAL_CONTEXT', False) else ""

        menu_info = ""
        if in_menu and self.config and getattr(self.config, 'ENABLE_SPATIAL_CONTEXT', False):
            menu_info = """
<menu_context>
MENU CONTEXT shows menu type, cursor, options, and a TIP with compound input.
FOLLOW THE TIP — send its exact button sequence in ONE send_inputs call. B closes menus. START toggles start menu.
</menu_context>
"""

        # Custom instructions from config
        custom_instructions = ""
        if self.config and hasattr(self.config, 'CUSTOM_INSTRUCTIONS') and self.config.CUSTOM_INSTRUCTIONS:
            custom_instructions = f"\n{self.config.CUSTOM_INSTRUCTIONS}\n"

        return f"""You play a video game in real-time. The game continues between turns — act quickly.
CORE RULE: When a TIP is present, send its exact button sequence via send_inputs. Do not overthink or try alternatives.

<notation>
{button_rules}
</notation>
{thinking_info}
{context_info}
{team_info}
{menu_info}
{custom_instructions}
Always use send_inputs to act. Be concise — send compound inputs, not one button at a time.
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
