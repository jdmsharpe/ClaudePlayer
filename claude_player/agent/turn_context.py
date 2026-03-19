"""Turn context builder: assembles user_content for each Claude API turn.

Extracted from game_agent.py to isolate the content-assembly and
injection-policy logic into a focused module.  The GameAgent creates
a TurnContextBuilder once in __init__ and calls build() each turn.
"""

import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from claude_player.agent.nav_planner import compute_nav
from claude_player.utils.world_map import WorldMap


class TurnContextBuilder:
    """Builds the user_content list sent to Claude each turn.

    Encapsulates injection-policy state (party/bag snapshots and refresh
    intervals) so GameAgent doesn't need to manage those fields directly.

    Args:
        memory_path: Path to saves/MEMORY.md.
        party_refresh_interval: Inject party context every N turns even if unchanged.
        bag_refresh_interval: Inject bag context every N turns even if unchanged.
    """

    def __init__(
        self,
        memory_path: str,
        party_refresh_interval: int = 10,
        bag_refresh_interval: int = 15,
    ):
        self.memory_path = memory_path

        # Party injection policy
        self._last_party_snapshot: Optional[tuple] = None
        self._last_party_inject_turn: int = 0
        self._party_refresh_interval = party_refresh_interval

        # Bag injection policy
        self._last_bag_snapshot: Optional[tuple] = None
        self._last_bag_inject_turn: int = 0
        self._bag_refresh_interval = bag_refresh_interval

        # Last NAV button sequence (e.g. "L64 U16") for fallback auto-execution
        self.last_nav_buttons: Optional[str] = None

    def build(
        self,
        captured_state: Dict[str, Any],
        *,
        game_state: Any,
        world_map: WorldMap,
        last_action_feedback: Optional[str],
        last_map_name: Optional[str],
        in_battle: bool,
        was_in_battle: bool,
        stuck_count: int,
        battle_stuck_count: int,
        consecutive_reversals: int,
        action_history: List[Tuple[int, str]],
    ) -> List[Dict[str, Any]]:
        """Assemble user_content for one API turn.

        Returns:
            List of content blocks (screenshot, text dicts) ready for the
            messages array.  Also updates internal injection-policy state.
        """
        screenshot = captured_state["screenshot"]
        spatial_data = captured_state.get("spatial_data")
        battle_data = captured_state.get("battle_data")
        menu_data = captured_state.get("menu_data")
        party_data = captured_state.get("party_data")
        bag_data = captured_state.get("bag_data")

        user_content: List[Dict[str, Any]] = [screenshot]

        # ── Movement feedback from last turn ──
        # Suppress during battle — position can't change, so "UNCHANGED" is noise
        if last_action_feedback and not in_battle:
            user_content.append({"type": "text", "text": last_action_feedback})

        # Memory is now injected as a cached system prompt block (see game_agent.py)
        # instead of a user message — saves tokens via cache-read pricing.

        # ── Main context: battle OR spatial ──
        if battle_data and battle_data.get("text"):
            user_content.append({"type": "text", "text": battle_data["text"]})
        elif spatial_data and spatial_data["text"]:
            spatial_text = self._build_spatial_text(
                spatial_data, world_map, game_state, last_map_name,
            )
            user_content.append({"type": "text", "text": spatial_text})

        # ── Menu context ──
        just_exited_battle = was_in_battle and not in_battle
        if menu_data and menu_data.get("text") and not just_exited_battle:
            user_content.append({"type": "text", "text": menu_data["text"]})

        # ── Party injection (change-based + periodic) ──
        self._maybe_inject_party(
            user_content, party_data, game_state.turn_count,
            was_in_battle=was_in_battle, in_battle=in_battle,
        )

        # ── Bag injection (change-based + periodic) ──
        self._maybe_inject_bag(
            user_content, bag_data, game_state.turn_count,
            was_in_battle=was_in_battle, in_battle=in_battle,
        )

        # ── Critical HP warning ──
        self._maybe_inject_critical_hp(
            user_content, party_data, in_battle, spatial_data,
        )

        # ── Stuck / battle-stuck / ping-pong warnings ──
        self._inject_stuck_warnings(
            user_content,
            stuck_count=stuck_count,
            battle_stuck_count=battle_stuck_count,
            consecutive_reversals=consecutive_reversals,
            action_history=action_history,
            in_battle=in_battle,
            spatial_data=spatial_data,
        )

        # ── Timing header (inserted at position 0) ──
        current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        header = f"Current time: {current_time_str}\nTurn #{game_state.turn_count}"
        if not game_state.identified_game:
            cartridge_title = captured_state.get("cartridge_title", "")
            if cartridge_title:
                header += f"\nCartridge: {cartridge_title}"
        user_content.insert(0, {"type": "text", "text": header})

        return user_content

    # ── Private helpers ──────────────────────────────────────────────

    def build_memory_block(self, turn_count: int, memory_turn: int) -> str:
        """Read MEMORY.md and wrap it in XML tags with staleness info.

        Called by game_agent to inject memory into the system prompt as a
        cached content block (cache-read pricing on unchanged turns).
        """
        if not os.path.exists(self.memory_path):
            return ""
        try:
            with open(self.memory_path, "r") as f:
                memory_text = f.read().strip()
        except OSError:
            return ""
        if not memory_text:
            return ""
        staleness = f" updated_at_turn={memory_turn}" if memory_turn > 0 else ""
        return f"<memory{staleness}>\n{memory_text}\n</memory>"

    def _build_spatial_text(
        self,
        spatial_data: Dict[str, Any],
        world_map: WorldMap,
        game_state: Any,
        last_map_name: Optional[str],
    ) -> str:
        """Build spatial context text with world map, goals, and NAV hint."""
        spatial_text = spatial_data["text"]
        # Prepend "entered from" note for orientation after warps
        if last_map_name:
            spatial_text = f"[Entered from: {last_map_name}]\n" + spatial_text

        # Prepend two-tier goal header
        strategic = game_state.strategic_goal
        tactical = game_state.tactical_goal
        if strategic or tactical:
            goal_header = f"STRATEGIC GOAL: {strategic or '(none)'}"
            if tactical:
                goal_header += f"\nTACTICAL GOAL: {tactical}"
            spatial_text = goal_header + "\n" + spatial_text

        # Append accumulated world map and run NAV pipeline
        map_id = spatial_data.get("map_number")
        player_pos = spatial_data.get("player_pos")
        if map_id is not None and player_pos is not None:
            world_map_text = world_map.render(
                map_id, player_pos,
                dead_end_zones=world_map.dead_ends.get(map_id, []),
            )
            if world_map_text:
                spatial_text += "\n" + world_map_text
            # World-map A* NAV: map graph BFS → compass fallback → inject hint
            # Use tactical goal for NAV routing (more precise map match),
            # with strategic goal as fallback for map-graph BFS.
            nav_goal = tactical or strategic or ""
            spatial_text = compute_nav(
                world_map, map_id, player_pos,
                goal_text=nav_goal,
                spatial_text=spatial_text,
                npc_positions=spatial_data.get("npc_abs_positions"),
                strategic_goal_text=strategic,
                current_turn=game_state.turn_count,
            )
            # Extract button sequence from NAV hint for fallback auto-execution
            nav_match = re.search(r'NAV\(map\): .+?: (.+?) —', spatial_text)
            self.last_nav_buttons = nav_match.group(1).strip() if nav_match else None
        return spatial_text

    def _maybe_inject_party(
        self,
        user_content: list,
        party_data: Optional[Dict],
        turn_count: int,
        was_in_battle: bool,
        in_battle: bool,
    ):
        """Inject party context on meaningful changes or periodically."""
        if not party_data or not party_data.get("text"):
            return
        current_snapshot = tuple(
            (m["hp"], m["status"]) for m in party_data["party"]
        )
        turns_since = turn_count - self._last_party_inject_turn
        just_left_battle = was_in_battle and not in_battle
        party_changed = current_snapshot != self._last_party_snapshot
        needs_healing = party_data.get("health", {}).get("needs_healing", False)
        periodic = turns_since >= self._party_refresh_interval

        if party_changed or just_left_battle or needs_healing or periodic:
            user_content.append({"type": "text", "text": party_data["text"]})
            self._last_party_inject_turn = turn_count
            if party_changed:
                logging.debug("Party context injected: state changed")

        self._last_party_snapshot = current_snapshot

    def _maybe_inject_bag(
        self,
        user_content: list,
        bag_data: Optional[Dict],
        turn_count: int,
        was_in_battle: bool,
        in_battle: bool,
    ):
        """Inject bag context on item change, post-battle, warnings, or periodically."""
        if not bag_data or not bag_data.get("text"):
            return
        current_snapshot = bag_data.get("snapshot")
        turns_since = turn_count - self._last_bag_inject_turn
        just_left_battle = was_in_battle and not in_battle
        bag_changed = current_snapshot != self._last_bag_snapshot
        has_warnings = bool(bag_data.get("assessment", {}).get("warnings"))
        periodic = turns_since >= self._bag_refresh_interval

        if bag_changed or just_left_battle or has_warnings or periodic:
            user_content.append({"type": "text", "text": bag_data["text"]})
            self._last_bag_inject_turn = turn_count
            if bag_changed:
                logging.debug("Bag context injected: inventory changed")

        self._last_bag_snapshot = current_snapshot

    @staticmethod
    def _maybe_inject_critical_hp(
        user_content: list,
        party_data: Optional[Dict],
        in_battle: bool,
        spatial_data: Optional[Dict],
    ):
        """Inject critical HP warning when party is nearly wiped."""
        if not party_data or in_battle:
            return
        if not spatial_data or (spatial_data.get("game_state") or {}).get("state") != "overworld":
            return
        health = party_data.get("health", {})
        hp_pct = health.get("total_hp_pct", 100)
        alive_count = health.get("alive", 6)
        total_count = health.get("total", 6)
        if hp_pct <= 25 and alive_count <= 2:
            fainted = total_count - alive_count
            user_content.append({
                "type": "text",
                "text": (
                    f"CRITICAL HP: {fainted}/{total_count} fainted, {hp_pct}% HP."
                    f" Heal soon — either head to a Pokémon Center or fight in battle"
                    f" to black out (free teleport to nearest Center)."
                )
            })
            logging.warning(f"CRITICAL HP: {fainted}/{total_count} fainted, {hp_pct}% HP")

    @staticmethod
    def _inject_stuck_warnings(
        user_content: list,
        stuck_count: int,
        battle_stuck_count: int,
        consecutive_reversals: int,
        action_history: List[Tuple[int, str]],
        in_battle: bool,
        spatial_data: Optional[Dict],
    ):
        """Inject escalating stuck / battle-stuck / ping-pong warnings."""
        # Overworld stuck
        if stuck_count >= 2:
            history_text = "\n".join(
                f"  T{turn}: {action}" for turn, action in action_history[-5:]
            ) or "  (none)"

            if stuck_count >= 5:
                user_content.append({
                    "type": "text",
                    "text": (
                        f"STUCK {stuck_count} turns! Failed:\n{history_text}\n"
                        "Try ONE untried: D16/L16/R16/U16, A (dialogue), B (cancel), S (menu)."
                    )
                })
                logging.warning(f"STUCK (CRITICAL): {stuck_count} turns")
            else:
                user_content.append({
                    "type": "text",
                    "text": (
                        f"STALLED {stuck_count} turns. Recent:\n{history_text}\n"
                        "Try untried direction (16 frames), A, or B."
                    )
                })
                logging.warning(f"STUCK: {stuck_count} turns at same position")

        # Battle stuck
        if in_battle and battle_stuck_count >= 4:
            history_text = "\n".join(
                f"  T{turn}: {action}" for turn, action in action_history[-5:]
            ) or "  (none)"

            if battle_stuck_count >= 7:
                user_content.append({
                    "type": "text",
                    "text": (
                        f"BATTLE STUCK {battle_stuck_count} turns!\n{history_text}\n"
                        "Try: B B B (back to main), then follow TIP, or A A A A A (advance text)."
                    )
                })
                logging.warning(f"BATTLE STUCK (CRITICAL): {battle_stuck_count} turns")
            else:
                user_content.append({
                    "type": "text",
                    "text": (
                        f"BATTLE STALLED {battle_stuck_count} turns. "
                        "Send B B to return to main menu, then follow TIP."
                    )
                })
                logging.warning(f"BATTLE STUCK: {battle_stuck_count} turns")

        # Direction reversal (ping-pong)
        if (consecutive_reversals >= 2
                and not in_battle
                and spatial_data and (spatial_data.get("game_state") or {}).get("state") == "overworld"):
            user_content.append({
                "type": "text",
                "text": (
                    f"PING-PONG WARNING: {consecutive_reversals} consecutive direction"
                    f" reversals. You are undoing your own progress."
                    f" Commit to a direction or try a perpendicular path."
                )
            })
            logging.warning(f"PING-PONG: {consecutive_reversals} consecutive reversals")
