"""Categorized Knowledge Base for persistent agent memory.

Replaces the flat 80-line MEMORY.md with a multi-section system:
- PARTY: team strategy, matchup lessons (subjective only; RAM has facts)
- STRATEGY: current plan, priorities, next steps
- LESSONS: hard-won rules, failure patterns to avoid
- LOCATIONS: per-map notes (verified paths, dead ends, key NPCs)

Storage layout:
    saves/knowledge/
        party.md
        strategy.md
        lessons.md
        locations/
            pallet_town.md
            mt_moon_b1f.md
            ...

Injection strategy (cache-friendly):
- System prompt (cached): lessons + strategy (change rarely)
- User message (per-turn): current map's location file (small, changes on map transition)
- Party is injected into system prompt alongside lessons/strategy
"""

import logging
import os
import re
from typing import Dict, Optional

from claude_player.data.maps import MAP_NAMES

# Section files (relative to knowledge_dir)
SECTION_FILES = {
    "party": "party.md",
    "strategy": "strategy.md",
    "lessons": "lessons.md",
}

LOCATIONS_DIR = "locations"

# Line limits per section
SECTION_LIMITS = {
    "party": 15,
    "strategy": 20,
    "lessons": 20,
    "location": 30,
}


def _sanitize_map_name(name: str) -> str:
    """Convert map name to a safe filename.

    'Mt. Moon B1F' -> 'mt_moon_b1f'
    'Pokémon Tower 1F' -> 'pokemon_tower_1f'
    """
    name = name.lower()
    name = name.replace("é", "e").replace("'", "")
    name = re.sub(r"[^a-z0-9]+", "_", name)
    name = name.strip("_")
    return name


class KnowledgeBase:
    """Categorized persistent knowledge store.

    Args:
        saves_dir: Path to saves/ directory.
    """

    def __init__(self, saves_dir: str):
        self.knowledge_dir = os.path.join(saves_dir, "knowledge")
        self.locations_dir = os.path.join(self.knowledge_dir, LOCATIONS_DIR)
        os.makedirs(self.locations_dir, exist_ok=True)

    # ── Reading ──────────────────────────────────────────────────────

    def read_section(self, section: str) -> str:
        """Read a core section (party, strategy, lessons). Returns '' if missing."""
        if section not in SECTION_FILES:
            logging.warning(f"Unknown KB section: {section}")
            return ""
        path = os.path.join(self.knowledge_dir, SECTION_FILES[section])
        return self._read_file(path)

    def read_location(self, map_id: int) -> str:
        """Read location notes for a map. Returns '' if no notes exist."""
        name = self._map_filename(map_id)
        if not name:
            return ""
        path = os.path.join(self.locations_dir, f"{name}.md")
        return self._read_file(path)

    def read_location_by_name(self, map_name: str) -> str:
        """Read location notes by human-readable map name."""
        name = _sanitize_map_name(map_name)
        path = os.path.join(self.locations_dir, f"{name}.md")
        return self._read_file(path)

    # ── Writing ──────────────────────────────────────────────────────

    def write_section(self, section: str, content: str) -> int:
        """Write a core section, enforcing line limit. Returns line count."""
        if section not in SECTION_FILES:
            logging.warning(f"Unknown KB section: {section}")
            return 0
        path = os.path.join(self.knowledge_dir, SECTION_FILES[section])
        limit = SECTION_LIMITS.get(section, 30)
        return self._write_file(path, content, limit)

    def write_location(self, map_id: int, content: str) -> int:
        """Write location notes for a map. Returns line count."""
        name = self._map_filename(map_id)
        if not name:
            return 0
        path = os.path.join(self.locations_dir, f"{name}.md")
        limit = SECTION_LIMITS["location"]
        return self._write_file(path, content, limit)

    def write_location_by_name(self, map_name: str, content: str) -> int:
        """Write location notes by human-readable map name."""
        name = _sanitize_map_name(map_name)
        path = os.path.join(self.locations_dir, f"{name}.md")
        limit = SECTION_LIMITS["location"]
        return self._write_file(path, content, limit)

    # ── Injection helpers ────────────────────────────────────────────

    def build_cached_block(self, turn_count: int, memory_turn: int) -> str:
        """Build the system-prompt-cached memory block.

        Includes: party + strategy + lessons (changes rarely → cache-friendly).
        """
        parts = []
        for section in ("party", "strategy", "lessons"):
            text = self.read_section(section)
            if text:
                parts.append(f"## {section.upper()}\n{text}")

        if not parts:
            return ""

        staleness = f" updated_at_turn={memory_turn}" if memory_turn > 0 else ""
        body = "\n\n".join(parts)
        return f"<memory{staleness}>\n{body}\n</memory>"

    def build_location_block(self, map_id: int) -> str:
        """Build per-turn location context for injection into user message.

        Returns empty string if no notes exist for this map.
        """
        text = self.read_location(map_id)
        if not text:
            return ""
        map_name = MAP_NAMES.get(map_id, f"Map 0x{map_id:02X}")
        return f"<location_notes map=\"{map_name}\">\n{text}\n</location_notes>"

    # ── Migration ────────────────────────────────────────────────────

    def migrate_from_memory_md(self, memory_path: str) -> bool:
        """Parse existing MEMORY.md and split into KB sections.

        Handles the section headers from the memory subagent prompt:
        ## STATUS, ## PARTY, ## INVENTORY, ## MAP KNOWLEDGE, ## STRATEGY, ## LESSONS

        STATUS and INVENTORY are merged into strategy (they're tactical context).
        MAP KNOWLEDGE entries are split into per-map location files.

        Returns True if migration was performed.
        """
        if not os.path.exists(memory_path):
            return False

        with open(memory_path, "r") as f:
            content = f.read().strip()

        if not content:
            return False

        logging.info(f"Migrating {memory_path} to knowledge base")

        # Parse sections by ## headers
        sections: Dict[str, str] = {}
        current_section = None
        current_lines = []

        for line in content.split("\n"):
            if line.startswith("## "):
                if current_section:
                    sections[current_section] = "\n".join(current_lines).strip()
                current_section = line[3:].strip().upper()
                current_lines = []
            else:
                current_lines.append(line)

        if current_section:
            sections[current_section] = "\n".join(current_lines).strip()

        # Map old sections to new KB sections
        # PARTY → party (strip HP/level facts that RAM provides; keep subjective notes)
        if "PARTY" in sections:
            self.write_section("party", sections["PARTY"])
            logging.info(f"  Migrated PARTY ({len(sections['PARTY'].splitlines())} lines)")

        # STRATEGY + STATUS + INVENTORY → strategy
        strategy_parts = []
        for key in ("STATUS", "STRATEGY", "INVENTORY"):
            if key in sections:
                strategy_parts.append(sections[key])
        if strategy_parts:
            combined = "\n".join(strategy_parts)
            self.write_section("strategy", combined)
            logging.info(f"  Migrated STRATEGY ({len(combined.splitlines())} lines)")

        # LESSONS → lessons
        if "LESSONS" in sections:
            self.write_section("lessons", sections["LESSONS"])
            logging.info(f"  Migrated LESSONS ({len(sections['LESSONS'].splitlines())} lines)")

        # MAP KNOWLEDGE → per-map location files
        if "MAP KNOWLEDGE" in sections:
            self._migrate_map_knowledge(sections["MAP KNOWLEDGE"])

        # Remove old file after successful migration
        os.rename(memory_path, memory_path + ".bak")
        logging.info(f"  Renamed {memory_path} → {memory_path}.bak")

        return True

    def _migrate_map_knowledge(self, text: str):
        """Split MAP KNOWLEDGE lines into per-map location files.

        Lines typically look like:
        - B1F (24,22): DEAD END — UP/RIGHT/LEFT all blocked
        - Route 3: linear path east, watch for trainers
        """
        # Group lines by their map prefix
        map_lines: Dict[str, list] = {}
        for line in text.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Strip leading "- " bullet
            if line.startswith("- "):
                line = line[2:]

            # Try to extract map name from the start of the line
            # Patterns: "B1F (24,22):", "Route 3:", "Pewter City:"
            # For Mt. Moon floors, prefix with "Mt. Moon" if just "B1F"/"B2F"
            map_name = self._extract_map_name_from_line(line)
            if map_name:
                if map_name not in map_lines:
                    map_lines[map_name] = []
                map_lines[map_name].append(line)
            else:
                # Can't determine map — put in a "general" bucket
                if "_general" not in map_lines:
                    map_lines["_general"] = []
                map_lines["_general"].append(line)

        for map_name, lines in map_lines.items():
            if map_name == "_general":
                # Append to strategy instead
                existing = self.read_section("strategy")
                extra = "\n".join(lines)
                if existing:
                    self.write_section("strategy", existing + "\n" + extra)
                else:
                    self.write_section("strategy", extra)
            else:
                content = "\n".join(lines)
                self.write_location_by_name(map_name, content)
                logging.info(f"  Migrated location: {map_name} ({len(lines)} lines)")

    @staticmethod
    def _extract_map_name_from_line(line: str) -> Optional[str]:
        """Best-effort extraction of map name from a MAP KNOWLEDGE line."""
        # Match patterns like "B1F (", "B2F:", "Route 3:", "Pewter City:"
        # Floor-only names (B1F, B2F, 1F, 2F) are ambiguous — prefix with context
        m = re.match(r'^((?:Mt\.?\s*Moon\s+)?B?\d+F)\b', line, re.IGNORECASE)
        if m:
            floor = m.group(1)
            # If just "B1F" without "Mt. Moon", guess Mt. Moon (most common in early game)
            if not floor.lower().startswith("mt"):
                floor = f"Mt. Moon {floor}"
            return floor

        # Match "Route N", "City Name", etc. before a colon or parenthesis
        m = re.match(r'^([A-Z][A-Za-z\s.\'é]+?)[\s]*[\(:]', line)
        if m:
            return m.group(1).strip()

        return None

    # ── Deletion ─────────────────────────────────────────────────────

    def delete_all(self):
        """Delete the entire knowledge directory."""
        import shutil
        if os.path.exists(self.knowledge_dir):
            shutil.rmtree(self.knowledge_dir)
            logging.warning("Knowledge base DELETED")
        os.makedirs(self.locations_dir, exist_ok=True)

    def delete_section(self, section: str) -> bool:
        """Delete a single section file."""
        if section not in SECTION_FILES:
            return False
        path = os.path.join(self.knowledge_dir, SECTION_FILES[section])
        if os.path.exists(path):
            os.remove(path)
            logging.warning(f"KB section '{section}' deleted")
            return True
        return False

    # ── Internal ─────────────────────────────────────────────────────

    def _map_filename(self, map_id: int) -> Optional[str]:
        """Get sanitized filename for a map ID."""
        name = MAP_NAMES.get(map_id)
        if not name:
            return f"map_0x{map_id:02x}"
        return _sanitize_map_name(name)

    @staticmethod
    def _read_file(path: str) -> str:
        """Read a file, returning '' if missing."""
        if not os.path.exists(path):
            return ""
        try:
            with open(path, "r") as f:
                return f.read().strip()
        except OSError:
            return ""

    @staticmethod
    def _write_file(path: str, content: str, line_limit: int) -> int:
        """Write content to file, enforcing a line limit. Returns line count."""
        lines = content.strip().split("\n")
        if len(lines) > line_limit:
            lines = lines[:line_limit]
            content = "\n".join(lines)
            logging.warning(f"KB section truncated to {line_limit} lines: {path}")
        else:
            content = "\n".join(lines)

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content + "\n")

        return len(lines)
