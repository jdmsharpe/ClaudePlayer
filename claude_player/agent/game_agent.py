import os
import sys
import logging
import time
import signal
import threading
import collections
from datetime import datetime
from pyboy import PyBoy

from claude_player.config.config_class import ConfigClass
from claude_player.config.config_loader import setup_logging
from claude_player.state.game_state import GameState
from claude_player.tools.tool_setup import setup_tool_registry
from claude_player.interface.claude_interface import ClaudeInterface
from claude_player.agent.summary_generator import SummaryGenerator
from claude_player.utils.message_utils import MessageUtils
from claude_player.utils.game_utils import take_screenshot
from claude_player.utils.spatial_context import extract_spatial_context
from claude_player.utils.battle_context import extract_battle_context
from claude_player.utils.party_context import extract_party_context
from claude_player.utils.bag_context import extract_bag_context
from claude_player.utils.menu_context import extract_menu_context
from claude_player.utils.terminal_display import TerminalDisplay

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

        # Enable sound if configured
        if hasattr(self.config, 'ENABLE_SOUND') and self.config.ENABLE_SOUND:
            logging.info("Sound enabled")
            pyboy_kwargs["sound_emulated"] = True

        self.pyboy = PyBoy(self.config.ROM_PATH, **pyboy_kwargs)
        self.pyboy.set_emulation_speed(target_speed=self.config.EMULATION_SPEED)
        
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
        self._visited_positions: collections.deque = collections.deque(maxlen=50)

        # Track consecutive thinking-only responses for recovery
        self._consecutive_thinking_only = 0

        # Track consecutive turns at the same position for stuck detection
        self._stuck_count = 0

        # Track recent actions for loop detection
        self._action_history = []  # List of (turn, action_string) tuples
        self._max_action_history = 8

        # Direction reversal detection: track last move's primary direction
        self._last_move_direction: str | None = None  # U/D/L/R
        self._reversal_detected = False  # Set True when next move reverses last

        # Dead-end memory: positions where CYCLING was detected
        # List of (map_x, map_y) absolute positions confirmed as dead ends
        self._dead_end_zones: list[tuple[int, int]] = []

        # Current context mode — drives which system prompt block to include
        self._in_battle = False
        self._in_menu = False

        # Battle-specific stuck detection: track battle state between turns
        self._battle_stuck_count = 0
        self._last_battle_snapshot = None  # (player_hp, enemy_hp, menu_type, cursor)

        # Party context: only inject when meaningful changes occur or periodically
        self._last_party_snapshot = None  # (hp, status) tuples for change detection
        self._last_party_inject_turn = 0  # Turn when party text was last injected
        self._party_refresh_interval = 10  # Inject every N turns even if unchanged

        # Bag context: inject on item change, post-battle, warnings, or periodically
        self._last_bag_snapshot = None       # (item_id, qty) tuples for change detection
        self._last_bag_inject_turn = 0
        self._bag_refresh_interval = 15     # Less frequent than party (bag changes rarely)

        # Periodic emulator state saving
        self._last_save_turn = 0
        self._save_interval = 100  # Save every N turns
        self._save_dir = os.path.join(os.path.dirname(self.config.ROM_PATH), "saves")
        self._save_path = os.path.join(self._save_dir, "autosave.state")
        
        # Initialize game state
        self.game_state = GameState()
        self.game_state.cartridge_title = self.pyboy.cartridge_title
        # Auto-set game identity from cartridge title so Claude doesn't waste
        # a tool call on set_game (and the log doesn't say "Not identified")
        if not self.game_state.identified_game and self.pyboy.cartridge_title:
            self.game_state.identified_game = self.pyboy.cartridge_title
        self.game_state.runtime_thinking_enabled = self.config.ACTION.get("THINKING", True)
        
        # Initialize tool registry
        self.tool_registry = setup_tool_registry(self.pyboy, self.game_state, self.config)
        
        # Initialize Claude interface
        self.claude = ClaudeInterface(self.config)

        # Initialize summary generator
        self.summary_generator = SummaryGenerator(self.claude, self.game_state, self.config)
        
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
                self.web_streamer = WebStreamer(self.display, port=web_port, config=self.config)
                self.web_streamer.start()
            except ImportError:
                logging.warning("Flask not installed — web streamer disabled (pip install flask)")
            except Exception as e:
                logging.warning(f"Failed to start web streamer: {e}")
    
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

        screenshot = take_screenshot(self.pyboy, True)
        # Feed raw frame to display for web streaming
        self.display.set_frame(self.pyboy.screen.image)
        spatial_data = None
        if self.config.ENABLE_SPATIAL_CONTEXT:
            spatial_data = extract_spatial_context(
                self.pyboy,
                self._previous_visible_tilemap,
                previous_player_pos=self._previous_player_pos,
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
            elif current_pos is not None:
                self._stuck_count = 0
            self._previous_player_pos = current_pos
            # Track visited positions for exploration analysis
            if current_pos is not None:
                # Clear history on map change (large position jump)
                if (self._visited_positions
                        and abs(current_pos[0] - self._visited_positions[-1][0])
                        + abs(current_pos[1] - self._visited_positions[-1][1]) > 10):
                    self._visited_positions.clear()
                    self._dead_end_zones.clear()  # new map — reset dead ends
                self._visited_positions.append(current_pos)

            # ── Unified navigation hint (single message, priority-based) ──
            # Instead of layering multiple independent warnings (CYCLING,
            # LOOPING, THRASHING, DEAD END, EXPLORATION) that can
            # contradict each other, compute ONE coherent directive.
            if (len(self._visited_positions) >= 10
                    and current_pos is not None
                    and spatial_data.get("text")):
                cx, cy = current_pos
                from collections import Counter
                pos_counts = Counter(self._visited_positions)
                most_visited_pos, most_visited_count = pos_counts.most_common(1)[0]
                unique_tiles = len(pos_counts)
                xs = [p[0] for p in self._visited_positions]
                ys = [p[1] for p in self._visited_positions]
                x_range = max(xs) - min(xs)
                y_range = max(ys) - min(ys)

                is_cycling = (len(self._visited_positions) >= 8
                              and most_visited_count >= 4)
                is_small_area = (len(self._visited_positions) >= 15
                                 and x_range <= 6 and y_range <= 6)
                is_thrashing = (len(self._visited_positions) >= 15
                                and x_range > 8 and y_range <= 3)

                # Record dead-end zone when cycling detected
                if is_cycling:
                    if not any(abs(cx - dz[0]) + abs(cy - dz[1]) <= 3
                              for dz in self._dead_end_zones):
                        self._dead_end_zones.append((cx, cy))
                        logging.info(f"DEAD-END ZONE recorded at ({cx},{cy})")

                # Compute avoid/suggest directions from dead-end zones
                avoid_dirs: list[str] = []
                at_dead_end = False
                for dz_x, dz_y in self._dead_end_zones:
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

                # Compute unexplored directions from movement centroid
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

            # Auto-set goal from event flags
            story_progress = spatial_data.get("story_progress")
            if story_progress:
                self.game_state.story_progress = story_progress
                if self.game_state.auto_goal_enabled and story_progress.get("next_goal"):
                    new_goal = story_progress["next_goal"]
                    if self.game_state.current_goal != new_goal:
                        logging.info(f"AUTO-GOAL: {new_goal}")
                        self.game_state.current_goal = new_goal
            if spatial_data.get("text"):
                logging.debug(f"Spatial context:\n{spatial_data['text']}")

        # Extract battle context when in battle (replaces spatial grid)
        battle_data = None
        if self.config.ENABLE_SPATIAL_CONTEXT and spatial_data:
            if (spatial_data.get("game_state") or {}).get("state") == "battle":
                # self._in_battle still holds the PREVIOUS turn's value here
                # (updated at line ~511 after capture). So not self._in_battle
                # correctly identifies "just entered battle this turn".
                battle_data = extract_battle_context(self.pyboy, just_entered_battle=not self._in_battle)

        # Extract party context (always available — overworld and battle)
        party_data = None
        if self.config.ENABLE_SPATIAL_CONTEXT:
            party_data = extract_party_context(self.pyboy)
            # Store latest party summary for the summary generator
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

        return {
            "screenshot": screenshot,
            "spatial_data": spatial_data,
            "battle_data": battle_data,
            "party_data": party_data,
            "bag_data": bag_data,
            "menu_data": menu_data,
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

        # Log the turn
        current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        logging.info(f"======= NEW TURN: {current_time_str} =======")
        self.game_state.log_state()

        # Extract display text for terminal
        spatial_grid = ""
        location = ""
        spatial_data = captured_state.get("spatial_data")
        battle_data = captured_state.get("battle_data")

        menu_data = captured_state.get("menu_data")

        self._was_in_battle = self._in_battle
        self._in_battle = bool(battle_data and battle_data.get("text"))
        self._in_menu = bool(menu_data and menu_data.get("text"))

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
                elif line.startswith("GAME STATE:") or line.startswith("PROGRESS:"):
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

        # Build compact one-line summaries for terminal display
        party_data = captured_state.get("party_data")
        bag_data = captured_state.get("bag_data")

        party_summary = ""
        if party_data and party_data.get("party"):
            mons = []
            for m in party_data["party"]:
                status = f" [{m['status']}]" if m["status"] != "OK" else ""
                mons.append(f"{m['name']} Lv{m['level']} {m['hp']}/{m['max_hp']}{status}")
            health = party_data.get("health", {})
            team = f"HP:{health.get('total_hp_pct', '?')}%"
            if health.get("recommendation"):
                team += f" — {health['recommendation']}"
            party_summary = " | ".join(mons) + f" [{team}]"

        party_mons_list = []
        if party_data and party_data.get("party"):
            party_mons_list = [
                {"name": m["name"], "level": m["level"], "hp": m["hp"],
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

        # Read Pokédex caught count from RAM (wPokedexOwned = 0xD2F7, 19 bytes, 1 bit/mon)
        dex_caught = sum(bin(self.pyboy.memory[0xD2F7 + i]).count("1") for i in range(19))
        dex_seen = sum(bin(self.pyboy.memory[0xD30A + i]).count("1") for i in range(19))

        # Read trainer name from RAM (wPlayerName = 0xD158, 11 bytes, Gen 1 charset, 0x50=terminator)
        _G1_CHARS = {
            **{0x80 + i: chr(ord('A') + i) for i in range(26)},
            **{0xA0 + i: chr(ord('a') + i) for i in range(26)},
            **{0xF6 + i: chr(ord('0') + i) for i in range(10)},
        }
        raw = []
        for i in range(11):
            b = self.pyboy.memory[0xD158 + i]
            if b == 0x50:
                break
            raw.append(b)
        trainer_name = "".join(_G1_CHARS.get(b, "") for b in raw).strip() or ""

        # Trainer ID (wPlayerID = 0xD359, 2 bytes big-endian)
        trainer_id = (self.pyboy.memory[0xD359] << 8) | self.pyboy.memory[0xD35A]

        # Play time (wPlayTimeHours = 0xDA40 word, wPlayTimeMinutes = 0xDA42 byte)
        pt_hours = (self.pyboy.memory[0xDA40] << 8) | self.pyboy.memory[0xDA41]
        pt_mins = self.pyboy.memory[0xDA42]
        play_time = f"{pt_hours}:{pt_mins:02d}"

        # Badges list from bag data (already read above)
        badges_list = bag_data.get("badges", []) if bag_data else []

        # Update terminal display
        self.display.update(
            turn=self.game_state.turn_count,
            status="Analyzing...",
            game=self.game_state.identified_game or self.game_state.cartridge_title or "",
            goal=self.game_state.current_goal or "",
            spatial_grid=spatial_grid,
            location=location,
            party_summary=party_summary,
            party_mons=party_mons_list,
            bag_summary=bag_summary,
            bag_items=bag_items_list,
            menu_summary=menu_summary,
            dex_caught=dex_caught,
            dex_seen=dex_seen,
            trainer_name=trainer_name,
            trainer_id=trainer_id,
            play_time=play_time,
            badges=badges_list,
        )

        # Build user content from pre-captured data
        screenshot = captured_state["screenshot"]
        user_content = [screenshot]
        if battle_data and battle_data.get("text"):
            # In battle: use battle context instead of spatial grid
            user_content.append({"type": "text", "text": battle_data["text"]})
        elif spatial_data and spatial_data["text"]:
            user_content.append({"type": "text", "text": spatial_data["text"]})

        # Menu context: inject every turn when active (menus change frequently)
        if menu_data and menu_data.get("text"):
            user_content.append({"type": "text", "text": menu_data["text"]})

        # Party status: inject only on meaningful changes or periodically
        if party_data and party_data.get("text"):
            # Snapshot: tuple of (hp, status) per mon for cheap comparison
            current_snapshot = tuple(
                (m["hp"], m["status"]) for m in party_data["party"]
            )
            turns_since_inject = self.game_state.turn_count - self._last_party_inject_turn
            just_left_battle = self._was_in_battle and not self._in_battle
            party_changed = current_snapshot != self._last_party_snapshot
            needs_healing = party_data.get("health", {}).get("needs_healing", False)
            periodic = turns_since_inject >= self._party_refresh_interval

            if party_changed or just_left_battle or needs_healing or periodic:
                user_content.append({"type": "text", "text": party_data["text"]})
                self._last_party_inject_turn = self.game_state.turn_count
                if party_changed:
                    logging.debug("Party context injected: state changed")

            self._last_party_snapshot = current_snapshot

        # Bag/inventory: inject on item change, post-battle, warnings, or periodically
        if bag_data and bag_data.get("text"):
            current_bag_snapshot = bag_data.get("snapshot")
            bag_turns_since = self.game_state.turn_count - self._last_bag_inject_turn
            just_left_battle = self._was_in_battle and not self._in_battle
            bag_changed = current_bag_snapshot != self._last_bag_snapshot
            has_warnings = bool(bag_data.get("assessment", {}).get("warnings"))
            bag_periodic = bag_turns_since >= self._bag_refresh_interval

            if bag_changed or just_left_battle or has_warnings or bag_periodic:
                user_content.append({"type": "text", "text": bag_data["text"]})
                self._last_bag_inject_turn = self.game_state.turn_count
                if bag_changed:
                    logging.debug("Bag context injected: inventory changed")

            self._last_bag_snapshot = current_bag_snapshot

        # Critical HP urgency: when party is nearly wiped, prioritize retreat
        if (party_data and not self._in_battle
                and spatial_data and (spatial_data.get("game_state") or {}).get("state") == "overworld"):
            health = party_data.get("health", {})
            hp_pct = health.get("total_hp_pct", 100)
            alive_count = health.get("alive", 6)
            total_count = health.get("total", 6)
            if hp_pct <= 25 and alive_count <= 2:
                fainted = total_count - alive_count
                is_stuck_and_lost = bool(self._dead_end_zones)
                if is_stuck_and_lost:
                    # Agent is cycling in dead ends with critical HP —
                    # a blackout teleports to Pokemon Center (optimal play)
                    user_content.append({
                        "type": "text",
                        "text": (
                            f"CRITICAL HP: {fainted}/{total_count} Pokemon fainted,"
                            f" {hp_pct}% HP remaining. You are STUCK in a dead-end area."
                            f" BEST STRATEGY: Walk INTO grass (,) tiles to trigger a"
                            f" wild battle. Let your last Pokemon faint — a blackout"
                            f" teleports you to the nearest Pokemon Center for FREE"
                            f" healing. This is faster than wandering lost. Do NOT"
                            f" avoid grass — seek it out."
                        )
                    })
                    logging.warning(f"CRITICAL HP + STUCK: {fainted}/{total_count} fainted, {hp_pct}% HP — blackout strategy suggested")
                else:
                    user_content.append({
                        "type": "text",
                        "text": (
                            f"CRITICAL HP WARNING: {fainted}/{total_count} Pokemon fainted,"
                            f" {hp_pct}% HP remaining. Another wild encounter could cause a blackout."
                            f" PRIORITY: Avoid grass tiles (,) and exit to the nearest town with"
                            f" a Pokemon Center. If you have a repel, use it to skip encounters."
                        )
                    })
                logging.warning(f"CRITICAL HP: {fainted}/{total_count} fainted, {hp_pct}% HP — retreat urgency injected")

        # Stuck detection: escalating intervention when player hasn't moved
        if self._stuck_count >= 2:
            # Build action history text so the model can see what it already tried
            history_lines = []
            for turn, action in self._action_history[-5:]:
                history_lines.append(f"  Turn {turn}: {action}")
            history_text = "\n".join(history_lines) if history_lines else "  (none recorded)"

            if self._stuck_count >= 5:
                user_content.append({
                    "type": "text",
                    "text": (
                        f"STUCK {self._stuck_count} turns! Failed actions:\n{history_text}\n"
                        "Try ONE of these (do NOT repeat failed actions above):\n"
                        "- D16, L16, R16, U16 (untried direction)\n"
                        "- A1 (confirm/advance dialogue)\n"
                        "- B1 (cancel/back out of menu)\n"
                        "- S (open/close start menu)\n"
                        "If in a YES/NO menu, use U16/D16 to move cursor then A1 to confirm."
                    )
                })
                logging.warning(f"STUCK DETECTION (CRITICAL): {self._stuck_count} turns, forcing single-step mode")
            else:
                user_content.append({
                    "type": "text",
                    "text": (
                        f"STALLED {self._stuck_count} turns. Recent actions:\n{history_text}\n"
                        "Try: untried direction (1 tile = 16 frames), A for dialogue, or B to cancel menu."
                    )
                })
                logging.warning(f"STUCK DETECTION: Player at same position for {self._stuck_count} turns")

        # Battle stuck detection: same HP/menu/cursor for too many turns
        if self._in_battle and self._battle_stuck_count >= 4:
            history_lines = []
            for turn, action in self._action_history[-5:]:
                history_lines.append(f"  Turn {turn}: {action}")
            history_text = "\n".join(history_lines) if history_lines else "  (none)"

            if self._battle_stuck_count >= 7:
                user_content.append({
                    "type": "text",
                    "text": (
                        f"BATTLE STUCK {self._battle_stuck_count} turns! "
                        f"Recent actions:\n{history_text}\n"
                        "STOP reasoning — your inputs are not working. Try IN ORDER:\n"
                        "1. B B B (back out of any submenu to main battle menu)\n"
                        "2. Then FOLLOW THE TIP exactly — send its compound input\n"
                        "3. If no TIP: A A A A A (advance text/animations)"
                    )
                })
                logging.warning(f"BATTLE STUCK (CRITICAL): {self._battle_stuck_count} turns, same battle state")
            else:
                user_content.append({
                    "type": "text",
                    "text": (
                        f"BATTLE STALLED {self._battle_stuck_count} turns — "
                        "you may be in a submenu. Send B B to return to main menu, "
                        "then FOLLOW THE TIP exactly."
                    )
                })
                logging.warning(f"BATTLE STUCK: {self._battle_stuck_count} turns, same state")

        # Direction reversal warning: flag when agent immediately undoes last move
        if (self._reversal_detected and self._last_move_direction
                and not self._in_battle
                and spatial_data and (spatial_data.get("game_state") or {}).get("state") == "overworld"):
            _dir_names = {'U': 'UP', 'D': 'DOWN', 'L': 'LEFT', 'R': 'RIGHT'}
            _rev_map = {'U': 'D', 'D': 'U', 'L': 'R', 'R': 'L'}
            prev_dir = _dir_names.get(self._last_move_direction, '?')
            came_from = _dir_names.get(_rev_map.get(self._last_move_direction, ''), '?')
            user_content.append({
                "type": "text",
                "text": (
                    f"REVERSAL WARNING: Your last move went {prev_dir},"
                    f" but the move before that went {came_from}."
                    f" You are undoing your own progress."
                    f" Commit to a direction — do not ping-pong."
                )
            })
            logging.warning(f"REVERSAL: agent reversed direction ({came_from} → {prev_dir})")

        # Add timing header (include cartridge title only until game is identified)
        header = f"Current time: {current_time_str}\nTurn #{self.game_state.turn_count}"
        if not self.game_state.identified_game:
            cartridge_title = captured_state.get("cartridge_title", "")
            if cartridge_title:
                header += f"\nCartridge: {cartridge_title}"
        user_content.insert(0, {"type": "text", "text": header})
        
        # Add user message to chat history
        if len(self.chat_history) == 0:
            user_message = {"role": "user", "content": user_content}
            self.chat_history.append(user_message)
            self.game_state.add_to_complete_history(user_message)
        else:
            current_memory = self.game_state.get_current_state_summary(
                compact=self.config.ENABLE_SPATIAL_CONTEXT,
                summary_interval=self.config.SUMMARY["SUMMARY_INTERVAL"],
            )
            content_prefix = [{"type": "text", "text": current_memory}] if current_memory else []
            user_message = {"role": "user", "content": content_prefix + user_content}
            self.chat_history.append(user_message)
            self.game_state.add_to_complete_history(user_message)

            # Strip state prefix from older user messages to avoid duplicating
            # the summary (~750 tokens) in every message in the context window.
            for msg in self.chat_history[:-1]:
                if msg["role"] == "user" and isinstance(msg["content"], list) and len(msg["content"]) > 1:
                    first = msg["content"][0]
                    if isinstance(first, dict) and first.get("type") == "text":
                        text = first.get("text", "")
                        if "=== GAME PROGRESS SUMMARY ===" in text or text.startswith("Memory:"):
                            msg["content"].pop(0)
            
        # Apply the screenshot limit
        self._limit_screenshots_in_history()
        
        # Check if we need to generate a summary (also retry if previous failed)
        summary_is_error = self.game_state.summary.startswith("[SUMMARY_ERROR]")
        battle_state_changed = self._was_in_battle != self._in_battle
        should_generate = (
            (self.config.SUMMARY["INITIAL_SUMMARY"] and self.game_state.turn_count == 1)
            or (self.game_state.turn_count % self.config.SUMMARY["SUMMARY_INTERVAL"] == 0
                and self.game_state.turn_count > 0)
            or summary_is_error
            or (battle_state_changed and self.game_state.turn_count > 1)
        )
        if should_generate:
            if summary_is_error:
                logging.info(f"Retrying failed summary at turn {self.game_state.turn_count}")
            elif battle_state_changed:
                logging.info(f"Generating summary at turn {self.game_state.turn_count} (battle state changed: {'entered' if self._in_battle else 'exited'} battle)")
            else:
                logging.info(f"Generating summary at turn {self.game_state.turn_count}")
            summary = self.summary_generator.generate_summary(self.game_state.complete_message_history)
            self.game_state.update_summary(summary)

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
                # Generate system prompt with context-appropriate guidance block
                system_prompt = self.claude.generate_system_prompt(
                    in_battle=self._in_battle, in_menu=self._in_menu,
                )

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
                    logging.warning("RECOVERY: Temporarily disabling thinking to force output")

                # Send request to Claude
                message = self.claude.send_request(
                    action_config,
                    system_prompt,
                    self.chat_history,
                    tools
                )

                # Get assistant response and add to chat history
                assistant_content = message.content
                assistant_message = {"role": "assistant", "content": assistant_content}
                self.chat_history.append(assistant_message)
                self.game_state.add_to_complete_history(assistant_message)

                message_content = MessageUtils.print_and_extract_message_content(message)

                # Detect thinking-only responses (no text or tool output)
                if not message_content["text_blocks"] and not message_content["tool_use_blocks"]:
                    self._consecutive_thinking_only += 1
                    logging.warning(
                        f"THINKING-ONLY RESPONSE (#{self._consecutive_thinking_only}, "
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
                        logging.info("RECOVERY: Appended nudge message, retrying...")
                        continue  # Retry
                    else:
                        logging.warning("RECOVERY: Max retries reached, proceeding with empty response")
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
                # Queue for main-thread execution
                pending_actions.append(tool_input["inputs"])
                logging.info(f"Queued input for later execution: {tool_input['inputs']} (queue size: {len(pending_actions)})")
                self.display.update(last_action=tool_input["inputs"])
                # Record in action history for loop detection
                self._action_history.append((self.game_state.turn_count, tool_input["inputs"]))
                if len(self._action_history) > self._max_action_history:
                    self._action_history.pop(0)
                # Track primary direction for reversal detection
                import re as _re
                _dir_match = _re.search(r'[UDLR]', tool_input["inputs"])
                if _dir_match:
                    new_dir = _dir_match.group()
                    opposites = {'U': 'D', 'D': 'U', 'L': 'R', 'R': 'L'}
                    if (self._last_move_direction
                            and new_dir == opposites.get(self._last_move_direction)):
                        self._reversal_detected = True
                    else:
                        self._reversal_detected = False
                    self._last_move_direction = new_dir
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": "Input queued for execution"}]
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
            in_battle = self.pyboy.memory[0xD057]
            if in_battle != 0:
                return
        except Exception:
            return

        try:
            os.makedirs(self._save_dir, exist_ok=True)
            with open(self._save_path, "wb") as f:
                self.pyboy.save_state(f)
            self._last_save_turn = turn
            logging.info(f"Autosaved emulator state at turn {turn} → {self._save_path}")
            self.display.print_event(f"Autosaved at turn {turn}")
        except Exception as e:
            logging.warning(f"Failed to autosave: {e}")

    def _save_state_now(self, reason: str = "manual"):
        """Unconditionally save emulator state (used on shutdown)."""
        try:
            os.makedirs(self._save_dir, exist_ok=True)
            with open(self._save_path, "wb") as f:
                self.pyboy.save_state(f)
            turn = self.game_state.turn_count
            logging.info(f"Saved state on {reason} at turn {turn} → {self._save_path}")
            self.display.print_event(f"Saved on {reason} (turn {turn})")
        except Exception as e:
            logging.warning(f"Failed to save state on {reason}: {e}")

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
                        logging.warning("NO-ACTION TURN: Model used tools but didn't send_inputs — nudging")
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

                # Use only action duration for adaptive interval (excludes summary generation)
                with lock:
                    if no_action:
                        # Skip the wait — let the model retry immediately with the nudge
                        adaptive_interval = 0.5
                    else:
                        adaptive_interval = (0.7 * adaptive_interval) + (0.3 * action_duration)
                        min_interval = self.config.CONTINUOUS_ANALYSIS_INTERVAL
                        max_interval = getattr(self.config, 'MAX_ADAPTIVE_INTERVAL', 15.0)
                        adaptive_interval = max(min_interval, min(adaptive_interval, max_interval))

                logging.info(f"======= END ANALYSIS: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =======")
                timing_parts = f"total={last_analysis_duration:.2f}s (prep={prep_duration:.2f}s, action={action_duration:.2f}s)"
                logging.info(f"Analysis took {timing_parts}, adaptive interval: {adaptive_interval:.2f}s")

                self.display.update(
                    status=f"Idle ({last_analysis_duration:.1f}s)",
                    analysis_duration=last_analysis_duration,
                    game=self.game_state.identified_game or self.game_state.cartridge_title or "",
                    goal=self.game_state.current_goal or "",
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
        prev_handler = signal.getsignal(signal.SIGINT)

        def _shutdown_handler(signum, frame):
            shutdown_event.set()

        signal.signal(signal.SIGINT, _shutdown_handler)

        # FPS tracking
        fps_frame_count = 0
        fps_last_log_time = time.time()
        fps_log_interval = 10  # Log FPS every 10 seconds
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
                    try:
                        from claude_player.utils.game_utils import press_and_release_buttons
                        frame_cb = self.display.set_frame if self.web_streamer else None
                        press_and_release_buttons(self.pyboy, action, settle_frames=0, stop_event=shutdown_event, frame_callback=frame_cb)
                    except Exception as e:
                        logging.error(f"Error executing inputs '{action}': {str(e)}")
                        # Continue with next actions rather than crashing
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
                with lock:
                    if (not ai_is_analyzing
                            and time_since_last_analysis >= adaptive_interval
                            and time_since_last_action >= action_settle_seconds):
                        start_analysis = True
                        ai_is_analyzing = True
                        analysis_complete = False
                
                if start_analysis:
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
                    if fatal_error_msg:
                        logging.critical(f"Stopping continuous mode due to fatal error: {fatal_error_msg}")
                        self.display.print_event(f"Fatal error: {fatal_error_msg}")
                        break
                    
                # Tick the emulator regardless of AI state
                if not self.pyboy.tick():
                    # PyBoy signal to exit
                    break
                fps_frame_count += 1

                # Feed web stream every 2nd tick (~30fps — plenty for the dashboard)
                if self.web_streamer and fps_frame_count % 2 == 0:
                    self.display.set_frame(self.pyboy.screen.image)

                # Log FPS periodically
                fps_elapsed = current_time - fps_last_log_time
                if fps_elapsed >= fps_log_interval:
                    current_fps = fps_frame_count / fps_elapsed
                    logging.info(f"FPS: {current_fps:.1f} (target: 59.7, frames: {fps_frame_count} in {fps_elapsed:.1f}s)")
                    self.display.update(fps=current_fps)
                    fps_frame_count = 0
                    fps_last_log_time = current_time
                
        except KeyboardInterrupt:
            shutdown_event.set()
        finally:
            signal.signal(signal.SIGINT, prev_handler)

        logging.info("Shutting down emulation")
        self.display.print_event("Stopping emulation...")

        # Save state on exit so no progress is lost
        self._save_state_now("shutdown")

        # Clean up
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
                self.pyboy.tick()

        # Run continuous emulation
        self.run_continuous()