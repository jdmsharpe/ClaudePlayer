"""Background subagent that maintains the categorized Knowledge Base.

Replaces the old flat MEMORY.md system. The subagent is called on a
background thread every MEMORY_INTERVAL turns and updates specific KB
sections based on what happened in recent gameplay.

Update triggers (section → when):
- party: after battle (team assessment changes)
- strategy: every update (plan evolves constantly)
- lessons: on failure/recovery (new insights)
- location: on map change (record discoveries about the map just left)
"""

import logging
import re
from typing import List, Dict, Any, Optional

from claude_player.config.config_class import ConfigClass
from claude_player.interface.claude_interface import ClaudeInterface
from claude_player.state.game_state import GameState
from claude_player.utils.message_utils import MessageUtils
from claude_player.agent.knowledge_base import KnowledgeBase

# System prompt for the KB update subagent
KB_SYSTEM_PROMPT = """\
You maintain a categorized Knowledge Base for a Pokémon Red AI agent.

Your job: read the current KB sections and recent gameplay, then produce UPDATED sections.

You will be told which sections to update. Output ONLY the requested sections using these exact XML tags:

<party>
1 line per Pokémon: name, type, key strengths/weaknesses, matchup notes.
Do NOT include HP, level, or PP — those come from RAM in real-time.
Focus on SUBJECTIVE knowledge: who to lead with, type matchup lessons, team composition strategy.
Max 15 lines.
</party>

<strategy>
Current plan, priorities, what to try next, mistakes to avoid RIGHT NOW.
Include milestone progress context and immediate navigation goals.
Max 20 lines.
</strategy>

<lessons>
Hard-won rules that prevent repeating past failures.
Each lesson should be a concrete, actionable rule — not vague advice.
Mark hallucination-prone beliefs with [VERIFY].
Max 20 lines.
</lessons>

<location name="Map Name">
Per-map notes: verified paths, dead ends, warp destinations, key NPCs, items found.
Positions use (x,y) block coordinates from RAM.
Max 30 lines per map.
</location>

Rules:
- AUTHORITATIVE STORY PROGRESS and PARTY STATUS come from RAM — use exact values
- Consolidate aggressively. Remove stale info. No turn-by-turn logs
- No filler, no speculation, no battle play-by-play
- Output ONLY the XML-tagged sections requested. No preamble or explanation
- You may output multiple <location> tags if the agent visited multiple maps
"""

INITIAL_KB_PROMPT = """\
The agent just started playing. Create initial KB sections from the gameplay so far.
Output party, strategy, and lessons sections. Keep each section concise.
"""


class MemoryManager:
    """Background subagent that maintains the categorized Knowledge Base."""

    def __init__(self, client: ClaudeInterface, game_state: GameState,
                 config: ConfigClass, knowledge_base: KnowledgeBase):
        self.client = client
        self.game_state = game_state
        self.config = config
        self.kb = knowledge_base
        self.update_count = 0
        self._last_update_map_id: Optional[int] = None

    def update_memory(self, chat_history: List[Dict[str, Any]],
                      current_map_id: Optional[int] = None,
                      current_map_name: Optional[str] = None) -> str:
        """Generate updated KB sections from recent gameplay.

        Called on a background thread every MEMORY_INTERVAL turns.

        Args:
            chat_history: Snapshot of complete message history.
            current_map_id: Current map ID for location updates.
            current_map_name: Human-readable name of current map.

        Returns:
            Summary of what was updated, or error marker string.
        """
        self.update_count += 1
        logging.info(f"KB update #{self.update_count} starting")

        # Determine which sections to update
        sections_to_update = self._decide_sections(current_map_id)

        # Read current KB state for the sections we're updating
        current_kb = self._read_current_state(sections_to_update, current_map_id)

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
            {"type": "text", "text": (
                f"Update these KB sections: {', '.join(sections_to_update)}.\n"
                f"Current turn: T{self.game_state.turn_count}."
            )},
        ]

        if current_kb:
            user_block.append({
                "type": "text",
                "text": f"Current KB state:\n\n{current_kb}",
            })
        else:
            user_block.append({
                "type": "text",
                "text": "No existing KB — create initial sections.",
            })

        if current_map_name and "location" in sections_to_update:
            user_block.append({
                "type": "text",
                "text": f"Current map: {current_map_name} (ID: 0x{current_map_id:02X})",
            })

        # Inject authoritative data
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

        is_initial = self.update_count == 1 and not current_kb
        system = INITIAL_KB_PROMPT if is_initial else KB_SYSTEM_PROMPT

        try:
            memory_config = self.config.MEMORY.copy() if hasattr(self.config, 'MEMORY') else {}
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

            # Log token usage
            usage = getattr(response, 'usage', None)
            if usage:
                from claude_player.utils.cost_tracker import estimate_cost
                m_in = getattr(usage, 'input_tokens', 0) or 0
                m_out = getattr(usage, 'output_tokens', 0) or 0
                m_cr = getattr(usage, 'cache_read_input_tokens', 0) or 0
                m_cw = getattr(usage, 'cache_creation_input_tokens', 0) or 0
                m_cost = estimate_cost(memory_config.get("MODEL", ""), m_in, m_out, m_cr, m_cw)
                logging.info(
                    f"KB TOKENS: in={m_in} out={m_out} "
                    f"cache_read={m_cr} cache_create={m_cw} "
                    f"| cost=${m_cost:.4f}"
                )

            raw_output = ""
            for block in content["text_blocks"]:
                raw_output += block.text

            # Parse XML-tagged sections from subagent output
            updated = self._parse_and_write(raw_output, current_map_id)
            self.game_state.memory_turn = self.game_state.turn_count
            self._last_update_map_id = current_map_id

            logging.info(f"KB updated: {', '.join(updated) if updated else 'no sections parsed'}")
            return f"Updated: {', '.join(updated)}" if updated else "No sections parsed"

        except Exception as e:
            logging.error(f"KB update failed: {e}")
            return f"[KB_ERROR] {e}"

    def _decide_sections(self, current_map_id: Optional[int]) -> List[str]:
        """Decide which KB sections to update this cycle."""
        sections = ["strategy"]  # Always update strategy

        # Update party every time (cheap, important for matchup learning)
        sections.append("party")

        # Update lessons every 3rd update (they accumulate slowly)
        if self.update_count % 3 == 0 or self.update_count <= 2:
            sections.append("lessons")

        # Update location if we have a map
        if current_map_id is not None:
            sections.append("location")

        return sections

    def _read_current_state(self, sections: List[str],
                            current_map_id: Optional[int]) -> str:
        """Read current KB state for the sections being updated."""
        parts = []

        for section in sections:
            if section == "location":
                if current_map_id is not None:
                    text = self.kb.read_location(current_map_id)
                    if text:
                        from claude_player.data.maps import MAP_NAMES
                        map_name = MAP_NAMES.get(current_map_id, f"Map 0x{current_map_id:02X}")
                        parts.append(f"<location name=\"{map_name}\">\n{text}\n</location>")
            else:
                text = self.kb.read_section(section)
                if text:
                    parts.append(f"<{section}>\n{text}\n</{section}>")

        return "\n\n".join(parts)

    def _parse_and_write(self, output: str, current_map_id: Optional[int]) -> List[str]:
        """Parse XML-tagged sections from subagent output and write to KB."""
        updated = []

        # Parse core sections
        for section in ("party", "strategy", "lessons"):
            match = re.search(
                rf'<{section}>\s*(.*?)\s*</{section}>',
                output, re.DOTALL,
            )
            if match:
                content = match.group(1).strip()
                if content:
                    self.kb.write_section(section, content)
                    updated.append(section)

        # Parse location sections (may have multiple)
        for match in re.finditer(
            r'<location\s+name="([^"]+)">\s*(.*?)\s*</location>',
            output, re.DOTALL,
        ):
            map_name = match.group(1).strip()
            content = match.group(2).strip()
            if content:
                self.kb.write_location_by_name(map_name, content)
                updated.append(f"location:{map_name}")

        # If subagent wrote a location without a name attribute, use current map
        nameless = re.search(
            r'<location>\s*(.*?)\s*</location>',
            output, re.DOTALL,
        )
        if nameless and current_map_id is not None:
            content = nameless.group(1).strip()
            if content:
                self.kb.write_location(current_map_id, content)
                from claude_player.data.maps import MAP_NAMES
                name = MAP_NAMES.get(current_map_id, f"0x{current_map_id:02X}")
                updated.append(f"location:{name}")

        return updated
