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
Grid legend: .=walkable #=blocked ,=grass ==water v/>/<= ledge T=cut tree B=boulder W=exit @=player 1-9=NPC i=item o=object g=ghost s=sign P=PC. 1 cell=16 frames.
FOLLOW [path:] hints — they route around walls. NAV(map) = A* through explored map (best signal). If [no path found], try 1-tile steps.
MAP EDGES: walk off edge (no W). WARPS: step ONTO W (no A).
Use large moves (D96, R128) to cover ground fast. NPCs/ITEMS: Walk adjacent + face + A. Always pick up i tiles.
NAME ENTRY: START to finalize. If RAM says dialogue but nothing visible, try movement.
</spatial_context>
<navigation>
COMPASS vs NAV: COMPASS shows crow-flies direction and distance to off-screen exits. These are NOT walkable paths — walls, corridors, and obstacles lie between you and the target. NEVER convert compass block distances into frame inputs (e.g. "6 LEFT, 3 DOWN" does NOT mean "L96 D48"). Always use the NAV(map) A* path instead — it routes around walls through explored tiles.
PRIORITY: NAV(map) > [path:] hints > COMPASS bearing. If NAV(map) is present, follow it. If only COMPASS is available, move in the general compass direction using 1-tile steps (U16/D16/L16/R16) and re-evaluate each turn.
STUCK RECOVERY: If your position is unchanged after a move, you walked into a wall. Do NOT retry the same direction. Try perpendicular directions or follow NAV(map) detour suggestions. If STUCK warnings appear, you are looping — pick a direction you have NOT tried in the last 5 turns.
WARP PATHING: Warps often require indirect paths through corridors and around walls. A warp that is "3 DOWN, 6 LEFT" may require going UP first to find a corridor. Trust NAV(map) for warp routing — it computes the actual walkable path.
DEAD ENDS: If the context says "dead-end" or "looping", leave immediately in the suggested direction. Do not attempt to reach a compass target through a dead-end area.
EXPLORED MAP: The large map shows all tiles you've visited with @ as your position. Use it to identify corridors you haven't explored yet. The map accumulates across turns — revisiting explored areas wastes time. Head toward unexplored edges (shown by ? or map boundaries) to discover new paths.
DUNGEONS: Caves like Mt. Moon have multiple floors connected by ladder warps. The exit to outside may require going through B1F or B2F first — don't assume the entrance floor has a direct exit. Follow NAV(map) routes even when they lead away from your compass target — detours through explored corridors are faster than wall-bumping toward a blocked bearing.
CONNECTIONS: Map edges marked in COMPASS as connections (e.g. "Route 3: ~5 blocks WEST") are reached by walking off the map edge — no warp tile needed. Routes between cities are linear — follow the path and avoid trainers by walking around their line of sight when possible.
</navigation>
<battle_context>
Shows both Pokemon's stats, moves (power=0 = status), and a TIP.
Main menu: FIGHT(0)/ITEM(1) left, PKMN(2)/RUN(3) right. A=confirm, B=back. In submenu/text: B to return, A to advance.
FAINT FLOW: A to advance → "Use next POKEMON?" → A=YES, D/U to pick mon with HP>0, or D A=NO (wild only).
RUN: In wild battles, RUN is bottom-right (D R A from FIGHT). Gen 1 escape can fail — send the sequence twice (B D R A B D R A) to retry automatically. Against trainers, RUN always fails — you must fight.
TYPE MATCHUPS: Water beats Fire/Rock/Ground. Electric beats Water/Flying. Fire beats Grass/Bug/Ice. Grass beats Water/Rock/Ground. Use super-effective moves when possible — they deal 2x damage. Avoid not-very-effective moves (0.5x). Normal moves don't affect Ghost types.
HEALING: Use potions (ITEM menu: D to POTION, A, pick Pokemon, A) when HP is below 30%. Visit Pokemon Centers (enter building, walk to counter, talk to nurse) whenever HP drops below 50% and one is nearby. The HEAL line in context means healing is urgent.
WILD ENCOUNTERS: In caves and grass, wild Pokemon appear randomly. RUN from encounters when your team is weak or you're trying to navigate. Only fight if you need XP or are trying to catch something.
TRAINERS: Trainer battles are mandatory when you walk into their line of sight. You cannot run. Focus on type advantages and use your strongest moves. Switch Pokemon if the current one is at a type disadvantage.
STAT MOVES: Moves like GROWL, LEER, TAIL WHIP lower enemy stats but deal no damage. Only use them if you plan to stay in the fight for multiple turns and need the edge. In most wild battles, just attack or run.
LEVEL ADVANTAGE: If your Pokemon is 5+ levels above the opponent, most attacks will KO in 1-2 hits. If 5+ levels below, consider switching or running. Check the level display in battle context to judge.
POKEMON CENTERS: Free full heal for your entire party. Always heal before gym battles and after dungeons. The nurse dialogue requires A to start, A to confirm, then A to dismiss — three A presses total.
</battle_context>
<menu_context>
Shows menu type, cursor, options, and a TIP. B closes menus, START toggles start menu.
POKEMON MENU: Use to check stats, reorder party (put strongest first), or teach TMs. Items can be used outside battle for healing.
SAVE: START → SAVE → A to save progress. The game auto-saves state separately via the emulator.
</menu_context>""")

            static_parts.append("""
<authority>
PARTY STATUS, SPATIAL/BATTLE CONTEXT are AUTHORITATIVE (real-time RAM). Trust over memory.
HEAL line = prioritize Pokemon Center. WARNING = address before main goal.
</authority>
<memory>
Your persistent memory is auto-injected each turn as <memory> in the user message.
It may be slightly stale — trust SPATIAL/BATTLE/PARTY context (real-time RAM) over memory when they conflict.
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

            # system_prompt: plain string (memory manager) or list of content blocks (main agent with caching)
            system_value = system_prompt

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
