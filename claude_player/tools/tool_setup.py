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
        description="Set the high-level strategic goal (milestone objective). Auto-goal normally handles this from story flags. Only use to override with a specific mission like 'heal at Pokémon Center' or 'buy Potions at Mart'. Clears any tactical override so map-based hints resume.",
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
        logging.info(f"TACTICAL GOAL SET TO: {self.game_state.tactical_goal}")
        return [{"type": "text", "text": f"Tactical goal set to: {self.game_state.tactical_goal} (auto-clears on map change)"}]

    # --- Memory tools (read_from_memory removed — now auto-injected into user message) ---
    memory_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "saves", "MEMORY.md")

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

    # Register check_tiles tool — read-only, executes immediately (no emulator ticking)
    @registry.register(
        name="check_tiles",
        description="Check what tiles lie ahead in a direction from the player. Returns tile types, passability, and ledge info. Use this to plan routes around obstacles instead of guessing. Does NOT consume your action — you can check_tiles AND send_inputs in the same turn.",
        input_schema={
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right"],
                    "description": "Direction to scan from player position"
                },
                "distance": {
                    "type": "integer",
                    "description": "Number of tiles to check (1-8, default 4)"
                }
            },
            "required": ["direction"]
        }
    )
    def handle_check_tiles(self, tool_input: Dict[str, Any]) -> List[Dict[str, Any]]:
        from claude_player.utils.spatial_context import _extract_terrain_data, _extract_npc_data, _overlay_npcs_on_grid

        if self.pyboy.memory[ADDR_IS_IN_BATTLE] != 0:
            return [{"type": "text", "text": "Error: Cannot check tiles during battle"}]

        direction = tool_input["direction"].lower()
        distance = min(max(tool_input.get("distance", 4), 1), 8)

        terrain = _extract_terrain_data(self.pyboy)
        if terrain is None:
            return [{"type": "text", "text": "Error: Terrain data unavailable"}]

        # Player screen position from OAM sprite 0 (same logic as spatial_context)
        s0 = self.pyboy.get_sprite(0)
        px = s0.x // 8 // 2
        py = (s0.y // 8 + 1) // 2

        # Overlay NPC/item/object sprites so check_tiles sees the full picture
        npc_data = _extract_npc_data(self.pyboy)
        if npc_data:
            _overlay_npcs_on_grid(terrain, npc_data, (px, py))

        grid_h = len(terrain)
        grid_w = len(terrain[0]) if terrain else 0

        dx, dy = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[direction]

        tile_desc = {
            '.': 'walkable', '#': 'BLOCKED (wall)', ',': 'grass (walkable)',
            'v': 'ledge DOWN', '<': 'ledge LEFT', '>': 'ledge RIGHT',
            '=': 'water (BLOCKED)', 'T': 'tree (BLOCKED, need Cut)', 'W': 'warp/exit',
            'i': 'ITEM (face it + press A to pick up)',
            'B': 'BOULDER (need Strength to push)',
            'o': 'OBJECT (blocked)',
            'g': 'GHOST (walkable, need Silph Scope to identify)',
        }
        # Sprites that block movement (player can't walk through them)
        sprite_blocked = frozenset({'i', 'B', 'o'})
        # NPC digits 1-9 and 'n' also block movement
        npc_chars = frozenset({str(d) for d in range(1, 10)} | {'n'})
        # Ledges block movement in the opposite direction of their jump
        ledge_blocks = {'v': 'up', '<': 'right', '>': 'left'}

        lines = []
        first_blocked = None
        x, y = px, py
        for i in range(distance):
            x += dx
            y += dy
            if 0 <= x < grid_w and 0 <= y < grid_h:
                t = terrain[y][x]
                blocked = False
                if t in npc_chars:
                    desc = f'NPC (blocked — talk with A)'
                    blocked = True
                else:
                    desc = tile_desc.get(t, f'unknown ({t})')
                if t in ('#', '=', 'T') or t in sprite_blocked:
                    blocked = True
                elif t in ledge_blocks and ledge_blocks[t] == direction:
                    desc += f' — IMPASSABLE going {direction}'
                    blocked = True
                elif t in ledge_blocks:
                    desc += f' — passable (jump {ledge_blocks.get(t, "?")} direction)'
                marker = 'X' if blocked else 'o'
                lines.append(f"  {i+1}. [{marker}] {t} = {desc}")
                if blocked and first_blocked is None:
                    first_blocked = i + 1
            else:
                lines.append(f"  {i+1}. [?] off-screen")

        map_x = self.pyboy.memory[ADDR_PLAYER_X]
        map_y = self.pyboy.memory[ADDR_PLAYER_Y]
        header = f"Tiles {direction.upper()} from @({map_x},{map_y}) screen({px},{py}):"
        if first_blocked:
            header += f" BLOCKED at tile {first_blocked}"

        result = header + "\n" + "\n".join(lines)
        result += "\n⚠ REMINDER: check_tiles is read-only. You MUST also call send_inputs this turn."
        return [{"type": "text", "text": result}]

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