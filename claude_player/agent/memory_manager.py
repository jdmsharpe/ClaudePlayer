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
from typing import List, Dict, Any, Optional, Tuple

from claude_player.config.config_class import ConfigClass
from claude_player.interface.claude_interface import ClaudeInterface
from claude_player.state.game_state import GameState
from claude_player.utils.message_utils import MessageUtils
from claude_player.agent.knowledge_base import KnowledgeBase

# System prompt for the KB update subagent
# NOTE: This prompt must stay ABOVE 2048 tokens for prompt caching to work
# (extended thinking enabled, so 2048 is the minimum).
KB_SYSTEM_PROMPT = """\
You are a KNOWLEDGE BASE UPDATER for a Pokémon Red AI agent. You do NOT play the game.
Do NOT use tools. Do NOT respond to battle situations. Do NOT narrate gameplay.

Your ONLY job: read current KB sections + recent gameplay context, then output UPDATED \
XML sections. Think deeply about patterns in the gameplay — what worked, what failed, \
what the agent should do differently. Your analysis directly shapes the agent's behavior \
for the next 20 turns.

CRITICAL FORMAT RULE: Your entire response must be XML tags. No preamble, no explanation, \
no markdown, no commentary. Start your response with the first XML tag. End with the last \
closing tag. Any text outside of XML tags is WASTED — the parser ignores it.

Output ONLY the requested sections using these exact XML tags:

<party>
Team strategy and composition notes. 1 line per Pokémon in the current party.
Include: name, type(s), key moves and what they're good against, role on team, \
catch priorities for upcoming areas.
NEVER include HP, level, PP, or fainted status — RAM provides those every turn.
WRONG: "CHARMELEON Lv28 — 19/82 HP, SCRATCH 0pp"
RIGHT: "CHARMELEON (Fire) — Lead attacker. SCRATCH+EMBER for offense. Weak to Water/Rock."
Think about: Who should lead? What type coverage gaps exist? What Pokémon should we \
catch next to cover weaknesses? What upcoming gym/trainer needs specific prep?
Max 15 lines.
</party>

<strategy>
The agent's strategic roadmap — milestone progress, current priorities, and next steps.
Focus on DURABLE plans that survive across many turns.
NEVER include: player coordinates "(x,y)", exact PP counts, HP values, cursor positions, \
"currently at/in" position snapshots, or current battle state ("in battle vs X"). RAM \
provides ALL of these every turn — writing them here wastes lines and misleads when stale.
WRONG: "B1F at (21,11), in wild battle. SCRATCH at 5pp."
WRONG: "Current position (21,11) is in east section."
WRONG: "In wild battle vs PARAS on B2F."
WRONG: "CURRENT: In battle with..."
RIGHT: "Clear Mt. Moon B2F, get fossil, exit via B1F east to Route 4"
RIGHT: "PP running low — run from wild battles, prioritize healing"
Remove info about current battles, current positions, or anything that becomes stale \
after the current battle/menu/turn ends.
Think about: What's the overall route plan? What obstacles lie ahead? What items/Pokémon \
do we need before the next milestone? Are we on track or do we need to grind/heal/shop?
Max 20 lines.
</strategy>

<lessons>
Hard-won rules distilled from gameplay experience. These are the agent's institutional \
memory — they prevent it from repeating costly mistakes across sessions.
Prefix with [CRITICAL] for rules that caused major setbacks when violated.
Prefix with [RULE] for general best practices discovered through play.
Prefix with [STRATEGY] for strategic insights about game mechanics or routing.
Mark unverified claims with [VERIFY].
NEVER include: player coordinates, exact PP/HP numbers, or position snapshots.
Lessons must be general rules, not situational state.
WRONG: "[CRITICAL] B1F W1 (17,11) → B2F lands at (28,11) DEAD END"
RIGHT: "[CRITICAL] B1F W1 → B2F south zone DEAD END. Use W6 or W4 instead."
Think about: What patterns keep causing failures? What non-obvious game mechanics bit us? \
What routing or battle strategies consistently work? What should the agent always/never do?
Look for: navigation loops (same maps visited repeatedly), failed battle strategies, \
wasted turns from wrong menu navigation, items missed, efficient routes discovered.
Max 20 lines.
</lessons>

<location name="Map Name">
Per-map spatial intelligence. This is the agent's memory of each map's layout.
Include: verified paths and corridors, dead ends (with evidence), warp destinations and \
which ones to use/avoid, key NPC positions, item locations, trainer sight lines, \
elevation/ledge constraints, and optimal routes through the area.
Positions use (x,y) block coordinates from RAM.
Think about: What's the fastest route through this map? Which warps loop back and waste \
time? Where are the dead ends? What items or NPCs are worth visiting? What areas are \
still unexplored?
Max 30 lines per map.
</location>

Rules:
- You are NOT the game agent. Do NOT use tools or respond to game events.
- Your ENTIRE response must be XML-tagged sections. Nothing else.
- AUTHORITATIVE data comes from RAM — use exact values for story progress.
- Consolidate aggressively. Remove stale info. No turn-by-turn battle logs.
- No filler, no speculation, no battle play-by-play.
- You may output multiple <location> tags if the agent visited multiple maps.
- PRESERVE existing lessons and location notes unless they are proven wrong.
  Only REMOVE entries when you have clear evidence they are incorrect.
  ADD new entries; do not rewrite from scratch each time.
- When the agent has been STUCK or LOOPING (same position, same maps repeatedly), \
  analyze WHY and write a lesson about it. The agent loses many turns to loops.

Game reference (Pokémon Red / Gen 1):
- 151 Pokémon, 8 gyms, Elite Four. No special/physical split — all moves of a type
  use the same stat (Special for Fire/Water/Electric/etc, Attack for Normal/Fighting/etc).
- Type chart: Normal/Fighting can't hit Ghost. Ground can't hit Flying. Ghost can't hit
  Normal. Psychic has no real counter (Ghost moves bugged to deal 0 damage in Gen 1).
- Status: Sleep is the strongest (target can't act), Paralysis halves Speed and has 25%
  full-para chance. Freeze is rare but locks target permanently until hit by Fire move.
- Critical hits: high Speed = higher crit rate. Moves like SLASH have boosted crit.
- STRUGGLE: only triggers when ALL 4 moves are at 0 PP simultaneously.
- Trainer battles: cannot flee. Gym leaders have set teams. Wild encounters: can flee.
- Items: Poké Balls for catching (Great > Poké, Ultra > Great). Potions heal HP.
  Antidotes/Parlyz Heal cure status. Repels block wild encounters temporarily.
- HMs: CUT (requires Cascade Badge), SURF (requires Soul Badge), STRENGTH (requires
  Rainbow Badge), FLASH (requires Boulder Badge), FLY (requires Thunder Badge).

Route knowledge (for strategy planning):
- Pallet Town → Route 1 → Viridian City → Route 2 → Viridian Forest → Pewter City
- Pewter City → Route 3 → Mt. Moon → Route 4 → Cerulean City
- Cerulean City → Route 24/25 (Nugget Bridge) → Bill's House (get S.S. Ticket)
- Cerulean City → Route 5 → Underground → Route 6 → Vermilion City
- Vermilion City → S.S. Anne (get HM01 CUT) → Route 11 → Diglett's Cave
- Vermilion City → Route 9 → Rock Tunnel → Route 10 → Lavender Town
- Lavender Town → Route 8 → Underground → Route 7 → Celadon City
- Celadon City → Route 16 → Cycling Road → Fuchsia City
- Fuchsia City → Safari Zone (get HM03 SURF, Gold Teeth → HM04 STRENGTH)
- Back to Saffron City (need Silph Scope from Celadon + clear Pokémon Tower in Lavender)
- Saffron City → Silph Co. → Fighting Dojo → Saffron Gym
- Cinnabar Island (SURF south from Pallet or Fuchsia) → Pokémon Mansion → Cinnabar Gym
- Back to Viridian City → Viridian Gym → Route 22 → Route 23 → Victory Road → Indigo Plateau

Gym leaders and recommended levels:
- Brock (Rock): Lv12-14. Geodude+Onix. Water/Grass super-effective.
- Misty (Water): Lv18-21. Staryu+Starmie. Electric/Grass super-effective.
- Lt. Surge (Electric): Lv21-24. Voltorb+Pikachu+Raichu. Ground super-effective.
- Erika (Grass): Lv24-29. Victreebel+Tangela+Vileplume. Fire/Ice/Flying super-effective.
- Koga (Poison): Lv37-43. Koffing×2+Muk+Weezing. Ground/Psychic super-effective.
- Sabrina (Psychic): Lv38-43. Kadabra+Mr.Mime+Venomoth+Alakazam. Bug super-effective (but weak options in Gen 1).
- Blaine (Fire): Lv42-47. Growlithe+Ponyta+Rapidash+Arcanine. Water/Ground/Rock super-effective.
- Giovanni (Ground): Lv45-50. Rhyhorn+Dugtrio+Nidoqueen+Nidoking+Rhydon. Water/Grass/Ice super-effective.

Elite Four (consecutive, no healing between):
- Lorelei (Ice): Lv52-56. Dewgong+Cloyster+Slowbro+Jynx+Lapras. Electric/Fighting/Rock.
- Bruno (Fighting): Lv51-56. Onix×2+Hitmonlee+Hitmonchan+Machamp. Water/Psychic/Flying.
- Agatha (Ghost/Poison): Lv54-58. Gengar×2+Golbat+Haunter+Arbok. Ground/Psychic.
- Lance (Dragon): Lv56-62. Gyarados+Dragonair×2+Aerodactyl+Dragonite. Ice/Electric/Rock.
- Champion (varies): Lv59-65. Mixed team based on starter choice.

Key items to acquire:
- Old Rod (Vermilion), Good Rod (Fuchsia), Super Rod (Route 12) — for catching Water types
- Bike Voucher (Vermilion Pokémon Fan Club) → Bike (Cerulean) — essential for fast travel
- Silph Scope (Rocket Hideout in Celadon) — required to clear Pokémon Tower
- Poké Flute (Mr. Fuji after Pokémon Tower) — wakes Snorlax on Routes 12 and 16
- Master Ball (Silph Co. president) — save for Mewtwo or roaming legendary
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
        self._maps_since_last_update: List[Tuple[int, str]] = []  # (map_id, map_name)

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

        # Trim chat history: 2x MEMORY_INTERVAL messages (each turn ≈ 2 msgs)
        memory_interval = self.config.MEMORY.get("MEMORY_INTERVAL", 20) if self.config else 20
        history_window = memory_interval * 2
        recent = chat_history[-history_window:] if len(chat_history) > history_window else chat_history[:]
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
                f"Current turn: T{self.game_state.turn_count}.\n"
                f"REMINDER: strategy and lessons must NOT contain coordinates, "
                f"PP counts, HP values, or position snapshots — RAM provides those "
                f"every turn. Only durable plans and general rules."
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
            map_info = f"Current map: {current_map_name} (ID: 0x{current_map_id:02X})"
            # Include maps visited since last KB update for multi-location writes
            if self._maps_since_last_update:
                visited_names = []
                seen = set()
                for mid, mname in self._maps_since_last_update:
                    if mname not in seen:
                        visited_names.append(mname)
                        seen.add(mname)
                if visited_names:
                    map_info += f"\nMaps visited since last KB update: {', '.join(visited_names)}"
                    map_info += "\nWrite <location> tags for each visited map that has new info."
            user_block.append({
                "type": "text",
                "text": map_info,
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
                memory_config["MAX_TOKENS"] = 16000
            if "THINKING" not in memory_config:
                memory_config["THINKING"] = True
            if "THINKING_BUDGET" not in memory_config:
                memory_config["THINKING_BUDGET"] = 10000
            # Ensure enough headroom for XML output after thinking
            tb = memory_config.get("THINKING_BUDGET", 10000)
            if memory_config["MAX_TOKENS"] < tb + 4096:
                memory_config["MAX_TOKENS"] = tb + 4096

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

            if not updated:
                # Log what the subagent actually produced for debugging
                preview = raw_output[:200].replace('\n', ' ') if raw_output else "(empty)"
                logging.warning(f"KB: no sections parsed from output ({len(raw_output)} chars): {preview}")

            self.game_state.memory_turn = self.game_state.turn_count
            self._last_update_map_id = current_map_id
            self._maps_since_last_update.clear()

            logging.info(f"KB updated: {', '.join(updated) if updated else 'no sections parsed'}")
            return f"Updated: {', '.join(updated)}" if updated else "No sections parsed"

        except Exception as e:
            logging.error(f"KB update failed: {e}")
            return f"[KB_ERROR] {e}"

    def record_map_visit(self, map_id: int, map_name: str):
        """Record a map visit for multi-location KB updates.

        Called by game_agent on every map transition so the KB subagent
        knows which maps to write location notes for.
        """
        # Deduplicate: only add if not already the last entry
        if not self._maps_since_last_update or self._maps_since_last_update[-1][0] != map_id:
            self._maps_since_last_update.append((map_id, map_name))

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
                # Read location notes for current map AND all visited maps
                seen_map_ids = set()
                if current_map_id is not None:
                    seen_map_ids.add(current_map_id)
                    text = self.kb.read_location(current_map_id)
                    if text:
                        from claude_player.data.maps import MAP_NAMES
                        map_name = MAP_NAMES.get(current_map_id, f"Map 0x{current_map_id:02X}")
                        parts.append(f"<location name=\"{map_name}\">\n{text}\n</location>")
                # Include existing notes for maps visited since last update
                for mid, mname in self._maps_since_last_update:
                    if mid not in seen_map_ids:
                        seen_map_ids.add(mid)
                        text = self.kb.read_location(mid)
                        if text:
                            parts.append(f"<location name=\"{mname}\">\n{text}\n</location>")
            else:
                text = self.kb.read_section(section)
                if text:
                    parts.append(f"<{section}>\n{text}\n</{section}>")

        return "\n\n".join(parts)

    @staticmethod
    def _sanitize_strategy(content: str) -> str:
        """Strip ephemeral state from strategy content that becomes stale.

        The subagent is told not to include coordinates, HP/PP values, or
        battle state, but it often does anyway. This post-processes the output
        to remove lines containing these patterns.
        """
        cleaned = []
        for line in content.split("\n"):
            line_lower = line.lower().strip()
            # Skip lines that are purely about current battle state
            if any(phrase in line_lower for phrase in (
                "in wild battle", "in battle vs", "in battle with",
                "currently fighting", "current battle",
            )):
                logging.debug(f"KB: stripped ephemeral strategy line: {line.strip()}")
                continue
            cleaned.append(line)
        return "\n".join(cleaned).strip()

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
                    # Validate strategy content — strip ephemeral state
                    if section == "strategy":
                        content = self._sanitize_strategy(content)
                    if content:  # Re-check after sanitization
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
