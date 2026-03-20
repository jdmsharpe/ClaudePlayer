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
You are a KNOWLEDGE BASE UPDATER, not the game agent. You do NOT play the game.
Do NOT use tools. Do NOT respond to battle situations. Do NOT narrate gameplay.

Your ONLY job: read current KB sections + recent gameplay context, then output UPDATED \
XML sections.

CRITICAL: Your entire response must be XML tags. No preamble, no explanation, no markdown.

Output ONLY the requested sections using these exact XML tags:

<party>
1 line per Pokémon: name, type, key strengths/weaknesses, role on team.
NEVER include HP, level, PP, or fainted status — RAM provides those every turn.
WRONG: "CHARMELEON Lv28 — 19/82 HP, SCRATCH 0pp"
RIGHT: "CHARMELEON (Fire) — Lead attacker. SCRATCH+EMBER for offense. Weak to Water/Rock."
Focus on: type matchup lessons, who to lead with, team composition strategy, catch priorities.
Max 15 lines.
</party>

<strategy>
Current plan, priorities, milestone progress, next steps.
Focus on DURABLE goals — not ephemeral battle state or menu cursor positions.
WRONG: "Cursor on SCRATCH (0pp) — press D R A to reach LEER"
RIGHT: "Clear Mt. Moon B2F, get fossil, exit via B1F east to Route 4"
Remove info that becomes stale after the current battle/menu ends.
Max 20 lines.
</strategy>

<lessons>
Hard-won rules that prevent repeating past failures. Each must be concrete and actionable.
Prefix with [CRITICAL] for rules that caused major setbacks when violated.
Prefix with [RULE] for general best practices.
Mark unverified claims with [VERIFY].
Max 20 lines.
</lessons>

<location name="Map Name">
Per-map notes: verified paths, dead ends, warp destinations, key NPCs, items found.
Positions use (x,y) block coordinates from RAM.
Max 30 lines per map.
</location>

Rules:
- You are NOT the game agent. Do NOT use tools or respond to game events.
- Your ENTIRE response must be XML-tagged sections. Nothing else.
- AUTHORITATIVE data comes from RAM — use exact values for story progress
- Consolidate aggressively. Remove stale info. No turn-by-turn battle logs.
- No filler, no speculation, no battle play-by-play
- You may output multiple <location> tags if the agent visited multiple maps
"""

INITIAL_KB_PROMPT = """\
You are a KNOWLEDGE BASE UPDATER, not the game agent. Do NOT play the game or use tools.

The agent just started playing. Create initial KB sections from the gameplay so far.
Output party, strategy, and lessons sections using XML tags. Keep each section concise.
Your entire response must be XML tags — no preamble or explanation.
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
        system_text = INITIAL_KB_PROMPT if is_initial else KB_SYSTEM_PROMPT
        # Wrap as cached content block so the static KB system prompt
        # gets cache-read pricing on subsequent KB calls within 5 min
        system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]

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

            # Fallback: if no sections parsed from text blocks, check thinking
            # blocks — the subagent sometimes puts XML there instead
            if not updated and content["thinking_blocks"]:
                thinking_output = ""
                for block in content["thinking_blocks"]:
                    thinking_output += block.thinking
                fallback = self._parse_and_write(thinking_output, current_map_id)
                if fallback:
                    updated = fallback
                    logging.info("KB: parsed sections from thinking block (fallback)")
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
