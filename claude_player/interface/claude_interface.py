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
        self._system_prompt = self._build_system_prompt()
    
    def _build_system_prompt(self) -> list:
        """Build the static system prompt once at startup.

        All context blocks (spatial, battle, menu) are included unconditionally so
        the prompt hash never changes between turns — the cache entry stays warm and
        every API call pays the cheap cache-read rate rather than the 1.25x
        cache-creation rate on battle/menu transitions.
        """
        static_parts = [f"""You play a video game in real-time. The game continues between turns — act quickly.
CORE RULE: When a TIP is present, send its exact button sequence via send_inputs. Do not overthink or try alternatives.

<notation>
{button_rules}
</notation>"""]

        if self.config.ACTION.get("DYNAMIC_THINKING", False):
            static_parts.append("""
<thinking_control>
Use toggle_thinking to turn thinking on/off. OFF = faster but less reasoning. Only disable for simple tasks; re-enable at decision points.
</thinking_control>""")

        has_spatial = self.config and getattr(self.config, 'ENABLE_SPATIAL_CONTEXT', False)
        if has_spatial:
            static_parts.append("""
<spatial_context>
Grid legend: .=walkable #=blocked ,=grass ==water v/>/<= ledge T=cut tree B=boulder W=exit @=player 1-9=NPC i=item o=object g=ghost. 1 cell=16 frames.
FOLLOW [path:] hints — they route around walls. NAV(map) = A* through explored map (best signal). If [no path found], try 1-tile steps.
MAP EDGES: walk off edge (no W). WARPS: step ONTO W (no A).
Use large moves (D96, R128) to cover ground fast. NPCs/ITEMS: Walk adjacent + face + A. Always pick up i tiles.
NAME ENTRY: START to finalize. If RAM says dialogue but nothing visible, try movement.
</spatial_context>
<battle_context>
Shows both Pokemon's stats, moves (power=0 = status), and a TIP.
Main menu: FIGHT(0)/ITEM(1) left, PKMN(2)/RUN(3) right. A=confirm, B=back. In submenu/text: B to return, A to advance.
FAINT FLOW: A to advance → "Use next POKEMON?" → A=YES, D/U to pick mon with HP>0, or D A=NO (wild only).
</battle_context>
<menu_context>
Shows menu type, cursor, options, and a TIP. B closes menus, START toggles start menu.
</menu_context>""")

            static_parts.append("""
<authority>
PARTY STATUS, SPATIAL/BATTLE CONTEXT are AUTHORITATIVE (real-time RAM). Trust over memory.
HEAL line = prioritize Pokemon Center. WARNING = address before main goal.
</authority>
<memory>
You have persistent memory (saves/MEMORY.md) updated automatically in the background.
Use read_from_memory when stuck, lost, or entering a familiar area — it may contain routes, dead ends, puzzle hints, and past mistakes.
</memory>""")

        # Custom instructions from config
        if self.config and hasattr(self.config, 'CUSTOM_INSTRUCTIONS') and self.config.CUSTOM_INSTRUCTIONS:
            static_parts.append(f"\n{self.config.CUSTOM_INSTRUCTIONS}")

        static_parts.append("\nAlways use send_inputs to act. Be concise — send compound inputs, not one button at a time.")

        return [
            {
                "type": "text",
                "text": "\n".join(static_parts),
                "cache_control": {"type": "ephemeral"},
            },
        ]

    def get_system_prompt(self) -> list:
        """Return the pre-built static system prompt."""
        return self._system_prompt
    
    def _prepare_tools_cached(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Add cache_control to the last tool definition for prompt caching.

        Tools are identical every turn, so caching them avoids re-processing
        the full tool schema on every API call.
        """
        if not tools:
            return tools
        # Shallow-copy the list; deep-copy only the last tool to add cache_control
        cached = list(tools)
        last = dict(cached[-1])
        last["cache_control"] = {"type": "ephemeral"}
        cached[-1] = last
        return cached

    def send_request(
            self,
            mode_config: Dict[str, Any],
            system_prompt,
            chat_history: List[Dict[str, Any]],
            tools: List[Dict[str, Any]]
        ) -> Any:
        """Send a request to the Claude API using mode configuration.

        Args:
            system_prompt: Either a string (legacy) or a list of content blocks
                           with optional cache_control for prompt caching.
        """
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
                logging.info(f"  Prompt caching: {isinstance(system_prompt, list)}")
                self._logged_config = True

            # Normalise system_prompt: accept plain string (memory manager)
            # or list of content blocks (main agent with caching)
            if isinstance(system_prompt, str):
                system_value = system_prompt
            else:
                system_value = system_prompt  # list of content blocks

            # Create API request params
            request_params = {
                "model": mode_config["MODEL"],
                "max_tokens": mode_config["MAX_TOKENS"],
                "tools": self._prepare_tools_cached(tools),
                "system": system_value,
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
