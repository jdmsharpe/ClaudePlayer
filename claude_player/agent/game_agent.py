import json
import os
import sys
import logging
import time
import signal
import threading
from collections import Counter, deque
import re
from datetime import datetime
from pyboy import PyBoy

from claude_player.config.config_class import ConfigClass
from claude_player.config.config_loader import setup_logging
from claude_player.state.game_state import GameState
from claude_player.tools.tool_setup import setup_tool_registry
from claude_player.interface.claude_interface import ClaudeInterface
from claude_player.utils.message_utils import MessageUtils
from claude_player.utils.game_utils import take_screenshot
from claude_player.utils.spatial_context import extract_spatial_context
from claude_player.utils.world_map import WorldMap
from claude_player.utils.battle_context import extract_battle_context
from claude_player.utils.party_context import extract_party_context
from claude_player.utils.bag_context import extract_bag_context
from claude_player.utils.menu_context import extract_menu_context
from claude_player.utils.text_context import extract_text_context
from claude_player.utils.terminal_display import TerminalDisplay
from claude_player.utils.sound_output import SoundOutput
from claude_player.utils.ram_constants import (
    ADDR_IS_IN_BATTLE, ADDR_CUR_MAP,
    ADDR_PLAYER_Y, ADDR_PLAYER_X,
    ADDR_PLAYER_NAME, ADDR_PLAYER_ID,
    ADDR_POKEDEX_OWNED, ADDR_POKEDEX_SEEN,
)
from claude_player.utils.cost_tracker import CostTracker
from claude_player.agent.turn_context import TurnContextBuilder
from claude_player.agent.memory_manager import MemoryManager
from claude_player.agent.goal_deriver import derive_tactical_goal, derive_nav_tactical_goal

from claude_player.data.pokemon import G1_CHARS

# Error strings that indicate non-recoverable failures (no point retrying)
FATAL_ERROR_PATTERNS = [
    "authentication_error",
    "invalid x-api-key",
    "invalid_api_key",
    "Streaming is required",
    "permission_error",
]

def _is_fatal_error(error: Exception) -> bool:
    """Check if an error is non-recoverable and should stop the game loop."""
    error_str = str(error)
    return any(pattern in error_str for pattern in FATAL_ERROR_PATTERNS)

class GameAgent:
    """Main game agent class that orchestrates the AI gameplay."""
    
    def __init__(self, config: ConfigClass):
        """Initialize the game agent with a configuration object."""
        self.config = config
        setup_logging(self.config)
        
        # Check if ROM file exists
        if not os.path.exists(self.config.ROM_PATH):
            error_msg = f"ERROR: ROM file not found: {self.config.ROM_PATH}"
            logging.critical(error_msg)
            logging.critical("Please check your configuration and ensure the ROM file exists.")
            logging.critical(f"If you're using a custom configuration file, verify the 'ROM_PATH' setting.")
            sys.exit(1)
        
        # Initialize game components
        pyboy_kwargs = {}

        # Apply GBC color palette (requires CGB mode for DMG games)
        # "game" = enable CGB so the ROM's own per-map palettes take effect
        # preset name = static palette override  |  None = DMG grayscale
        from claude_player.config.gbc_palettes import resolve_palette
        palette_cfg = getattr(self.config, 'GBC_COLOR_PALETTE', None)
        if palette_cfg and str(palette_cfg).lower() == 'game':
            pyboy_kwargs['cgb'] = True
        else:
            palette = resolve_palette(palette_cfg)
            if palette is not None:
                pyboy_kwargs['cgb'] = True
                pyboy_kwargs['cgb_color_palette'] = palette

        self.pyboy = PyBoy(self.config.ROM_PATH, **pyboy_kwargs)
        self.pyboy.set_emulation_speed(target_speed=self.config.EMULATION_SPEED)

        # Sound output — buffers PyBoy audio frames into WAV chunks for browser streaming
        # When disabled, we also pass sound=False to tick() so PyBoy skips APU
        # sampling entirely.
        self._sound_enabled = getattr(self.config, 'ENABLE_SOUND', True)
        self.sound_output = SoundOutput(
            sample_rate=self.pyboy.sound.sample_rate,
            enabled=self._sound_enabled,
        )
        
        # Load saved state if available
        if self.config.STATE_PATH:
            if not os.path.exists(self.config.STATE_PATH):
                logging.warning(f"Saved state file not found: {self.config.STATE_PATH}")
            else:
                with open(self.config.STATE_PATH, "rb") as file:
                    self.pyboy.load_state(file)
                logging.info(f"Loaded explicit save state: {self.config.STATE_PATH}")
        else:
            # Fall back to autosave if it exists
            autosave = os.path.join(os.path.dirname(self.config.ROM_PATH), "saves", "autosave.state")
            if os.path.exists(autosave):
                with open(autosave, "rb") as file:
                    self.pyboy.load_state(file)
                logging.info(f"Loaded autosave: {autosave}")
        
        # Store previous tilemap and player position for spatial context
        self._previous_visible_tilemap = None
        self._previous_player_pos = None
        self._visited_positions: deque = deque(maxlen=150)

        # Track consecutive thinking-only responses for recovery
        self._consecutive_thinking_only = 0

        # Track consecutive turns at the same position for stuck detection
        self._stuck_count = 0

        # Movement feedback from last send_inputs execution
        self._last_action_feedback = None

        # Track recent actions for loop detection
        self._action_history = []  # List of (turn, action_string) tuples
        self._max_action_history = 8

        # Direction reversal detection (overworld anti-ping-pong)
        self._last_move_direction: str | None = None  # Last directional token (U/D/L/R)
        self._consecutive_reversals = 0  # Count of back-to-back direction reversals

        # Track which directions were blocked at current position
        self._blocked_directions: set[str] = set()  # e.g. {"U", "R", "L"}
        self._blocked_at_pos: tuple[int, int] | None = None  # position where blocks were recorded

        # Dead-end memory is stored directly in self._world_map.dead_ends so it
        # persists across sessions (serialized alongside tiles/warps in world_map.json).
        self._current_map_id: int | None = None  # tracks map changes for reset
        self._current_map_name: str | None = None  # current map name (snapshot before each update)
        self._last_map_name: str | None = None  # name of previous map (shown after warp for orientation)

        # Current context mode — drives which system prompt block to include
        self._in_battle = False

        # Battle-specific stuck detection: track battle state between turns
        self._battle_stuck_count = 0
        self._last_battle_snapshot = None  # (player_hp, enemy_hp, menu_type, cursor)

        # Per-turn token/cost stash for TURN_SUMMARY logging
        self._last_turn_tokens = 0
        self._last_turn_cost = 0.0

        # Periodic aggregate stats (emitted every _STATS_INTERVAL turns)
        self._stats_interval = 25
        self._stats_blocked = 0  # UNCHANGED outcomes since last stats
        self._stats_moved = 0    # successful moves since last stats
        self._stats_cost = 0.0   # cost since last stats
        self._stats_thinking_only = 0  # THINKING-ONLY burns since last stats
        self._stats_no_action = 0      # NO-ACTION turns since last stats
        self._stats_last_turn = 0      # turn number at last stats emission

        # Cumulative token/cost tracking (deferred until after _save_dir is set)

        # Injection-policy state is managed by TurnContextBuilder (initialized below)

        # Periodic emulator state saving
        self._last_save_turn = 0
        self._save_interval = 100  # Save every N turns

        # World map saves more frequently (JSON only — no emulator state risk)
        self._last_world_map_save_turn = 0
        self._world_map_save_interval = 20
        self._save_dir = os.path.join(os.path.dirname(self.config.ROM_PATH), "saves")
        self._save_path = os.path.join(self._save_dir, "autosave.state")
        self._visited_maps_path = os.path.join(self._save_dir, "visited_maps.json")
        self.cost_tracker = CostTracker(
            stats_path=os.path.join(self._save_dir, "session_stats.json"),
        )

        # Persistent world map: accumulates explored tiles across turns
        self._world_map = WorldMap()
        self._world_map_path = os.path.join(self._save_dir, "world_map.json")
        self._world_map.load(self._world_map_path)
        
        # Initialize game state
        self.game_state = GameState()
        self.game_state.cartridge_title = self.pyboy.cartridge_title
        # Auto-set game identity from cartridge title so Claude doesn't waste
        # a tool call on set_game (and the log doesn't say "Not identified")
        if not self.game_state.identified_game and self.pyboy.cartridge_title:
            self.game_state.identified_game = self.pyboy.cartridge_title
        self.game_state.runtime_thinking_enabled = self.config.ACTION.get("THINKING", True)
        self._load_visited_maps()

        # Initialize tool registry
        self.tool_registry = setup_tool_registry(self.pyboy, self.game_state, self.config)
        
        # Initialize Claude interface
        self.claude = ClaudeInterface(self.config)

        # Initialize Knowledge Base (categorized persistent memory)
        from claude_player.agent.knowledge_base import KnowledgeBase
        self._knowledge_base = KnowledgeBase(self._save_dir)

        # Migrate old MEMORY.md to KB if it exists
        old_memory_path = os.path.join(self._save_dir, "MEMORY.md")
        if os.path.exists(old_memory_path):
            self._knowledge_base.migrate_from_memory_md(old_memory_path)

        # Initialize memory manager (background subagent for KB updates)
        self.memory_manager = MemoryManager(
            self.claude, self.game_state, self.config, self._knowledge_base,
        )
        self._memory_thread = None  # Background thread for async memory updates

        # Turn context builder: assembles user_content for Claude each turn
        self._context_builder = TurnContextBuilder(
            knowledge_base=self._knowledge_base,
            party_refresh_interval=10,
            bag_refresh_interval=15,
        )

        # Initialize chat history
        self.chat_history = []

        # Initialize terminal display
        self.display = TerminalDisplay()

        # Initialize web streamer (if configured)
        self.web_streamer = None
        web_port = getattr(self.config, 'WEB_PORT', 0)
        if web_port:
            try:
                from claude_player.web.web_server import WebStreamer
                self.web_streamer = WebStreamer(self.display, port=web_port, config=self.config, sound=self.sound_output)
                self.web_streamer.start()
            except ImportError:
                logging.warning("Flask not installed — web streamer disabled (pip install flask)")
            except Exception as e:
                logging.warning(f"Failed to start web streamer: {e}")
    
    def _goal_with_progress(self) -> str:
        """Combine strategic goal with milestone progress fraction."""
        goal = self.game_state.strategic_goal or ""
        sp = self.game_state.story_progress
        if sp and sp.get("completed") is not None:
            done = len(sp["completed"])
            from claude_player.utils.event_flags import STORY_PROGRESSION
            total = len(STORY_PROGRESSION)
            return f"[{done}/{total}] {goal}" if goal else f"{done}/{total} milestones"
        return goal

    def _tactical_goal_display(self) -> str:
        """Return the current tactical goal for display, or empty string."""
        return self.game_state.tactical_goal or ""

    def _side_objectives_display(self) -> str:
        """Return pipe-separated side objectives for display, or empty string."""
        return " | ".join(self.game_state.side_objectives) if self.game_state.side_objectives else ""

    def _limit_screenshots_in_history(self):
        """
        Limit the number of screenshots in chat history to MAX_SCREENSHOTS.
        Only removes screenshots from user messages, keeping all other content intact.
        """
        # Count screenshots in the chat history
        screenshot_count = 0
        screenshot_indices = []
        
        # First pass: find all screenshots in user messages
        for i, message in enumerate(self.chat_history):
            # Only process user messages with multiple content items
            if message["role"] == "user" and isinstance(message["content"], list):
                for j, content_item in enumerate(message["content"]):
                    # Check if the item is an image
                    if isinstance(content_item, dict) and content_item.get("type") == "image":
                        screenshot_count += 1
                        # Store information about where this screenshot is
                        screenshot_indices.append((i, j))
        
        # If we have more screenshots than allowed, remove the oldest ones
        if screenshot_count > self.config.MAX_SCREENSHOTS:
            # Calculate how many to remove
            screenshots_to_remove = screenshot_count - self.config.MAX_SCREENSHOTS
            screenshots_to_keep = screenshot_indices[screenshots_to_remove:]
            
            # Create a set of positions to keep for O(1) lookup
            positions_to_keep = set((i, j) for i, j in screenshots_to_keep)
            
            # Second pass: create new history without the oldest screenshots
            for i, message in enumerate(self.chat_history):
                if message["role"] == "user" and isinstance(message["content"], list):
                    # Filter the content to keep only non-screenshots or screenshots we want to keep
                    new_content = []
                    for j, content_item in enumerate(message["content"]):
                        # Keep non-image content or screenshots we want to keep
                        if not (isinstance(content_item, dict) and content_item.get("type") == "image") or (i, j) in positions_to_keep:
                            new_content.append(content_item)
                    
                    # Update the message content
                    message["content"] = new_content
            
            # Remove user messages that ended up with empty content
            self.chat_history = [
                msg for msg in self.chat_history
                if not (msg["role"] == "user" and isinstance(msg["content"], list) and len(msg["content"]) == 0)
            ]

            # Log the screenshot reduction
            logging.info(f"Reduced screenshots in chat history from {screenshot_count} to {self.config.MAX_SCREENSHOTS}")

    def _sanitize_chat_history(self):
        """Remove invalid leading messages from chat history.

        Drops orphaned tool_result messages (whose matching tool_use was
        truncated away) and leading assistant messages (which need a
        preceding user message). Called after truncation and as error recovery.
        """
        removed = 0
        while self.chat_history:
            first = self.chat_history[0]
            # Drop assistant messages at the start (no preceding user context)
            if first["role"] == "assistant":
                self.chat_history.pop(0)
                removed += 1
                continue
            # Drop user messages that contain orphaned tool_result blocks
            if first["role"] == "user" and isinstance(first["content"], list):
                has_tool_result = any(
                    isinstance(item, dict) and item.get("type") == "tool_result"
                    for item in first["content"]
                )
                if has_tool_result:
                    self.chat_history.pop(0)
                    removed += 1
                    continue
            break
        if removed:
            logging.info(f"Sanitized chat history: removed {removed} invalid leading message(s)")

    def capture_pyboy_state(self):
        """Capture PyBoy screen data on the main thread (thread-safe).

        Returns a dict with screenshot and optional spatial context data,
        ready to be passed to prepare_turn_state on the AI thread.
        """
        # Periodic autosave (main thread only, skips during battles)
        self._maybe_save_state()
        self._maybe_save_world_map()

        screenshot = take_screenshot(self.pyboy, True)
        # Feed raw frame to display for web streaming
        self.display.set_frame(self.pyboy.screen.image)
        spatial_data = None
        if self.config.ENABLE_SPATIAL_CONTEXT:
            spatial_data = extract_spatial_context(
                self.pyboy,
                self._previous_visible_tilemap,
                previous_player_pos=self._previous_player_pos,
                visited_maps=self.game_state.visited_maps,
            )
            # Stuck detection: only count when in overworld AND screen is static.
            # Some scripted dialogue (e.g. "Wild POKEMON live in tall grass!")
            # doesn't set wStatusFlags5 bit 0, so RAM-based game state misses it.
            # Comparing tilemaps catches these: if the screen changed, something
            # is happening (text box, animation, cutscene) and the player isn't stuck.
            current_tilemap = spatial_data.get("visible_tilemap")
            old_tilemap = self._previous_visible_tilemap
            self._previous_visible_tilemap = current_tilemap

            screen_changed = False
            if current_tilemap and old_tilemap:
                changes = sum(
                    1 for y in range(min(len(current_tilemap), len(old_tilemap)))
                    for x in range(min(len(current_tilemap[y]), len(old_tilemap[y])))
                    if current_tilemap[y][x] != old_tilemap[y][x]
                )
                screen_changed = changes > 8  # text scroll = 20+ tiles, idle anim = ~2-4

            current_pos = spatial_data.get("player_pos")
            detected_state = spatial_data.get("game_state")
            in_overworld = detected_state and detected_state.get("state") == "overworld"
            if not in_overworld or screen_changed:
                self._stuck_count = 0
            elif self._previous_player_pos is not None and current_pos == self._previous_player_pos:
                self._stuck_count += 1
                if self._stuck_count >= 3:
                    self._world_map.discard_pending_route()
            elif current_pos is not None:
                self._stuck_count = 0
            self._previous_player_pos = current_pos
            # Track visited positions for exploration analysis
            new_map_id = spatial_data.get("map_number")
            map_changed = False
            prev_map_id = self._current_map_id  # for graph 0xFF resolution
            if current_pos is not None:
                # Detect map change: map_id changed (position-jump heuristic
                # removed — large caves like Mt. Moon B1F are 28x28 and
                # routine movement easily exceeds 10 tiles, causing false
                # warp transitions and spurious cycling detection)
                map_changed = (
                    new_map_id is not None and new_map_id != self._current_map_id
                )
                if map_changed:
                    # Confirm pending route cache on successful warp
                    if prev_map_id is not None:
                        self._world_map.confirm_route(prev_map_id)
                    # Record warp transition for ping-pong detection
                    if (prev_map_id is not None
                            and new_map_id is not None
                            and self._visited_positions):
                        last_pos_on_old_map = self._visited_positions[-1]
                        self._world_map.record_warp_transition(
                            prev_map_id, last_pos_on_old_map,
                            new_map_id, self.game_state.turn_count,
                            arrival_pos=current_pos,
                        )
                    self._visited_positions.clear()
                    # Evict stale tactical goal and resume auto-derivation for new map
                    self.game_state._tactical_goal_override = False
                    self.game_state.tactical_goal = None
                    # Snapshot the name of the map we just left for orientation context
                    if self._current_map_name:
                        self._last_map_name = self._current_map_name
                # Keep current map name in sync (spatial_data reflects latest RAM read)
                self._current_map_name = spatial_data.get("map_name")
                if new_map_id is not None:
                    self._current_map_id = new_map_id
                self._visited_positions.append(current_pos)

            # Accumulate tiles into persistent world map.
            # Skip the first turn after a map transition — the base_grid may
            # still show warp animation / black-screen tiles that would corrupt
            # the new map's tile record.  Stable tiles arrive the next turn.
            if (current_pos is not None
                    and in_overworld
                    and spatial_data.get("map_number") is not None):
                if map_changed:
                    # On map-change turns: record graph edges only (warp/connection
                    # data is from RAM, not the screen grid which may be mid-transition).
                    self._world_map.update_graph(
                        spatial_data["map_number"],
                        spatial_data.get("warp_data_raw"),
                        last_map_id=prev_map_id,
                    )
                elif (spatial_data.get("base_grid")
                      and spatial_data.get("player_screen_pos")):
                    # Normal turns: full tile + warp + graph update.
                    self._world_map.update(
                        map_id=spatial_data["map_number"],
                        player_pos=current_pos,
                        player_screen_pos=spatial_data["player_screen_pos"],
                        grid=spatial_data["base_grid"],
                        warp_data=spatial_data.get("warp_data_raw"),
                        last_map_id=prev_map_id,
                        pair_blocked=spatial_data.get("pair_blocked"),
                    )

            # ── Unified navigation hint (single message, priority-based) ──
            # Instead of layering multiple independent warnings (CYCLING,
            # LOOPING, THRASHING, DEAD END, EXPLORATION) that can
            # contradict each other, compute ONE coherent directive.
            if (len(self._visited_positions) >= 10
                    and current_pos is not None
                    and in_overworld
                    and spatial_data.get("text")):
                cx, cy = current_pos
                pos_counts = Counter(self._visited_positions)
                most_visited_pos, most_visited_count = pos_counts.most_common(1)[0]
                unique_tiles = len(pos_counts)
                xs = [p[0] for p in self._visited_positions]
                ys = [p[1] for p in self._visited_positions]
                x_range = max(xs) - min(xs)
                y_range = max(ys) - min(ys)

                _sc = getattr(self.config, "STUCK", {})
                _cycling_min = _sc.get("CYCLING_MIN_VISITS", 4)
                _small_x     = _sc.get("SMALL_AREA_X", 6)
                _small_y     = _sc.get("SMALL_AREA_Y", 6)
                _thrash_x    = _sc.get("THRASH_X", 8)
                _thrash_y    = _sc.get("THRASH_Y", 3)

                is_cycling = (len(self._visited_positions) >= 8
                              and most_visited_count >= _cycling_min)
                is_small_area = (len(self._visited_positions) >= 15
                                 and x_range <= _small_x and y_range <= _small_y)
                is_thrashing = (len(self._visited_positions) >= 15
                                and x_range > _thrash_x and y_range <= _thrash_y)

                # Record dead-end zone when cycling detected (per-map)
                _map_key = self._current_map_id if self._current_map_id is not None else -1
                map_dead_ends = self._world_map.dead_ends.setdefault(_map_key, [])
                if is_cycling:
                    # Don't mark areas near warps as dead ends — the agent
                    # often cycles near buildings while pathfinding, and marking
                    # those tiles as dead ends blocks the only viable routes.
                    _warp_positions = self._world_map.warps.get(_map_key, {})
                    near_warp = any(
                        abs(cx - wx) + abs(cy - wy) <= 4
                        for (wx, wy) in _warp_positions
                    )
                    if not near_warp and not any(
                        abs(cx - dz[0]) + abs(cy - dz[1]) <= 6
                        for dz in map_dead_ends
                    ):
                        # Cap at 4 dead-end zones per map to prevent over-coverage
                        _MAX_DEAD_ENDS_PER_MAP = 4
                        if len(map_dead_ends) >= _MAX_DEAD_ENDS_PER_MAP:
                            map_dead_ends.pop(0)  # evict oldest
                        map_dead_ends.append((cx, cy))
                        logging.info(f"DEAD-END ZONE recorded at ({cx},{cy})")

                # Compute avoid/suggest directions from dead-end zones (current map only)
                avoid_dirs: list[str] = []
                at_dead_end = False
                for dz_x, dz_y in map_dead_ends:
                    dist = abs(cx - dz_x) + abs(cy - dz_y)
                    if dist <= 3:
                        at_dead_end = True
                    elif dist <= 8:
                        dx_dz, dy_dz = dz_x - cx, dz_y - cy
                        if dy_dz < -2 and "NORTH" not in avoid_dirs:
                            avoid_dirs.append("NORTH")
                        elif dy_dz > 2 and "SOUTH" not in avoid_dirs:
                            avoid_dirs.append("SOUTH")
                        if dx_dz < -2 and "WEST" not in avoid_dirs:
                            avoid_dirs.append("WEST")
                        elif dx_dz > 2 and "EAST" not in avoid_dirs:
                            avoid_dirs.append("EAST")

                # Suggest directions AWAY from movement centroid (unexplored territory)
                # dy_c > 0 means centroid is SOUTH of player → go NORTH (away)
                # dx_c > 0 means centroid is EAST of player → go WEST (away)
                avg_x = sum(xs) / len(xs)
                avg_y = sum(ys) / len(ys)
                dx_c, dy_c = avg_x - cx, avg_y - cy
                suggest_dirs: list[str] = []
                if dy_c > 1.0:
                    suggest_dirs.append("NORTH")
                elif dy_c < -1.0:
                    suggest_dirs.append("SOUTH")
                if dx_c > 1.0:
                    suggest_dirs.append("WEST")
                elif dx_c < -1.0:
                    suggest_dirs.append("EAST")
                # Remove suggestions that lead toward dead ends
                suggest_dirs = [d for d in suggest_dirs if d not in avoid_dirs]

                # Build ONE navigation hint based on priority
                nav_hint = ""

                if is_cycling and at_dead_end:
                    # P1: Cycling at a known dead end — strongest warning
                    rec = (f" Try {' or '.join(suggest_dirs)}."
                           if suggest_dirs else
                           " Try any direction you haven't attempted yet.")
                    nav_hint = (
                        f"STUCK: Looping at dead-end ({cx},{cy}) —"
                        f" tile visited {most_visited_count}x."
                        f" LEAVE this area.{rec}"
                    )
                elif is_cycling:
                    # P2: Cycling but not at a recorded dead end
                    rec = (f" Try {' or '.join(suggest_dirs)}."
                           if suggest_dirs else "")
                    avoid = (f" Avoid {'/'.join(avoid_dirs)} (dead end)."
                             if avoid_dirs else "")
                    nav_hint = (
                        f"STUCK: Tile ({most_visited_pos[0]},{most_visited_pos[1]})"
                        f" visited {most_visited_count}x in {len(self._visited_positions)}"
                        f" turns. You are looping.{avoid}{rec}"
                    )
                elif is_small_area or is_thrashing:
                    # P3: Stuck in a small area or thrashing laterally
                    label = (f"Bouncing {x_range} tiles east-west without"
                             f" vertical progress" if is_thrashing else
                             f"Confined to {x_range+1}x{y_range+1} tile area"
                             f" for {len(self._visited_positions)} turns")
                    avoid = (f" Avoid {'/'.join(avoid_dirs)} (dead end)."
                             if avoid_dirs else "")
                    rec = (f" Try {' or '.join(suggest_dirs)}."
                           if suggest_dirs else
                           " Pick ONE new direction and commit to it.")
                    nav_hint = f"STUCK: {label}.{avoid}{rec}"
                elif at_dead_end:
                    # P4: At dead end but not cycling yet — early warning
                    rec = (f" Try {' or '.join(suggest_dirs)}."
                           if suggest_dirs else
                           " Move in a direction you haven't tried.")
                    nav_hint = (
                        f"WARNING: Near a known dead-end zone ({cx},{cy})."
                        f" Leave before you get stuck.{rec}"
                    )
                elif avoid_dirs:
                    # P5: Near a dead end (not at it, not cycling)
                    nav_hint = (
                        f"NOTE: Avoid going {'/'.join(avoid_dirs)}"
                        f" — leads to a dead end you already explored."
                    )
                elif suggest_dirs and "[path:" not in spatial_data["text"]:
                    # P6: Mild exploration hint (only when no A* path)
                    nav_hint = (
                        f"EXPLORE: Recent movement clustered"
                        f" — try {' or '.join(suggest_dirs)} for new areas."
                    )

                if nav_hint:
                    spatial_data["text"] += f"\n{nav_hint}"

                # Exploration frontier hint from persistent world map
                map_id = spatial_data.get("map_number")
                if map_id is not None and current_pos is not None:
                    frontier_hint = self._world_map.frontier_dirs(
                        map_id, current_pos
                    )
                    if frontier_hint:
                        spatial_data["text"] += f"\n{frontier_hint}"

            # Auto-set strategic goal from event flags
            story_progress = spatial_data.get("story_progress")
            if story_progress:
                self.game_state.story_progress = story_progress
                if self.game_state.auto_goal_enabled and story_progress.get("next_goal"):
                    new_strategic = story_progress["next_goal"]
                    if self.game_state.strategic_goal != new_strategic:
                        logging.info(f"AUTO-STRATEGIC-GOAL: {new_strategic}")
                        self.game_state.strategic_goal = new_strategic

                # Derive tactical goal from MAP_HINTS (unless agent-overridden)
                if not self.game_state._tactical_goal_override:
                    next_milestone = story_progress.get("next")
                    next_flag = next_milestone[0] if next_milestone else None
                    new_map_id = spatial_data.get("map_number")
                    old_tactical = self.game_state.tactical_goal
                    self.game_state.tactical_goal = derive_tactical_goal(
                        next_flag, new_map_id,
                    )
                    # Fallback: BFS routing from map graph
                    if self.game_state.tactical_goal is None and new_map_id is not None:
                        self.game_state.tactical_goal = derive_nav_tactical_goal(
                            self._world_map, new_map_id,
                            self.game_state.strategic_goal,
                        )
                    if self.game_state.tactical_goal != old_tactical:
                        logging.info(f"AUTO-TACTICAL-GOAL: {self.game_state.tactical_goal}")
            if spatial_data.get("text"):
                logging.debug(f"Spatial context:\n{spatial_data['text']}")

        # Extract battle context when in battle (replaces spatial grid)
        battle_data = None
        if self.config.ENABLE_SPATIAL_CONTEXT and spatial_data:
            if (spatial_data.get("game_state") or {}).get("state") == "battle":
                # self._in_battle still holds the PREVIOUS turn's value here
                # (updated at line ~511 after capture). So not self._in_battle
                # correctly identifies "just entered battle this turn".
                battle_data = extract_battle_context(
                    self.pyboy,
                    just_entered_battle=not self._in_battle,
                )

        # Extract party context (always available — overworld and battle)
        party_data = None
        if self.config.ENABLE_SPATIAL_CONTEXT:
            party_data = extract_party_context(self.pyboy)
            # Store latest party status from RAM
            if party_data and party_data.get("party"):
                health = party_data.get("health", {})
                names = ", ".join(
                    f"{m['name']} Lv{m['level']} HP:{m['hp']}/{m['max_hp']}"
                    for m in party_data["party"]
                )
                self.game_state.party_summary = (
                    f"{names} — {health.get('total_hp_pct', '?')}% HP"
                )

        # Extract bag/inventory context
        bag_data = None
        if self.config.ENABLE_SPATIAL_CONTEXT:
            bag_data = extract_bag_context(self.pyboy)

        # Extract overworld menu context (dialogue/menu state, not battle)
        menu_data = None
        if self.config.ENABLE_SPATIAL_CONTEXT and spatial_data:
            gs = (spatial_data.get("game_state") or {}).get("state")
            if gs == "dialogue" and not battle_data:
                menu_data = extract_menu_context(
                    self.pyboy, party_data=party_data, bag_data=bag_data,
                )

        # Extract on-screen text (dialogue, signs, item pickups) from wTileMap
        text_data = extract_text_context(self.pyboy)

        return {
            "screenshot": screenshot,
            "spatial_data": spatial_data,
            "battle_data": battle_data,
            "party_data": party_data,
            "bag_data": bag_data,
            "menu_data": menu_data,
            "text_data": text_data,
            "cartridge_title": self.pyboy.cartridge_title,
        }

    def prepare_turn_state(self, captured_state):
        """Prepare the game state for a new analysis turn.

        Args:
            captured_state: Dict from capture_pyboy_state() with pre-captured
                           PyBoy data (thread-safe — no PyBoy access here).
        """
        # Increment turn counter
        self.game_state.increment_turn()

        # Extract display text for terminal
        spatial_grid = ""
        location = ""
        spatial_data = captured_state.get("spatial_data")
        battle_data = captured_state.get("battle_data")

        menu_data = captured_state.get("menu_data")

        self._was_in_battle = self._in_battle
        self._in_battle = bool(battle_data and battle_data.get("text"))

        # Log the turn (after battle state is known, with map context)
        current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        logging.info(f"======= NEW TURN: {current_time_str} =======")
        self.game_state.log_state(
            map_id=spatial_data.get("map_number") if spatial_data else self._current_map_id,
            map_name=spatial_data.get("map_name") if spatial_data else self._current_map_name,
            player_pos=spatial_data.get("player_pos") if spatial_data else None,
            in_battle=self._in_battle,
        )

        # fight_cursor is now read from wPlayerMoveListIndex RAM in extract_battle_context.
        # No client-side tracking needed — the TIP uses U U U reset so cursor position
        # doesn't matter for navigation. Reset on battle end for legacy display only.
        if self._was_in_battle and not self._in_battle:
            self.game_state.fight_cursor = 0

        # Battle stuck detection: track HP/menu between turns
        if self._in_battle and battle_data:
            snap = (
                battle_data.get("player", {}).get("hp"),
                battle_data.get("enemy", {}).get("hp"),
                battle_data.get("menu_type"),
                battle_data.get("cursor"),
            )
            if snap == self._last_battle_snapshot:
                self._battle_stuck_count += 1
            else:
                self._battle_stuck_count = 0
            self._last_battle_snapshot = snap
        elif not self._in_battle:
            self._battle_stuck_count = 0
            self._last_battle_snapshot = None

        if self._in_battle:
            # In battle: show battle context in terminal
            spatial_grid = battle_data["text"]
        elif spatial_data and spatial_data["text"]:
            # Overworld: extract grid lines for terminal
            grid_lines = []
            map_name_line = ""
            map_pos_line = ""
            region_line = ""
            for line in spatial_data["text"].split("\n"):
                stripped = line.lstrip()
                # Column header (digits row) or grid row (starts with digit)
                if stripped and (stripped[0].isdigit() or stripped.startswith(".")):
                    grid_lines.append(line)
                elif line.startswith("Map:"):
                    map_name_line = line
                elif line.startswith("Map position:"):
                    map_pos_line = line
                elif line.startswith("Region:"):
                    region_line = line
            spatial_grid = "\n".join(grid_lines)
            # Compose compact location string for web dashboard
            location_parts = []
            if map_name_line:
                # "Map: Viridian City (size=34x48)" → "Viridian City"
                name_part = map_name_line[len("Map:"):].split("(")[0].strip()
                location_parts.append(name_part)
            if map_pos_line:
                # "Map position: (17, 19) of 34x48" → keep as-is
                location_parts.append(map_pos_line[len("Map position:"):].strip())
            location = " — ".join(location_parts)
            if region_line:
                # "Region: South Forest — hint..." → "South Forest"
                region_name = region_line[len("Region:"):].split("—")[0].strip()
                location += f"\n{region_name}"
            if location:
                spatial_grid = location + "\n" + spatial_grid

        # Build compact one-line summaries for terminal display
        party_data = captured_state.get("party_data")
        bag_data = captured_state.get("bag_data")

        party_summary = ""
        if party_data and party_data.get("party"):
            mons = []
            for m in party_data["party"]:
                status = f" [{m['status']}]" if m["status"] != "OK" else ""
                nick = m.get("nickname", "")
                display = f"{m['name']} ({nick})" if nick else m["name"]
                mons.append(f"{display} Lv{m['level']} {m['hp']}/{m['max_hp']}{status}")
            health = party_data.get("health", {})
            team = f"HP:{health.get('total_hp_pct', '?')}%"
            if health.get("recommendation"):
                team += f" — {health['recommendation']}"
            party_summary = " | ".join(mons) + f" [{team}]"

        party_mons_list = []
        if party_data and party_data.get("party"):
            party_mons_list = [
                {"name": m["name"], "nickname": m.get("nickname", ""),
                 "level": m["level"], "hp": m["hp"],
                 "max_hp": m["max_hp"], "types": m.get("types", []),
                 "status": m.get("status", "OK"),
                 "exp": m.get("exp", 0)}
                for m in party_data["party"]
            ]

        bag_summary = ""
        if bag_data and bag_data.get("assessment"):
            a = bag_data["assessment"]
            parts = [
                f"{a['badge_count']} badges",
                f"${a['money']}",
            ]
            if a["pokeballs"]:
                parts.append(f"Balls:{a['pokeballs']}")
            if a["healing_items"]:
                parts.append(f"Medicine:{a['healing_items']}")
            if a["key_items"]:
                parts.append(f"Key: {', '.join(a['key_items'][:3])}")
            bag_summary = " | ".join(parts)

        bag_items_list = []
        if bag_data and bag_data.get("items"):
            bag_items_list = [
                {"name": it["name"], "qty": it["quantity"], "cat": it["category"]}
                for it in bag_data["items"]
            ]

        menu_summary = ""
        if menu_data and menu_data.get("menu_type"):
            mt = menu_data
            menu_summary = (
                f"{mt['menu_type'].replace('_', ' ').title()} "
                f"[cursor:{mt['cursor']}/{mt.get('max_item', 0)}]"
            )

        # Read Pokédex caught/seen counts from RAM (19-byte bitfields, 1 bit per species)
        dex_caught = sum(bin(self.pyboy.memory[ADDR_POKEDEX_OWNED + i]).count("1") for i in range(19))
        dex_seen = sum(bin(self.pyboy.memory[ADDR_POKEDEX_SEEN + i]).count("1") for i in range(19))

        # Read trainer name from RAM (11 bytes, Gen 1 charset, 0x50=terminator)
        raw = []
        for i in range(11):
            b = self.pyboy.memory[ADDR_PLAYER_NAME + i]
            if b == 0x50:
                break
            raw.append(b)
        trainer_name = "".join(G1_CHARS.get(b, "") for b in raw).strip() or ""

        # Trainer ID (2 bytes big-endian)
        trainer_id = (self.pyboy.memory[ADDR_PLAYER_ID] << 8) | self.pyboy.memory[ADDR_PLAYER_ID + 1]

        # Play time (wPlayTimeHours = 0xDA40 word, wPlayTimeMinutes = 0xDA42 byte)
        pt_hours = (self.pyboy.memory[0xDA40] << 8) | self.pyboy.memory[0xDA41]
        pt_mins = self.pyboy.memory[0xDA42]
        play_time = f"{pt_hours}:{pt_mins:02d}"

        # Badges list from bag data (already read above)
        badges_list = bag_data.get("badges", []) if bag_data else []

        # Render world map for display (overworld only, hidden during battle)
        world_map_text = ""
        if not self._in_battle and spatial_data:
            map_id = spatial_data.get("map_number")
            player_pos = spatial_data.get("player_pos")
            if map_id is not None and player_pos is not None:
                from claude_player.utils.world_map import _MAX_DISPLAY_SIZE
                world_map_text = self._world_map.render(
                    map_id, player_pos,
                    dead_end_zones=self._world_map.dead_ends.get(map_id, []),
                    max_size=_MAX_DISPLAY_SIZE,
                ) or ""

        # Update terminal display
        self.display.update(
            turn=self.game_state.turn_count,
            status="Analyzing...",
            game=self.game_state.identified_game or self.game_state.cartridge_title or "",
            goal=self._goal_with_progress(),
            tactical_goal=self._tactical_goal_display(),
            side_objectives=self._side_objectives_display(),
            spatial_grid=spatial_grid,
            party_summary=party_summary,
            party_mons=party_mons_list,
            bag_summary=bag_summary,
            bag_items=bag_items_list,
            menu_summary=menu_summary,
            world_map_text=world_map_text,
            dex_caught=dex_caught,
            dex_seen=dex_seen,
            trainer_name=trainer_name,
            trainer_id=trainer_id,
            play_time=play_time,
            badges=badges_list,
            session_cost=self.cost_tracker.cost_usd,
        )

        # Build user content via TurnContextBuilder
        user_content = self._context_builder.build(
            captured_state,
            game_state=self.game_state,
            world_map=self._world_map,
            last_action_feedback=self._last_action_feedback,
            last_map_name=self._last_map_name,
            in_battle=self._in_battle,
            was_in_battle=self._was_in_battle,
            stuck_count=self._stuck_count,
            battle_stuck_count=self._battle_stuck_count,
            consecutive_reversals=self._consecutive_reversals,
            action_history=self._action_history,
        )
        self._last_action_feedback = None  # consumed by builder

        # Add user message to chat history
        if len(self.chat_history) == 0:
            user_message = {"role": "user", "content": user_content}
            self.chat_history.append(user_message)
            self.game_state.add_to_complete_history(user_message)
        else:
            state_header = self.game_state.get_current_state_header(
                compact=self.config.ENABLE_SPATIAL_CONTEXT,
            )
            content_prefix = [{"type": "text", "text": state_header}] if state_header else []

            user_message = {"role": "user", "content": content_prefix + user_content}
            self.chat_history.append(user_message)
            self.game_state.add_to_complete_history(user_message)

        # Apply the screenshot limit
        self._limit_screenshots_in_history()

        # Background KB update (subagent): every MEMORY_INTERVAL turns
        memory_interval = self.config.MEMORY.get("MEMORY_INTERVAL", 20)
        if (self.game_state.turn_count % memory_interval == 0
                and self.game_state.turn_count > 0):
            # Run KB update on a background thread so it doesn't block the AI turn
            if self._memory_thread is not None and self._memory_thread.is_alive():
                logging.info("KB update still running from previous trigger — skipping")
            else:
                logging.info(f"Triggering async KB update at turn {self.game_state.turn_count}")
                # Snapshot the history so the background thread has its own copy
                history_snapshot = list(self.game_state.complete_message_history)
                self._memory_thread = threading.Thread(
                    target=self.memory_manager.update_memory,
                    args=(history_snapshot,),
                    kwargs={
                        "current_map_id": self._current_map_id,
                        "current_map_name": self._current_map_name,
                    },
                    daemon=True,
                )
                self._memory_thread.start()

    def get_ai_response(self):
        """Get AI response for the current game state.

        Implements recovery for thinking-only responses (no text or tool output):
        - 1st occurrence: retry with a nudge message
        - 2nd+ consecutive: retry with thinking temporarily disabled
        """
        max_retries = 2
        message_content = None

        for attempt in range(max_retries + 1):
            try:
                memory_text = self._context_builder.build_cached_kb_block(
                    self.game_state.turn_count, self.game_state.memory_turn,
                )
                system_prompt = self.claude.get_system_prompt(memory_text)

                # Get tools
                tools = self.tool_registry.get_tools()

                # Create a copy of the config.ACTION dictionary so we can modify it
                action_config = self.config.ACTION.copy()

                # Override the THINKING setting with the runtime value from GameState
                if hasattr(self.game_state, 'runtime_thinking_enabled'):
                    action_config["THINKING"] = self.config.MODEL_DEFAULTS.get("THINKING", True) and self.game_state.runtime_thinking_enabled

                # Recovery escalation: disable thinking after repeated thinking-only failures
                if attempt > 0 and self._consecutive_thinking_only >= 2:
                    action_config["THINKING"] = False
                    logging.warning(f"RECOVERY: t={self.game_state.turn_count} Temporarily disabling thinking to force output")

                # Send request to Claude
                message = self.claude.send_request(
                    action_config,
                    system_prompt,
                    self.chat_history,
                    tools
                )

                # Get assistant response and add to chat history.
                # Strip thinking blocks to prevent input token explosion —
                # each 8k thinking block stays in history and gets re-billed
                # on every subsequent turn.  Replace with empty stubs to
                # maintain the API schema (thinking blocks must be present
                # but can have empty content).
                assistant_content = []
                for block in message.content:
                    if getattr(block, 'type', None) == 'thinking':
                        # Keep signature (API validates it) but empty the
                        # thinking text to avoid re-billing 8k tokens/turn.
                        assistant_content.append({
                            "type": "thinking",
                            "thinking": "",
                            "signature": getattr(block, 'signature', ''),
                        })
                    else:
                        assistant_content.append(block)
                assistant_message = {"role": "assistant", "content": assistant_content}
                self.chat_history.append(assistant_message)
                self.game_state.add_to_complete_history(assistant_message)

                message_content = MessageUtils.print_and_extract_message_content(message)

                # Log token usage and cost
                usage = getattr(message, 'usage', None)
                if usage:
                    cache_create = getattr(usage, 'cache_creation_input_tokens', 0) or 0
                    cache_read = getattr(usage, 'cache_read_input_tokens', 0) or 0
                    input_tok = getattr(usage, 'input_tokens', 0) or 0
                    output_tok = getattr(usage, 'output_tokens', 0) or 0

                    # Accumulate totals via CostTracker
                    model = action_config.get("MODEL", "")
                    turn_cost = self.cost_tracker.record(
                        model, input_tok, output_tok, cache_read, cache_create,
                    )

                    total_input = cache_create + cache_read + input_tok
                    pct = (cache_read / total_input * 100) if total_input else 0
                    logging.info(
                        f"TOKENS: in={input_tok} out={output_tok} "
                        f"cache_read={cache_read} cache_create={cache_create} ({pct:.0f}% cached) "
                        f"| turn=${turn_cost:.4f} session=${self.cost_tracker.cost_usd:.4f}"
                    )
                    # Stash for TURN_SUMMARY (emitted at end of analysis cycle)
                    self._last_turn_tokens = input_tok + output_tok + cache_read + cache_create
                    self._last_turn_cost = turn_cost

                # Detect thinking-only responses (no text or tool output)
                if not message_content["text_blocks"] and not message_content["tool_use_blocks"]:
                    self._consecutive_thinking_only += 1
                    self._stats_thinking_only += 1
                    logging.warning(
                        f"THINKING-ONLY RESPONSE: t={self.game_state.turn_count} "
                        f"(#{self._consecutive_thinking_only}, "
                        f"attempt {attempt + 1}/{max_retries + 1}): Model produced thinking "
                        f"but no text or tool output."
                    )

                    if attempt < max_retries:
                        # Append a nudge message for the retry
                        nudge = {
                            "role": "user",
                            "content": [{"type": "text", "text":
                                "You produced thinking but no output. "
                                "Act now — send a simple action based on what you can see."
                            }]
                        }
                        self.chat_history.append(nudge)
                        self.game_state.add_to_complete_history(nudge)
                        logging.info(f"RECOVERY: t={self.game_state.turn_count} Appended nudge message, retrying...")
                        continue  # Retry
                    else:
                        logging.warning(f"RECOVERY: t={self.game_state.turn_count} Max retries reached, proceeding with empty response")
                else:
                    # Successful response — reset counter
                    self._consecutive_thinking_only = 0

                # Update terminal display with AI response and thinking
                response_text = ""
                if message_content["text_blocks"]:
                    response_text = message_content["text_blocks"][0].text
                thinking_text = ""
                if message_content["thinking_blocks"]:
                    thinking_text = message_content["thinking_blocks"][0].thinking
                self.display.update(
                    last_response=response_text,
                    last_thinking=thinking_text,
                    status="Processing...",
                )

                return message_content

            except Exception as e:
                error_msg = f"ERROR in get_ai_response: {str(e)}"
                logging.error(error_msg)

                # Self-heal corrupted history on orphaned tool_result errors
                if "tool_use_id" in str(e) and "tool_result" in str(e):
                    logging.warning("Detected orphaned tool_result in history — cleaning up")
                    self._sanitize_chat_history()

                # Re-raise the exception so the caller can handle it
                raise

        # Fallback return (all retries exhausted with thinking-only responses)
        return message_content

    @staticmethod
    def _extract_direction_tokens(inputs: str) -> list[str]:
        """Return ordered movement directions (U/D/L/R) from a compound input."""
        dirs: list[str] = []
        for token in inputs.split():
            match = re.fullmatch(r"([UDLR])(?:\d+)?", token.strip().upper())
            if match:
                dirs.append(match.group(1))
        return dirs

    def process_tool_results(self, message_content):
        """Process tool results from AI response.

        send_inputs calls are queued for execution on the main thread.
        All other tools are executed immediately.
        """
        tool_use_blocks = message_content["tool_use_blocks"]

        tool_results = []
        pending_actions = []

        for tool_use in tool_use_blocks:
            tool_name = tool_use.name
            tool_input = tool_use.input
            tool_use_id = tool_use.id

            if tool_name == "send_inputs":
                raw_inputs = tool_input["inputs"]
                queued_inputs = raw_inputs
                directions = self._extract_direction_tokens(raw_inputs)
                opposites = {'U': 'D', 'D': 'U', 'L': 'R', 'R': 'L'}

                if directions:
                    first_dir = directions[0]
                    if (self._last_move_direction
                            and first_dir == opposites.get(self._last_move_direction)):
                        self._consecutive_reversals += 1
                    else:
                        self._consecutive_reversals = 0
                    self._last_move_direction = directions[-1]
                else:
                    # Non-movement action resets reversal tracking
                    self._consecutive_reversals = 0

                # Queue for main-thread execution
                pending_actions.append(queued_inputs)
                logging.info(f"Queued input for later execution: {queued_inputs} (queue size: {len(pending_actions)})")
                self.display.update(last_action=queued_inputs)
                # Record in action history for loop detection
                self._action_history.append((self.game_state.turn_count, queued_inputs))
                if len(self._action_history) > self._max_action_history:
                    self._action_history.pop(0)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": "Input queued for execution"}]
                })
            elif tool_name == "run_from_battle":
                # run_from_battle generates a button sequence — queue it like send_inputs
                try:
                    result_content = self.tool_registry.execute_tool(tool_name, tool_input, tool_use_id)
                    result_text = result_content[0]["text"]
                    if result_text.startswith("Error:"):
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": result_content
                        })
                    else:
                        # Result text IS the button sequence
                        pending_actions.append(result_text)
                        logging.info(f"Queued run_from_battle: {result_text} (queue size: {len(pending_actions)})")
                        self.display.update(last_action=f"RUN: {result_text}")
                        self._action_history.append((self.game_state.turn_count, result_text))
                        if len(self._action_history) > self._max_action_history:
                            self._action_history.pop(0)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": [{"type": "text", "text": "Run sequence queued — will attempt to flee twice with auto-retry"}]
                        })
                except Exception as e:
                    error_msg = f"ERROR executing run_from_battle: {str(e)}"
                    logging.error(error_msg)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": [{"type": "text", "text": f"Error: {str(e)}"}]
                    })
            else:
                try:
                    tool_result_content = self.tool_registry.execute_tool(tool_name, tool_input, tool_use_id)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": tool_result_content
                    })
                except Exception as e:
                    error_msg = f"ERROR executing tool {tool_name}: {str(e)}"
                    logging.error(error_msg)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": [{"type": "text", "text": f"Error: {str(e)}"}]
                    })
        
        # Add tool results to chat history if there are any
        if tool_results:
            tool_results_message = {
                "role": "user",
                "content": tool_results
            }
            self.chat_history.append(tool_results_message)
            self.game_state.add_to_complete_history(tool_results_message)
            
            # Apply the screenshot limit after adding tool results
            # Tool results may contain screenshots (e.g., from send_inputs)
            self._limit_screenshots_in_history()
        
        # Limit chat history to max_messages (but keep complete history intact)
        if len(self.chat_history) > self.config.MAX_HISTORY_MESSAGES:
            # Find a clean user-message boundary to avoid orphaning tool_results
            candidate = self.chat_history[-self.config.MAX_HISTORY_MESSAGES:]
            # Walk forward to find the first plain user message (not a tool_result)
            trim = 0
            for msg in candidate:
                if msg["role"] == "user":
                    is_tool_result = isinstance(msg["content"], list) and any(
                        isinstance(item, dict) and item.get("type") == "tool_result"
                        for item in msg["content"]
                    )
                    if not is_tool_result:
                        break
                trim += 1
            self.chat_history = candidate[trim:]
            
        return pending_actions

    def _maybe_save_state(self):
        """Save emulator state periodically (every _save_interval turns).

        Called from the main thread during capture_pyboy_state.
        Keeps a single autosave file that gets overwritten.
        """
        turn = self.game_state.turn_count
        if turn <= 0 or turn - self._last_save_turn < self._save_interval:
            return

        # Don't save during battles (RAM state can be weird mid-battle)
        try:
            in_battle = self.pyboy.memory[ADDR_IS_IN_BATTLE]
            if in_battle != 0:
                return
        except Exception:
            return

        try:
            os.makedirs(self._save_dir, exist_ok=True)
            with open(self._save_path, "wb") as f:
                self.pyboy.save_state(f)
            self._world_map.save(self._world_map_path)
            self._save_visited_maps()
            self._last_save_turn = turn
            logging.info(f"Autosaved emulator state at turn {turn} → {self._save_path}")
            self.display.print_event(f"Autosaved at turn {turn}")
        except Exception as e:
            logging.warning(f"Failed to autosave: {e}")

    def _maybe_save_world_map(self):
        """Save world map JSON every _world_map_save_interval turns.

        Separate from emulator autosave — no battle restriction, no 100-turn wait.
        Dead-end zones are included since they live in self._world_map.dead_ends.
        """
        turn = self.game_state.turn_count
        if turn <= 0 or turn - self._last_world_map_save_turn < self._world_map_save_interval:
            return
        try:
            self._world_map.save(self._world_map_path)
            self._save_visited_maps()
            self._save_session_stats()
            self._last_world_map_save_turn = turn
        except Exception as e:
            logging.warning(f"Failed to save world map: {e}")

    def _save_state_now(self, reason: str = "manual"):
        """Unconditionally save emulator state (used on shutdown)."""
        try:
            os.makedirs(self._save_dir, exist_ok=True)
            with open(self._save_path, "wb") as f:
                self.pyboy.save_state(f)
            self._world_map.save(self._world_map_path)
            self._save_visited_maps()
            self._save_session_stats()
            turn = self.game_state.turn_count
            logging.info(f"Saved state on {reason} at turn {turn} → {self._save_path}")
            self.display.print_event(f"Saved on {reason} (turn {turn})")
        except Exception as e:
            logging.warning(f"Failed to save state on {reason}: {e}")

    def _load_visited_maps(self):
        """Load visited_maps set from JSON."""
        if not os.path.exists(self._visited_maps_path):
            return
        try:
            with open(self._visited_maps_path) as f:
                data = json.load(f)
            self.game_state.visited_maps = set(data)
            logging.info(f"Loaded {len(self.game_state.visited_maps)} visited maps from disk")
        except Exception as e:
            logging.warning(f"Failed to load visited maps: {e}")

    def _save_visited_maps(self):
        """Save visited_maps set to JSON."""
        try:
            with open(self._visited_maps_path, "w") as f:
                json.dump(sorted(self.game_state.visited_maps), f)
        except Exception as e:
            logging.warning(f"Failed to save visited maps: {e}")

    def _save_session_stats(self):
        """Save cumulative cost/token stats to JSON."""
        self.cost_tracker.save()

    def run_continuous(self):
        """Run the game agent in continuous mode where the emulator runs at 1x speed continuously."""
        logging.info("Starting continuous emulation mode")
        self.display.update(status="Continuous mode")
        
        # Set emulation speed to 1x (real-time)
        self.pyboy.set_emulation_speed(target_speed=1)
        
        # Variables to track time for AI analysis
        last_analysis_time = time.time()
        last_analysis_duration = 0
        last_action_time = 0  # When the last action finished executing
        adaptive_interval = self.config.CONTINUOUS_ANALYSIS_INTERVAL
        action_settle_seconds = self.config.CONTINUOUS_ANALYSIS_INTERVAL  # Wait after action before next screenshot
        
        ai_is_analyzing = False
        ai_thread = None
        
        # Create a flag to signal when AI has completed its analysis
        analysis_complete = False
        pending_actions = []
        
        # Error tracking variables
        self.error_count = 0
        self.last_error_time = 0
        fatal_error_msg = None

        # State-aware interrupt tracking: detect battle/map transitions mid-analysis
        interrupt_fired = False
        tracked_battle_state = self.pyboy.memory[ADDR_IS_IN_BATTLE]
        tracked_map_id = self.pyboy.memory[ADDR_CUR_MAP]
        # Minimum settle time after a map transition before re-analyzing.
        # ADDR_CUR_MAP changes mid-animation (after fade-to-black, before fade-in),
        # so 15ms after interrupt detection the game is still mid-warp.
        last_map_change_time = 0.0
        _MAP_TRANSITION_SETTLE = 1.5  # seconds
        # Minimum settle time after battle START before re-analyzing.
        # Battle intro animations ("Wild X appeared!", "Go! POKEMON!") take ~5-6s;
        # analyzing too early produces stale submenu detection and wasted turns.
        # With 6s settle + ~2-3s API latency, first inputs land at ~8-9s — well
        # past the intro.  Previous value of 4.0s caused run_from_battle to fire
        # during animations, sending D R A into the fight menu instead of RUN.
        last_battle_start_time = 0.0
        _BATTLE_START_SETTLE = 6.0  # seconds

        # Add threading lock for shared variables
        lock = threading.Lock()
        
        # Function to run AI analysis in a separate thread
        def run_analysis(captured_state):
            nonlocal analysis_complete, pending_actions, last_analysis_duration, adaptive_interval

            analysis_start_time = time.time()
            message_content = None

            try:
                # Prepare turn state using pre-captured PyBoy data (no PyBoy access here)
                self.prepare_turn_state(captured_state=captured_state)

                action_start_time = time.time()
                # Get AI response
                message_content = self.get_ai_response()
                
                # Process tools (don't execute send_inputs immediately)
                actions = self.process_tool_results(message_content)

                # Detect no-action turns (model used tools but forgot send_inputs)
                no_action = False
                if not actions and message_content and message_content.get("tool_use_blocks"):
                    if any(t.name != "send_inputs" for t in message_content["tool_use_blocks"]):
                        no_action = True
                        self._stats_no_action += 1
                        logging.warning(
                            f"NO-ACTION TURN: t={self.game_state.turn_count} "
                            f"Model used tools but didn't send_inputs — nudging"
                        )
                        nudge = {
                            "role": "user",
                            "content": [{"type": "text", "text":
                                "You used tools but didn't send any game inputs. "
                                "Always include a send_inputs call with your actions."
                            }]
                        }
                        self.chat_history.append(nudge)
                        self.game_state.add_to_complete_history(nudge)

                # Safely update shared variables
                with lock:
                    pending_actions.extend(actions)

                # Calculate how long the analysis took
                analysis_end_time = time.time()
                last_analysis_duration = analysis_end_time - analysis_start_time
                action_duration = analysis_end_time - action_start_time
                prep_duration = action_start_time - analysis_start_time

                # Use only action duration for adaptive interval
                with lock:
                    if no_action:
                        # Skip the wait — let the model retry immediately with the nudge
                        adaptive_interval = 0.5
                    else:
                        adaptive_interval = (0.7 * adaptive_interval) + (0.3 * action_duration)
                        min_interval = self.config.CONTINUOUS_ANALYSIS_INTERVAL
                        max_interval = getattr(self.config, 'MAX_ADAPTIVE_INTERVAL', 15.0)
                        adaptive_interval = max(min_interval, min(adaptive_interval, max_interval))

                # Structured TURN_SUMMARY — one grepable line per turn
                _sd = captured_state.get("spatial_data") or {}
                _map_id = _sd.get("map_number", self._current_map_id)
                _map_name = _sd.get("map_name") or self._current_map_name or "?"
                _map_str = f"0x{_map_id:02X}({_map_name})" if _map_id is not None else f"?({_map_name})"
                _pos = _sd.get("player_pos")
                _pos_str = f"({_pos[0]},{_pos[1]})" if _pos else "?"
                _hp_str = ""
                if self.game_state.party_summary:
                    _hp_match = re.search(r'(\d+)%', self.game_state.party_summary)
                    if _hp_match:
                        _hp_str = f" hp={_hp_match.group(1)}%"
                _tools_str = ""
                if message_content and message_content.get("tool_use_blocks"):
                    _tool_names = [t.name for t in message_content["tool_use_blocks"]]
                    _tools_str = f" tools={'+'.join(_tool_names)}"
                _actions_str = f" actions=\"{'; '.join(actions)}\"" if actions else ""
                _flags = ""
                if self._in_battle:
                    _flags += " battle=true"
                if no_action:
                    _flags += " NO_ACTION"
                # Gather stuck + NAV diagnostics for TURN_SUMMARY
                from claude_player.agent.nav_planner import last_nav_method as _nav_method
                _stuck_str = f" stuck={self._stuck_count}" if self._stuck_count > 0 else ""
                _nav_str = f" nav={_nav_method}" if _nav_method else ""
                logging.info(
                    f"TURN_SUMMARY: t={self.game_state.turn_count}"
                    f" map={_map_str} pos={_pos_str}{_hp_str}{_flags}"
                    f"{_stuck_str}{_nav_str}"
                    f" goal=\"{self.game_state.current_goal or 'None'}\""
                    f" cost=${self._last_turn_cost:.4f} tokens={self._last_turn_tokens}"
                    f"{_tools_str}{_actions_str}"
                    f" duration={last_analysis_duration:.1f}s"
                )

                # Accumulate periodic stats
                self._stats_cost += self._last_turn_cost
                _cur_turn = self.game_state.turn_count
                if _cur_turn - self._stats_last_turn >= self._stats_interval:
                    _total_moves = self._stats_blocked + self._stats_moved
                    _block_pct = (self._stats_blocked / _total_moves * 100) if _total_moves > 0 else 0
                    logging.info(
                        f"STATS: t={self._stats_last_turn + 1}-{_cur_turn}"
                        f" blocked={_block_pct:.0f}%({self._stats_blocked}/{_total_moves})"
                        f" cost=${self._stats_cost:.2f}"
                        f" thinking_only={self._stats_thinking_only}"
                        f" no_action={self._stats_no_action}"
                        f" session=${self.cost_tracker.cost_usd:.2f}"
                    )
                    self._stats_blocked = 0
                    self._stats_moved = 0
                    self._stats_cost = 0.0
                    self._stats_thinking_only = 0
                    self._stats_no_action = 0
                    self._stats_last_turn = _cur_turn

                logging.info(f"======= END ANALYSIS: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =======")
                timing_parts = f"total={last_analysis_duration:.2f}s (prep={prep_duration:.2f}s, action={action_duration:.2f}s)"
                logging.info(f"Analysis took {timing_parts}, adaptive interval: {adaptive_interval:.2f}s")

                self.display.update(
                    status=f"Idle ({last_analysis_duration:.1f}s)",
                    analysis_duration=last_analysis_duration,
                    game=self.game_state.identified_game or self.game_state.cartridge_title or "",
                    goal=self._goal_with_progress(),
                    tactical_goal=self._tactical_goal_display(),
                    side_objectives=self._side_objectives_display(),
                )
                
                # Reset error count after successful analysis
                with lock:
                    self.error_count = 0
                
            except Exception as e:
                error_msg = f"CRITICAL ERROR during analysis: {str(e)}"
                logging.critical(error_msg)

                # Fatal errors should stop the loop entirely
                if _is_fatal_error(e):
                    logging.critical("FATAL: Non-recoverable error, stopping game loop.")
                    with lock:
                        nonlocal fatal_error_msg
                        fatal_error_msg = str(e)
                        analysis_complete = True
                    return

                # Track error frequency
                current_time = time.time()
                with lock:
                    if current_time - self.last_error_time < 60:  # Within a minute
                        self.error_count += 1
                    else:
                        self.error_count = 1
                    self.last_error_time = current_time
                    self.display.update(status="Error (retrying)", error_count=self.error_count)
                
                    # If too many errors in a short time, increase the delay
                    if self.error_count > 3:
                        logging.warning(f"Multiple errors ({self.error_count}) detected, increasing delay between analyses")
                        time.sleep(5.0)  # More aggressive delay
                        adaptive_interval = max(adaptive_interval * 1.5, 10.0)  # Increase interval dramatically
                    else:
                        # Add delay after error to avoid rapid error loops
                        time.sleep(2.0)
                
                # Try to handle any existing tool_use blocks
                if message_content is not None and "tool_use_blocks" in message_content:
                    # Handle tool_use blocks that might exist in message_content
                    tool_use_blocks = message_content["tool_use_blocks"]
                    if tool_use_blocks:
                        tool_results = []
                        for tool_use in tool_use_blocks:
                            # Handle both dictionary and object access
                            tool_use_id = tool_use.get("id") if isinstance(tool_use, dict) else tool_use.id
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": [{"type": "text", "text": f"Error: {str(e)}"}]
                            })
                        
                        # Add tool results to chat history to maintain API conversation requirements
                        tool_results_message = {
                            "role": "user",
                            "content": tool_results
                        }
                        self.chat_history.append(tool_results_message)
                        self.game_state.add_to_complete_history(tool_results_message)
                        logging.info("Added error responses for pending tool use blocks from message_content")
                # Fall back to checking chat history if message_content is None or doesn't have tool_use_blocks
                elif len(self.chat_history) >= 2 and self.chat_history[-1]["role"] == "assistant":
                    assistant_content = self.chat_history[-1]["content"]
                    # Fix: Use proper access method for the content blocks based on their type
                    tool_use_blocks = []
                    for block in assistant_content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tool_use_blocks.append(block)
                        elif hasattr(block, "type") and block.type == "tool_use":
                            tool_use_blocks.append(block)
                    
                    if tool_use_blocks:
                        # Create error responses for each tool use
                        tool_results = []
                        for tool_use in tool_use_blocks:
                            # Handle both dictionary and object access
                            tool_use_id = tool_use.get("id") if isinstance(tool_use, dict) else tool_use.id
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": [{"type": "text", "text": f"Error: {str(e)}"}]
                            })
                        
                        # Add tool results to chat history to maintain API conversation requirements
                        tool_results_message = {
                            "role": "user",
                            "content": tool_results
                        }
                        self.chat_history.append(tool_results_message)
                        self.game_state.add_to_complete_history(tool_results_message)
                        logging.info("Added error responses for pending tool use blocks from chat history")
            
            # Mark analysis as complete
            with lock:
                analysis_complete = True
        
        # Shutdown event — signal handler sets this so Ctrl+C works even when
        # the main thread is inside PyBoy's Cython tick() which swallows
        # KeyboardInterrupt.  The event is also passed to press_and_release_buttons
        # so long button holds (e.g. D64) can be interrupted immediately.
        shutdown_event = threading.Event()
        # Separate event for mid-action battle/map transitions — aborts current
        # press_and_release_buttons immediately without triggering full shutdown.
        state_change_event = threading.Event()
        prev_handler = signal.getsignal(signal.SIGINT)

        def _shutdown_handler(signum, frame):
            shutdown_event.set()
            state_change_event.set()  # also abort any running action

        signal.signal(signal.SIGINT, _shutdown_handler)

        # FPS tracking
        fps_frame_count = 0
        fps_last_log_time = time.time()
        fps_log_interval = 60  # Log FPS every 60 seconds (only warnings at degraded FPS)
        current_fps = 0.0

        # Main continuous emulation loop
        try:
            while not shutdown_event.is_set():
                current_time = time.time()
                
                # Process any pending actions from the AI
                action = None
                with lock:
                    if pending_actions:
                        action = pending_actions.pop(0)
                
                if action:
                    logging.info(f"Executing pending action: {action} (remaining: {len(pending_actions)})")
                    # Record position before execution for movement feedback
                    _pre_y = self.pyboy.memory[ADDR_PLAYER_Y]
                    _pre_x = self.pyboy.memory[ADDR_PLAYER_X]
                    _pre_map = self.pyboy.memory[ADDR_CUR_MAP]
                    try:
                        from claude_player.utils.game_utils import press_and_release_buttons
                        frame_cb = self.display.set_frame if self.web_streamer else None
                        sound_cb = (lambda: self.sound_output.write(self.pyboy.sound)) if self._sound_enabled else None
                        # Clear state_change_event before executing so mid-action transitions can abort it
                        state_change_event.clear()
                        press_and_release_buttons(self.pyboy, action, settle_frames=0, stop_event=state_change_event, frame_callback=frame_cb, sound_callback=sound_cb)
                    except Exception as e:
                        logging.error(f"Error executing inputs '{action}': {str(e)}")
                        # Continue with next actions rather than crashing
                    # Record position after execution and store feedback
                    _post_y = self.pyboy.memory[ADDR_PLAYER_Y]
                    _post_x = self.pyboy.memory[ADDR_PLAYER_X]
                    _post_map = self.pyboy.memory[ADDR_CUR_MAP]
                    if _pre_map != _post_map:
                        self._last_action_feedback = f"Executed: {action} — map changed (warped)"
                        self._blocked_directions.clear()
                        self._blocked_at_pos = None
                    elif _pre_x == _post_x and _pre_y == _post_y:
                        # Extract directions attempted from action string
                        _dirs_tried = set(re.findall(r'[UDLR]', action.upper()))
                        _dir_names = {"U": "UP", "D": "DOWN", "L": "LEFT", "R": "RIGHT"}
                        _cur_pos = (_post_x, _post_y)
                        if self._blocked_at_pos != _cur_pos:
                            self._blocked_directions.clear()
                            self._blocked_at_pos = _cur_pos
                        self._blocked_directions.update(_dirs_tried)
                        _all_dirs = {"U", "D", "L", "R"}
                        _untried = _all_dirs - self._blocked_directions
                        _blocked_str = ",".join(sorted(_dir_names[d] for d in self._blocked_directions))
                        _untried_str = ",".join(sorted(_dir_names[d] for d in _untried)) if _untried else "NONE"
                        self._last_action_feedback = (
                            f"Executed: {action} — position UNCHANGED at ({_post_x},{_post_y}). Path was blocked.\n"
                            f"BLOCKED directions at ({_post_x},{_post_y}): {_blocked_str} | Untried: {_untried_str}"
                        )
                    else:
                        self._last_action_feedback = f"Executed: {action} — moved ({_pre_x},{_pre_y})→({_post_x},{_post_y})"
                        self._blocked_directions.clear()
                        self._blocked_at_pos = None
                    logging.info(f"OUTCOME: t={self.game_state.turn_count} {self._last_action_feedback}")
                    # Track stats for periodic aggregate
                    if _pre_x == _post_x and _pre_y == _post_y and _pre_map == _post_map:
                        self._stats_blocked += 1
                    else:
                        self._stats_moved += 1
                    # Refresh battle context after action so the web/terminal cursor
                    # matches the live game state (cursor RAM updates immediately on button press)
                    if self._in_battle:
                        try:
                            fresh = extract_battle_context(self.pyboy)
                            if fresh and fresh.get("text"):
                                self.display.update(spatial_grid=fresh["text"])
                        except Exception:
                            pass
                    last_action_time = time.time()
                
                # Check if it's time to run AI analysis and we're not already analyzing
                time_since_last_analysis = current_time - last_analysis_time
                time_since_last_action = current_time - last_action_time

                start_analysis = False
                map_settled = (current_time - last_map_change_time) >= _MAP_TRANSITION_SETTLE
                battle_settled = (current_time - last_battle_start_time) >= _BATTLE_START_SETTLE
                with lock:
                    if (not ai_is_analyzing
                            and time_since_last_analysis >= adaptive_interval
                            and time_since_last_action >= action_settle_seconds
                            and map_settled
                            and battle_settled):
                        start_analysis = True
                        ai_is_analyzing = True
                        analysis_complete = False
                
                if start_analysis:
                    # Reset interrupt flag for the new analysis cycle
                    with lock:
                        interrupt_fired = False

                    # Capture PyBoy state ON THE MAIN THREAD before spawning AI thread
                    captured_state = self.capture_pyboy_state()

                    logging.info(f"Starting analysis (adaptive interval: {adaptive_interval:.2f}s, "
                                f"time since last: {time_since_last_analysis:.2f}s)")

                    # Start AI analysis in a separate thread with pre-captured state
                    ai_thread = threading.Thread(target=run_analysis, args=(captured_state,))
                    ai_thread.daemon = True  # Make thread a daemon so it exits when main program exits
                    ai_thread.start()
                    last_analysis_time = current_time
                
                # Check if AI analysis has completed
                with lock:
                    if ai_is_analyzing and analysis_complete:
                        ai_is_analyzing = False
                        if interrupt_fired:
                            # State changed while AI was thinking — discard results, re-analyze now
                            pending_actions.clear()
                            adaptive_interval = self.config.CONTINUOUS_ANALYSIS_INTERVAL
                            last_action_time = 0     # bypass action settle check
                            logging.info(f"INTERRUPT: t={self.game_state.turn_count} analysis finished post-interrupt, scheduling re-analysis")
                            interrupt_fired = False
                    if fatal_error_msg:
                        logging.critical(f"Stopping continuous mode due to fatal error: {fatal_error_msg}")
                        self.display.print_event(f"Fatal error: {fatal_error_msg}")
                        break
                    
                # Tick the emulator regardless of AI state
                try:
                    if not self.pyboy.tick(sound=self._sound_enabled):
                        # PyBoy signal to exit
                        break
                    if self._sound_enabled:
                        self.sound_output.write(self.pyboy.sound)
                except Exception as e:
                    if self._sound_enabled:
                        logging.warning(f"Sound error during tick, disabling sound: {e}")
                        self._sound_enabled = False
                        self.sound_output.close()
                    else:
                        raise
                fps_frame_count += 1

                # --- State-aware interrupt detection ---
                # Poll RAM every tick (~nanosecond cost) to catch battle/map transitions
                cur_battle = self.pyboy.memory[ADDR_IS_IN_BATTLE]
                cur_map = self.pyboy.memory[ADDR_CUR_MAP]

                battle_changed = cur_battle != tracked_battle_state
                map_changed = cur_map != tracked_map_id

                if battle_changed or map_changed:
                    reason_parts = []
                    if battle_changed:
                        reason_parts.append(f"battle {'started' if cur_battle else 'ended'} ({tracked_battle_state}->{cur_battle})")
                    if map_changed:
                        reason_parts.append(f"map changed ({tracked_map_id:#04x}->{cur_map:#04x})")
                    reason = ", ".join(reason_parts)
                    tracked_battle_state = cur_battle
                    tracked_map_id = cur_map
                    if map_changed:
                        last_map_change_time = current_time
                    if battle_changed and cur_battle:
                        last_battle_start_time = current_time

                    # Abort any currently-running button sequence (e.g. D64 mid-walk)
                    state_change_event.set()

                    with lock:
                        if ai_is_analyzing and not interrupt_fired:
                            # Stale analysis in flight — discard its future actions
                            interrupt_fired = True
                            pending_actions.clear()
                            logging.info(f"INTERRUPT: t={self.game_state.turn_count} {reason} — discarding pending actions, will re-analyze immediately")
                            self.display.print_event(f"Interrupt: {reason}")
                        else:
                            # Either not analyzing, or already interrupted.
                            # Clear stale actions and accelerate re-analysis regardless.
                            discarded = len(pending_actions)
                            pending_actions.clear()
                            adaptive_interval = self.config.CONTINUOUS_ANALYSIS_INTERVAL
                            last_action_time = 0  # bypass settle check
                            if discarded:
                                logging.info(f"INTERRUPT: t={self.game_state.turn_count} {reason} — discarded {discarded} stale queued actions, re-analyzing soon")
                            else:
                                logging.info(f"INTERRUPT: t={self.game_state.turn_count} {reason} — idle transition, scheduling re-analysis")
                            self.display.print_event(f"Interrupt: {reason}")

                    # Immediately refresh display context so dashboard reflects new state
                    try:
                        if cur_battle:
                            # Battle just started — show battle grid immediately
                            fresh_battle = extract_battle_context(self.pyboy, just_entered_battle=True)
                            if fresh_battle and fresh_battle.get("text"):
                                self.display.update(spatial_grid=fresh_battle["text"], status="Battle!")
                        else:
                            # Battle ended or map changed — clear battle grid,
                            # show transitional status until next full analysis
                            self.display.update(spatial_grid="", status="Transitioning...")
                    except Exception:
                        pass  # display refresh is best-effort

                # Feed web stream every 2nd tick (~30fps — plenty for the dashboard)
                if self.web_streamer and fps_frame_count % 2 == 0:
                    self.display.set_frame(self.pyboy.screen.image)

                # Log FPS periodically
                fps_elapsed = current_time - fps_last_log_time
                if fps_elapsed >= fps_log_interval:
                    current_fps = fps_frame_count / fps_elapsed
                    fps_target = 59.7
                    logging.debug(f"FPS: {current_fps:.1f} (target: {fps_target}, frames: {fps_frame_count} in {fps_elapsed:.1f}s)")
                    self.display.update(fps=current_fps)
                    fps_frame_count = 0
                    fps_last_log_time = current_time
                
        except KeyboardInterrupt:
            shutdown_event.set()
        finally:
            signal.signal(signal.SIGINT, prev_handler)

        logging.info("Shutting down emulation")
        ct = self.cost_tracker
        logging.info(
            f"SESSION TOTALS: turns={self.game_state.turn_count} "
            f"input={ct.input_tokens} output={ct.output_tokens} "
            f"cache_read={ct.cache_read_tokens} cache_create={ct.cache_create_tokens} "
            f"cost=${ct.cost_usd:.4f}"
        )
        self.display.print_event("Stopping emulation...")

        # Save state on exit so no progress is lost
        self._save_state_now("shutdown")

        # Clean up
        self.sound_output.close()
        if ai_thread and ai_thread.is_alive():
            # Wait for AI thread to complete (with timeout)
            ai_thread.join(timeout=2.0)

    def run(self):
        """Run the game agent until completion."""
        # Show initial display with cartridge title
        self.display.update(game=self.pyboy.cartridge_title, status="Booting...")

        # Advance past Game Boy boot sequence so first screenshot isn't blank
        boot_frames = self.config.BOOT_FRAMES
        if boot_frames > 0:
            logging.info(f"Advancing {boot_frames} frames for boot sequence...")
            for _ in range(boot_frames):
                self.pyboy.tick(sound=self._sound_enabled)

        # Run continuous emulation
        self.run_continuous()
