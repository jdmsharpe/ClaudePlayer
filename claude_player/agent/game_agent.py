import os
import sys
import logging
import time
import threading
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
        
        # Store previous tilemap and player position for spatial context
        self._previous_visible_tilemap = None
        self._previous_player_pos = None

        # Track consecutive thinking-only responses for recovery
        self._consecutive_thinking_only = 0

        # Track consecutive turns at the same position for stuck detection
        self._stuck_count = 0

        # Track recent actions for loop detection
        self._action_history = []  # List of (turn, action_string) tuples
        self._max_action_history = 8
        
        # Initialize game state
        self.game_state = GameState()
        self.game_state.cartridge_title = self.pyboy.cartridge_title
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
        screenshot = take_screenshot(self.pyboy, True)
        spatial_data = None
        if self.config.ENABLE_SPATIAL_CONTEXT:
            spatial_data = extract_spatial_context(
                self.pyboy,
                self._previous_visible_tilemap,
                previous_player_pos=self._previous_player_pos,
            )
            self._previous_visible_tilemap = spatial_data.get("visible_tilemap")
            # Stuck detection: only count when in overworld (player CAN move
            # but didn't).  Don't count dialogue/battle/menu/cutscene turns.
            current_pos = spatial_data.get("player_pos")
            detected_state = spatial_data.get("game_state")
            in_overworld = detected_state and detected_state.get("state") == "overworld"
            if not in_overworld:
                # Player can't move during dialogue/battle/cutscene — reset stuck counter
                self._stuck_count = 0
            elif self._previous_player_pos is not None and current_pos == self._previous_player_pos:
                self._stuck_count += 1
            elif current_pos is not None:
                self._stuck_count = 0
            self._previous_player_pos = current_pos
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
        return {
            "screenshot": screenshot,
            "spatial_data": spatial_data,
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

        # Update terminal display
        self.display.update(
            turn=self.game_state.turn_count,
            status="Analyzing...",
            game=self.game_state.identified_game or self.game_state.cartridge_title or "",
            goal=self.game_state.current_goal or "",
        )

        # Build user content from pre-captured data
        screenshot = captured_state["screenshot"]
        user_content = [screenshot]
        if captured_state.get("spatial_data") and captured_state["spatial_data"]["text"]:
            user_content.append({"type": "text", "text": captured_state["spatial_data"]["text"]})

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
                        "Single-tile moves only: D16, L16, R16, U16 (untried direction), or A1. Do NOT repeat above."
                    )
                })
                logging.warning(f"STUCK DETECTION (CRITICAL): {self._stuck_count} turns, forcing single-step mode")
            else:
                user_content.append({
                    "type": "text",
                    "text": (
                        f"STALLED {self._stuck_count} turns. Recent actions:\n{history_text}\n"
                        "Try: untried direction (1 tile = 16 frames), or A1 for hidden dialogue."
                    )
                })
                logging.warning(f"STUCK DETECTION: Player at same position for {self._stuck_count} turns")

        # Add timing information and cartridge title
        cartridge_title = captured_state.get("cartridge_title", "")
        header = f"Current time: {current_time_str}\nTurn #{self.game_state.turn_count}"
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
                compact=self.config.ENABLE_SPATIAL_CONTEXT
            )
            content_prefix = [{"type": "text", "text": current_memory}] if current_memory else []
            user_message = {"role": "user", "content": content_prefix + user_content}
            self.chat_history.append(user_message)
            self.game_state.add_to_complete_history(user_message)
            
        # Apply the screenshot limit
        self._limit_screenshots_in_history()
        
        # Check if we need to generate a summary (also retry if previous failed)
        summary_is_error = self.game_state.summary.startswith("[SUMMARY_ERROR]")
        should_generate = (
            (self.config.SUMMARY["INITIAL_SUMMARY"] and self.game_state.turn_count == 1)
            or (self.game_state.turn_count % self.config.SUMMARY["SUMMARY_INTERVAL"] == 0
                and self.game_state.turn_count > 0)
            or summary_is_error
        )
        if should_generate:
            if summary_is_error:
                logging.info(f"Retrying failed summary at turn {self.game_state.turn_count}")
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
                # Generate system prompt
                system_prompt = self.claude.generate_system_prompt()

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
        
        # FPS tracking
        fps_frame_count = 0
        fps_last_log_time = time.time()
        fps_log_interval = 10  # Log FPS every 10 seconds
        current_fps = 0.0

        # Main continuous emulation loop
        try:
            while True:
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
                        press_and_release_buttons(self.pyboy, action, settle_frames=0)
                    except Exception as e:
                        logging.error(f"Error executing inputs '{action}': {str(e)}")
                        # Continue with next actions rather than crashing
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

                # Log FPS periodically
                fps_elapsed = current_time - fps_last_log_time
                if fps_elapsed >= fps_log_interval:
                    current_fps = fps_frame_count / fps_elapsed
                    logging.info(f"FPS: {current_fps:.1f} (target: 59.7, frames: {fps_frame_count} in {fps_elapsed:.1f}s)")
                    self.display.update(fps=current_fps)
                    fps_frame_count = 0
                    fps_last_log_time = current_time
                
        except KeyboardInterrupt:
            logging.info("Received keyboard interrupt, stopping emulation")
            self.display.print_event("Stopping emulation...")
        
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