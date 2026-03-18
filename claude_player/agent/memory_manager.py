import logging
import os
from typing import List, Dict, Any

from claude_player.config.config_class import ConfigClass
from claude_player.interface.claude_interface import ClaudeInterface
from claude_player.state.game_state import GameState
from claude_player.utils.message_utils import MessageUtils

MEMORY_MAX_LINES = 80
MEMORY_WARN_LINES = 60

MEMORY_SYSTEM_PROMPT = """\
You maintain a concise MEMORY file for a Pokemon Red AI agent. The agent reads this \
file via a tool call, so every line costs a turn — keep it SHORT.

Your job: read the current memory and recent gameplay, then produce an UPDATED file.

HARD LIMIT: 80 lines max. Aim for 40-60 lines.

Sections (use exactly these):
## STATUS — 2-3 lines: milestone progress (from AUTHORITATIVE data), current location, immediate goal
## PARTY — 1 line per Pokemon: name, level, type, key moves. No HP (read from RAM live)
## INVENTORY — 1-2 lines: badges, money, key items only
## MAP KNOWLEDGE — Routes discovered, verified paths, dead ends. One line per location. Drop locations no longer relevant to current goal
## STRATEGY — 2-5 lines: current plan, what to try next, mistakes to avoid RIGHT NOW
## LESSONS — 3-5 bullet points: hard-won insights that prevent repeating past failures

Rules:
- AUTHORITATIVE STORY PROGRESS and PARTY STATUS come from RAM — use exact values
- Consolidate aggressively. Remove stale info. No turn-by-turn logs
- No filler, no speculation, no battle play-by-play
- Output ONLY the file content. No preamble
"""

INITIAL_MEMORY_PROMPT = """\
The agent just started playing. Create a concise initial memory file (under 40 lines).
Output ONLY the memory file content.
"""


class MemoryManager:
    """Background subagent that maintains persistent memory (saves/MEMORY.md)."""

    def __init__(self, client: ClaudeInterface, game_state: GameState, config: ConfigClass):
        self.client = client
        self.game_state = game_state
        self.config = config
        self.update_count = 0

        # Memory file path: saves/MEMORY.md alongside autosave.state
        self.memory_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "saves", "MEMORY.md",
        )

    def _read_memory(self) -> str:
        """Read current memory file, or empty string if missing."""
        if os.path.exists(self.memory_path):
            with open(self.memory_path, "r") as f:
                return f.read()
        return ""

    def _write_memory(self, content: str) -> int:
        """Write memory file, enforcing line cap. Returns line count."""
        lines = content.split("\n")
        if len(lines) > MEMORY_MAX_LINES:
            lines = lines[:MEMORY_MAX_LINES]
            content = "\n".join(lines)
            logging.warning(f"Memory truncated to {MEMORY_MAX_LINES} lines")

        os.makedirs(os.path.dirname(self.memory_path), exist_ok=True)
        with open(self.memory_path, "w") as f:
            f.write(content)

        line_count = len(lines)
        if line_count >= MEMORY_WARN_LINES:
            logging.warning(f"Memory at {line_count}/{MEMORY_MAX_LINES} lines — consolidation needed")
        return line_count

    def update_memory(self, chat_history: List[Dict[str, Any]]) -> str:
        """Generate an updated memory file from recent gameplay.

        Called on a background thread every MEMORY_INTERVAL turns.
        Uses a cheap model (Haiku) to process chat history + current memory.

        Returns the updated memory text, or an error marker string.
        """
        self.update_count += 1
        logging.info(f"Memory update #{self.update_count} starting")

        current_memory = self._read_memory()
        old_lines = len(current_memory.split("\n")) if current_memory else 0

        # Trim chat history: last 60 messages, clean orphaned leading messages
        recent = chat_history[-60:] if len(chat_history) > 60 else chat_history[:]
        while recent:
            first = recent[0]
            if first["role"] == "assistant":
                recent.pop(0)
                continue
            if first["role"] == "user" and isinstance(first.get("content"), list):
                if any(isinstance(c, dict) and c.get("type") == "tool_result"
                       for c in first["content"]):
                    recent.pop(0)
                    continue
            break

        # Build the request
        messages = list(recent)
        user_block = [
            {"type": "text", "text": "Analyze the recent gameplay and produce an updated memory file."},
        ]

        if current_memory:
            user_block.append({
                "type": "text",
                "text": f"Current MEMORY.md ({old_lines} lines):\n\n{current_memory}",
            })
        else:
            user_block.append({
                "type": "text",
                "text": "No existing memory file — create the initial one.",
            })

        # Inject current turn for context
        cur_turn = self.game_state.turn_count
        user_block.append({
            "type": "text",
            "text": f"Current turn: T{cur_turn}. Keep the file under {MEMORY_MAX_LINES} lines.",
        })

        # Inject authoritative data so the model doesn't hallucinate
        if self.game_state.story_progress and self.game_state.story_progress.get("progress_summary"):
            user_block.append({
                "type": "text",
                "text": f"AUTHORITATIVE STORY PROGRESS (from RAM):\n{self.game_state.story_progress['progress_summary']}",
            })
        if self.game_state.party_summary:
            user_block.append({
                "type": "text",
                "text": f"AUTHORITATIVE PARTY STATUS (from RAM):\n{self.game_state.party_summary}",
            })

        messages.append({"role": "user", "content": user_block})

        system = INITIAL_MEMORY_PROMPT if self.update_count == 1 and not current_memory else MEMORY_SYSTEM_PROMPT

        try:
            # Use MEMORY config (falls back to Haiku defaults)
            memory_config = self.config.MEMORY.copy() if hasattr(self.config, 'MEMORY') else {}
            # Ensure model config fields exist for the API call
            if "MODEL" not in memory_config:
                memory_config["MODEL"] = "claude-sonnet-4-6"
            if "MAX_TOKENS" not in memory_config:
                memory_config["MAX_TOKENS"] = 4096
            if "THINKING" not in memory_config:
                memory_config["THINKING"] = True
            if "THINKING_BUDGET" not in memory_config:
                memory_config["THINKING_BUDGET"] = 2048

            response = self.client.send_request(memory_config, system, messages, [])
            content = MessageUtils.print_and_extract_message_content(response)

            # Log memory subagent token usage
            usage = getattr(response, 'usage', None)
            if usage:
                from claude_player.utils.cost_tracker import estimate_cost
                m_in = getattr(usage, 'input_tokens', 0) or 0
                m_out = getattr(usage, 'output_tokens', 0) or 0
                m_cr = getattr(usage, 'cache_read_input_tokens', 0) or 0
                m_cw = getattr(usage, 'cache_creation_input_tokens', 0) or 0
                m_cost = estimate_cost(memory_config.get("MODEL", ""), m_in, m_out, m_cr, m_cw)
                logging.info(
                    f"MEMORY TOKENS: in={m_in} out={m_out} "
                    f"cache_read={m_cr} cache_create={m_cw} "
                    f"| cost=${m_cost:.4f}"
                )

            new_memory = ""
            for block in content["text_blocks"]:
                new_memory += block.text

            new_lines = self._write_memory(new_memory)
            self.game_state.memory_turn = self.game_state.turn_count

            logging.info(f"Memory updated ({old_lines} → {new_lines} lines)")
            return new_memory

        except Exception as e:
            logging.error(f"Memory update failed: {e}")
            return f"[MEMORY_ERROR] {e}"
