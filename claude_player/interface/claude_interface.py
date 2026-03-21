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
Grid legend: .=walkable :=lower ground (cave) #=blocked ,=grass ==water T=cut tree B=boulder W=exit @=player 1-9=NPC i=item o=object g=ghost s=sign P=PC. 1 cell=16 frames.
ELEVATION: In caves, . is upper platform and : is lower ground. EAST/WEST between . and : is ALWAYS blocked (invisible wall). NORTH/SOUTH: you CAN step from . (upper, north) down to : (lower, south) and climb back. You CANNOT cross if : is north of . — blocked both ways. NAV(map) accounts for these walls; trust it over manual pathing in caves.
LEDGES: v=jump DOWN only (blocks UP), <=jump LEFT only (blocks RIGHT), >=jump RIGHT only (blocks LEFT). Ledges are one-way — you CANNOT move against the arrow. Plan routes around them.
FOLLOW [path:] hints — they route around walls. NAV(map) = A* through explored map (best signal). If [no path found], try 1-tile steps.
MAP EDGES: walk off edge (no W). WARPS: step ONTO W (no A).
Use large moves (D96, R128) to cover ground fast. NPCs/ITEMS: Walk adjacent + face + A. Always pick up i tiles.
NAME ENTRY: START to finalize. If RAM says dialogue but nothing visible, try movement.
</spatial_context>
<navigation>
COMPASS vs NAV: COMPASS shows crow-flies direction and distance to off-screen exits. These are NOT walkable paths — walls, corridors, and obstacles lie between you and the target. NEVER convert compass block distances into frame inputs (e.g. "6 LEFT, 3 DOWN" does NOT mean "L96 D48"). Always use the NAV(map) A* path instead — it routes around walls through explored tiles.
PRIORITY: NAV(map) > [path:] hints > COMPASS bearing. If NAV(map) is present, follow it. If only COMPASS is available, move in the general compass direction using 1-tile steps (U16/D16/L16/R16) and re-evaluate each turn.
ROUTE PLANNING: When no NAV(map) path is available, move in the general compass direction using 1-tile steps (U16/D16/L16/R16) and re-evaluate each turn. The spatial grid and explored map show obstacles — use them to pick a clear path.
STUCK RECOVERY: If your position is unchanged after a move, you walked into a wall. Do NOT retry the same direction. Try perpendicular directions or follow NAV(map) detour suggestions. If STUCK warnings appear, you are looping — pick a direction you have NOT tried in the last 5 turns.
WARP PATHING: Warps often require indirect paths through corridors and around walls. A warp that is "3 DOWN, 6 LEFT" may require going UP first to find a corridor. Trust NAV(map) for warp routing — it computes the actual walkable path.
DEAD ENDS: If the context says "dead-end" or "looping", leave immediately in the suggested direction. Do not attempt to reach a compass target through a dead-end area.
EXPLORED MAP: The large map shows all tiles you've visited with @ as your position. Use it to identify corridors you haven't explored yet. The map accumulates across turns — revisiting explored areas wastes time. Head toward unexplored edges (shown by ? or map boundaries) to discover new paths.
DUNGEONS: Caves like Mt. Moon have multiple floors connected by ladder warps. The exit to outside may require going through B1F or B2F first — don't assume the entrance floor has a direct exit. Follow NAV(map) routes even when they lead away from your compass target — detours through explored corridors are faster than wall-bumping toward a blocked bearing.
CONNECTIONS: Map edges marked in COMPASS as connections (e.g. "Route 3: ~5 blocks WEST") are reached by walking off the map edge — no warp tile needed. Routes between cities are linear — follow the path and avoid trainers by walking around their line of sight when possible.
GOALS: Three tiers. STRATEGIC GOAL = milestone objective (auto-set from story flags, e.g. "Beat Brock") — do NOT override this for temporary needs. TACTICAL GOAL = immediate map-specific action (auto-derived from your location, e.g. "Enter Pewter Gym from north"). SIDE OBJECTIVES = persistent secondary tasks (heal, catch, buy items) — tracked via add_side_objective, removed via complete_side_objective. NAV routes toward the TACTICAL GOAL when present. Use set_tactical_goal for in-map sub-tasks; use add_side_objective for temporary missions like healing or buying items. Tactical goals auto-clear on map change; side objectives persist until completed.
</navigation>
<battle_context>
Shows both Pokémon's stats, moves (power=0 = status), and a TIP.
Main menu: FIGHT(0)/ITEM(1) left, PKMN(2)/RUN(3) right. A=confirm, B=back. In submenu/text: B to return, A to advance.
FAINT FLOW: A to advance → "Use next POKEMON?" → A=YES, D/U to pick mon with HP>0, or D A=NO (wild only).
RUN: In wild battles, use run_from_battle tool — it handles menu navigation, text dismissal, and auto-retry in one call. Against trainers, RUN always fails — you must fight.
TYPE MATCHUPS: Water beats Fire/Rock/Ground. Electric beats Water/Flying. Fire beats Grass/Bug/Ice. Grass beats Water/Rock/Ground. Use super-effective moves when possible — they deal 2x damage. Avoid not-very-effective moves (0.5x). Normal moves don't affect Ghost types.
HEALING: Use potions (ITEM menu: D to POTION, A, pick Pokémon, A) when HP is below 30%. Visit Pokémon Centers (enter building, walk to counter, talk to nurse) whenever HP drops below 50% and one is nearby. The HEAL line in context means healing is urgent.
WILD ENCOUNTERS: In caves and grass, wild Pokémon appear randomly. FIGHT wild encounters when the TIP says TRAIN (your team needs XP). RUN only when HP is below 30% or your lead has no PP for damage moves. When grinding XP, use the overworld START → POKEMON menu to put an underleveled Pokémon in the lead slot — it gets full XP even if you switch to a stronger mon on turn 1 of battle.
TRAINERS: Trainer battles are mandatory when you walk into their line of sight. You cannot run. Focus on type advantages and use your strongest moves. Switch Pokémon if the current one is at a type disadvantage.
STAT MOVES: Moves like GROWL, LEER, TAIL WHIP lower enemy stats but deal no damage. Only use them if you plan to stay in the fight for multiple turns and need the edge. In most wild battles, just attack or run.
LEVEL ADVANTAGE: If your Pokémon is 5+ levels above the opponent, most attacks will KO in 1-2 hits. If 5+ levels below, switch to a stronger party member or run. Check the level display in battle context to judge.
POKEMON CENTERS: Free full heal for your entire party. Always heal before gym battles and after dungeons. The nurse dialogue requires A to start, A to confirm, then A to dismiss — three A presses total.
</battle_context>
<menu_context>
Shows menu type, cursor, options, and a TIP. B closes menus, START toggles start menu.
POKEMON MENU: Use to check stats, reorder party (put strongest first), or teach TMs. Items can be used outside battle for healing.
SAVE: START → SAVE → A to save progress. The game auto-saves state separately via the emulator.
</menu_context>""")

            memory_interval = self.config.MEMORY.get('MEMORY_INTERVAL', 20) if self.config else 20
            static_parts.append(f"""
<authority>
PARTY STATUS, SPATIAL/BATTLE CONTEXT are AUTHORITATIVE (real-time RAM). Trust over memory.
HEAL line = prioritize Pokémon Center. WARNING = address before main goal.
</authority>
<memory>
Your persistent Knowledge Base is split into two injection points:
- System prompt <memory>: PARTY (team strategy), STRATEGY (current plan), LESSONS (hard-won rules). Updated every {memory_interval} turns.
- User message <location_notes>: per-map notes (paths, dead ends, warps) for the current map only. Changes on map transition.
Both may be slightly stale — trust SPATIAL/BATTLE/PARTY context (real-time RAM) over KB when they conflict.
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

    def get_system_prompt(self, memory_text: str = "") -> list:
        """Return system prompt with optional memory as a second cached block.

        Memory changes infrequently (~every 30 turns), so placing it in the
        system prompt as its own cached content block lets the API serve it
        at the cheap cache-read rate for all turns where it hasn't changed.
        The static block (index 0) always cache-hits regardless of memory.
        """
        if not memory_text:
            return self._system_prompt
        return self._system_prompt + [
            {
                "type": "text",
                "text": memory_text,
                "cache_control": {"type": "ephemeral"},
            },
        ]
    
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
            tools: List[Dict[str, Any]],
            on_stream_event=None,
        ) -> Any:
        """Send a request to the Claude API using mode configuration.

        Args:
            system_prompt: Either a string (legacy) or a list of content blocks
                           with optional cache_control for prompt caching.
            on_stream_event: Optional callback(event_type, data) for streaming
                             token deltas to the web UI. event_type is one of
                             'thinking', 'text', 'stream_start', 'stream_end'.
        """
        try:
            thinking_enabled = mode_config.get("THINKING", False)
            effort = mode_config.get("EFFORT", "medium")

            # Log config once on first request
            if not self._logged_config:
                logging.info(f"API Request Configuration:")
                logging.info(f"  Model: {mode_config.get('MODEL', 'default')}")
                thinking_mode = "disabled"
                if thinking_enabled:
                    thinking_mode = f"budget={mode_config['THINKING_BUDGET']}" if mode_config.get("THINKING_BUDGET") else "adaptive"
                logging.info(f"  Thinking: {thinking_mode}")
                logging.info(f"  Effort: {effort}")
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
                "output_config": {"effort": effort},
            }

            if thinking_enabled:
                # Use budget_tokens if THINKING_BUDGET is set (Sonnet), else adaptive (Opus 4.6)
                budget = mode_config.get("THINKING_BUDGET")
                if budget:
                    request_params["thinking"] = {
                        "type": "enabled",
                        "budget_tokens": budget,
                    }
                else:
                    request_params["thinking"] = {"type": "adaptive"}

            with self.client.messages.stream(**request_params) as stream:
                if on_stream_event is None:
                    return stream.get_final_message()

                # Event-by-event iteration for live token streaming.
                # try/finally guarantees stream_end fires even on API errors,
                # so the browser's _streaming flag never gets stuck true.
                on_stream_event("stream_start", "")
                try:
                    for event in stream:
                        etype = getattr(event, "type", None)
                        if etype == "content_block_delta":
                            delta = getattr(event, "delta", None)
                            if delta is None:
                                continue
                            dtype = getattr(delta, "type", "")
                            if dtype == "thinking_delta":
                                on_stream_event("thinking", delta.thinking)
                            elif dtype == "text_delta":
                                on_stream_event("text", delta.text)
                            # signature_delta and input_json_delta are ignored
                finally:
                    on_stream_event("stream_end", "")
                return stream.get_final_message()
        except Exception as e:
            logging.error(f"ERROR in Claude API request: {str(e)}")
            raise
