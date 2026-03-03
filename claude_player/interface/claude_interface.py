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
    
    def generate_system_prompt(self, in_battle: bool = False) -> str:
        """Generate the system prompt for Claude.

        Args:
            in_battle: When True, include battle guidance instead of spatial.
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
During battles, BATTLE CONTEXT replaces the spatial grid. It shows:
- Both Pokemon: name, level, HP (exact from RAM), status condition
- Your moves: name, type, power, remaining PP. power=0 means STATUS move (no damage!)
- Current menu + cursor position, and a TIP with recommended action

BATTLE FLOW: Main menu (FIGHT/ITEM/PKMN/RUN) → select FIGHT → move menu appears → select a move → attacks execute → repeat.
During attack animations or text messages, press A to advance.

FIGHT MENU is 2x2:
  Move1(0)  Move2(1)
  Move3(2)  Move4(3)
U/D = row, L/R = column, A = confirm, B = back to main menu.

STRATEGY: Always prefer damaging moves (power > 0) over status moves in wild battles.
Follow the TIP line — it tells you exactly which buttons to press.
CATCHING: In wild battles, the TIP may suggest catching with Poke Balls via the ITEM menu. Lower enemy HP first for better catch rate. Never waste balls on trainer battles (impossible to catch).
</battle_context>
"""
            else:
                context_info = """
<spatial_context>
Each turn includes a SPATIAL CONTEXT grid: . = walkable, # = blocked, , = grass, = = water, v/>/< = ledge, T = cut tree, B = boulder, W = warp, @ = player, 1-9 = NPC, i = item, o = object.
1 cell = 1 tile = 16 frames (3 cells right = R48). GAME STATE line is RAM-derived and can be stale after transitions.
PROGRESS line shows milestones and auto-sets your current_goal.
TERRAIN: , = tall grass (random wild battles — avoid if HP low). = = water (blocked, need Surf HM+badge). v/>/< = ledge (one-way jump in arrow direction ONLY, cannot climb back — plan routes carefully!). T = cuttable tree (blocked, need Cut HM+badge). B = boulder (blocked, need Strength HM+badge to push).

MAP EDGES vs DOORS: There are TWO ways to change maps:
- "Map edges" = walk off the edge of the current map (e.g. walk UP off Pallet Town to reach Route 1). No warp tile needed — just keep walking in that direction.
- "Doors/Warps" (W tiles) = building entrances, stairs, caves. Step ONTO the W tile to enter.
To reach a route or city listed under "Map edges", walk toward that edge of the map. Do NOT look for a W tile.
NAVIGATION: Follow [path: ...] suggestions — they route around walls and avoid accidental warps.
If [no path found], try 1-tile exploratory moves in different directions.
For long paths (5+ tiles), execute the first 3-4 tiles then re-check spatial context after screen scrolls.
MOVEMENT: Max 4 tiles (64 frames) per direction token. Chain shorter moves: "R64 U64" not "R128". Always check the grid for walls before choosing a direction.
If "Player didn't move" — you hit a wall, try different direction.
If GAME STATE says dialogue/menu but no text/menu is visible, treat it as stale and try movement.
NAME ENTRY: On "YOUR NAME?" / "RIVAL'S NAME?" keyboard screens when an alphabet is visible, A selects letters (can cause loops). Use START to finalize current name quickly, or choose a preset name menu option then A.
DOORS: Walk ONTO W tiles (no A press). To exit houses: walk DOWN (D16) onto door-mat W tile.
On a W tile but didn't warp? Move UP first, then D16 back onto it.
NPCs/ITEMS: Walk to an adjacent tile and press A while facing them. Follow [path: ...] hints which include the face+interact step. If an NPC blocks your path, go around them.
PICKUP ITEMS: Always pick up ground items (i tiles) — they contain TMs, Poke Balls, potions, and other useful rewards. Follow the [path:] hint to walk onto the item tile. Prioritize nearby items before continuing your route.
ITEMS ON TABLES: Pokeballs, items on tables/desks are NOT shown on the grid (they're background tiles, not sprites). Walk next to the table and press A while facing it to interact. In Oak's Lab, the 3 starter Pokeballs are on a table — walk to an adjacent tile and press A. After choosing your starter, do NOT go back for the remaining Pokeballs on Oak's table.
</spatial_context>
"""

        # Party & inventory guidance — always included (these context blocks
        # are injected conditionally into user messages, not every turn)
        team_info = """
<team_and_inventory>
PARTY STATUS (injected when HP/status changes or periodically): Shows each Pokemon's level, HP, status, and moves with PP.
PARTY STATUS is AUTHORITATIVE (real-time from RAM) — always trust it over any HP/status claims in the summary.
If HEAL line appears, prioritize visiting a Pokemon Center. Lead fainted = switch or heal immediately.

INVENTORY (injected when items change or periodically): Shows badges, money, key items, balls, medicine.
HM usability: ✓ = can use in field, "(need Badge)" = have HM but lack the badge. Plan routes around HM access.
WARNING lines = progression blockers (missing HMs, no Poke Balls, etc.) — address before continuing main goal.
</team_and_inventory>
""" if self.config and getattr(self.config, 'ENABLE_SPATIAL_CONTEXT', False) else ""

        # Custom instructions from config
        custom_instructions = ""
        if self.config and hasattr(self.config, 'CUSTOM_INSTRUCTIONS') and self.config.CUSTOM_INSTRUCTIONS:
            custom_instructions = f"\n{self.config.CUSTOM_INSTRUCTIONS}\n"

        return f"""You are an AI agent playing a video game running in real-time. The game continues between your analyses. Your inputs are queued and executed as soon as possible — make them robust to slight state changes.

<notation>
{button_rules}
</notation>
{thinking_info}
{context_info}
{team_info}
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
