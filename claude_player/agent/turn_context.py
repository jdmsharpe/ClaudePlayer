"""Turn context builder: assembles user_content for each Claude API turn.

Extracted from game_agent.py to isolate the content-assembly and
injection-policy logic into a focused module.  The GameAgent creates
a TurnContextBuilder once in __init__ and calls build() each turn.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from claude_player.agent.knowledge_base import KnowledgeBase
from claude_player.agent.nav_planner import compute_nav, NavResult
from claude_player.utils.world_map import WorldMap


class TurnContextBuilder:
    """Builds the user_content list sent to Claude each turn.

    Assembles screenshot, spatial/battle context, party, bag, stuck warnings,
    and KB location notes into the user message content blocks.

    The Knowledge Base is injected in two layers:
    - System prompt (cached): party + strategy + lessons via KnowledgeBase.build_cached_block()
    - User message (per-turn): current map's location notes via KnowledgeBase.build_location_block()

    Args:
        knowledge_base: Categorized KB instance for persistent agent memory.
    """

    _LOCATION_REFRESH_TURNS = 6
    _PARTY_REFRESH_OVERWORLD = 4
    _PARTY_REFRESH_BATTLE = 8
    _BAG_REFRESH_OVERWORLD = 10
    _BAG_REFRESH_BATTLE = 99999  # Effectively "change-only" in battle.

    def __init__(
        self,
        knowledge_base: KnowledgeBase,
        grid_in_prompt: bool = True,
    ):
        self.kb = knowledge_base
        self._grid_in_prompt = grid_in_prompt
        self._cross_map_loop_logged = False  # dedup: WARNING once, then DEBUG
        self._last_location_map_id: Optional[int] = None
        self._last_location_turn: int = 0
        self._last_party_inject_turn: int = 0
        self._last_bag_inject_turn: int = 0
        self._last_party_signature: Optional[Tuple[Any, ...]] = None
        self._last_bag_snapshot: Optional[Tuple[Any, ...]] = None
        self._last_nav_result: Optional[NavResult] = None

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
        turn_count = int(getattr(game_state, "turn_count", 0) or 0)

        user_content: List[Dict[str, Any]] = [screenshot]

        # ── Movement feedback from last turn ──
        # Suppress during battle — position can't change, so "UNCHANGED" is noise
        if last_action_feedback and not in_battle:
            user_content.append({"type": "text", "text": last_action_feedback})

        # ── Map-aware location notes from Knowledge Base ──
        # Injected as user message (not cached); refreshed on map changes and
        # periodic cadence while in overworld.
        # Core KB sections (party/strategy/lessons) are in the cached system prompt.
        if spatial_data and not in_battle:
            map_id = spatial_data.get("map_number")
            if map_id is not None and self._should_inject_location_notes(map_id, turn_count):
                location_block = self.kb.build_location_block(map_id)
                if location_block:
                    user_content.append({"type": "text", "text": location_block})

        # ── Main context: battle OR spatial ──
        if battle_data and battle_data.get("text"):
            user_content.append({"type": "text", "text": battle_data["text"]})
        elif spatial_data and spatial_data["text"]:
            spatial_text = self._build_spatial_text(
                spatial_data, world_map, game_state, last_map_name,
                stuck_count=stuck_count,
                world_map_text=captured_state.get("world_map_text"),
            )
            user_content.append({"type": "text", "text": spatial_text})

        # ── Menu context ──
        just_exited_battle = was_in_battle and not in_battle
        if menu_data and menu_data.get("text") and not just_exited_battle:
            user_content.append({"type": "text", "text": menu_data["text"]})

        # ── Screen text (dialogue, signs, notifications) ──
        text_data = captured_state.get("text_data")
        if text_data and text_data.get("text"):
            user_content.append({"type": "text", "text": text_data["text"]})

        # ── Party injection (change-aware + cadence refresh) ──
        if party_data and party_data.get("text"):
            party_signature = self._party_signature(party_data)
            if self._should_inject_party_full(party_signature, turn_count, in_battle):
                user_content.append({"type": "text", "text": party_data["text"]})
            else:
                compact_party = self._build_compact_party_status(party_data)
                if compact_party:
                    user_content.append({"type": "text", "text": compact_party})

        # ── Bag injection (change-aware + sparse refresh) ──
        if bag_data and bag_data.get("text"):
            bag_snapshot = bag_data.get("snapshot")
            if self._should_inject_bag_full(bag_snapshot, turn_count, in_battle):
                user_content.append({"type": "text", "text": bag_data["text"]})
            elif in_battle:
                compact_bag = self._build_compact_bag_status(bag_data)
                if compact_bag:
                    user_content.append({"type": "text", "text": compact_bag})

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

        # ── Cross-map loop warning ──
        if not in_battle:
            cross_map_warning = world_map.get_cross_map_stuck_warning()
            if cross_map_warning:
                user_content.append({"type": "text", "text": cross_map_warning})
                if not self._cross_map_loop_logged:
                    logging.warning(f"CROSS-MAP LOOP: detected by warp history")
                    self._cross_map_loop_logged = True
                else:
                    logging.debug(f"CROSS-MAP LOOP: still active")
            elif self._cross_map_loop_logged:
                # Loop resolved — reset so next occurrence gets WARNING again
                logging.info("CROSS-MAP LOOP: resolved")
                self._cross_map_loop_logged = False

        # ── Timing header (inserted at position 0) ──
        current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        header = f"Current time: {current_time_str}\nTurn #{game_state.turn_count}"
        if not game_state.identified_game:
            cartridge_title = captured_state.get("cartridge_title", "")
            if cartridge_title:
                header += f"\nCartridge: {cartridge_title}"
        user_content.insert(0, {"type": "text", "text": header})

        return user_content

    # ── Public helpers ───────────────────────────────────────────────

    def build_cached_kb_block(self, turn_count: int, memory_turn: int) -> str:
        """Build the cached system prompt block from Knowledge Base.

        Includes party + strategy + lessons (changes rarely → cache-friendly).
        Called by game_agent to inject into get_system_prompt().
        """
        return self.kb.build_cached_block(turn_count, memory_turn)

    # ── Private helpers ──────────────────────────────────────────────

    def _should_inject_location_notes(self, map_id: int, turn_count: int) -> bool:
        """Inject location notes on map transition or periodic refresh."""
        map_changed = self._last_location_map_id != map_id
        due = (turn_count - self._last_location_turn) >= self._LOCATION_REFRESH_TURNS
        if map_changed or due:
            self._last_location_map_id = map_id
            self._last_location_turn = turn_count
            return True
        return False

    @staticmethod
    def _party_signature(party_data: Dict[str, Any]) -> Tuple[Any, ...]:
        """Build a coarse signature for change-aware party injection."""
        party = party_data.get("party") or []
        health = party_data.get("health") or {}
        mons = tuple(
            (
                mon.get("name"),
                mon.get("level"),
                mon.get("status"),
            )
            for mon in party
        )
        hp_bucket = int(health.get("total_hp_pct", 0)) // 10
        return (
            mons,
            hp_bucket,
            health.get("alive", 0),
            health.get("fainted", 0),
            health.get("status_count", 0),
            bool(health.get("low_pp")),
            health.get("recommendation", ""),
        )

    def _should_inject_party_full(
        self,
        party_signature: Tuple[Any, ...],
        turn_count: int,
        in_battle: bool,
    ) -> bool:
        """Inject full party block when changed or refresh cadence is due."""
        refresh = self._PARTY_REFRESH_BATTLE if in_battle else self._PARTY_REFRESH_OVERWORLD
        changed = party_signature != self._last_party_signature
        due = (turn_count - self._last_party_inject_turn) >= refresh
        if changed or due:
            self._last_party_signature = party_signature
            self._last_party_inject_turn = turn_count
            return True
        return False

    def _should_inject_bag_full(
        self,
        bag_snapshot: Optional[Tuple[Any, ...]],
        turn_count: int,
        in_battle: bool,
    ) -> bool:
        """Inject full bag block when changed or refresh cadence is due."""
        refresh = self._BAG_REFRESH_BATTLE if in_battle else self._BAG_REFRESH_OVERWORLD
        changed = bag_snapshot != self._last_bag_snapshot
        due = (turn_count - self._last_bag_inject_turn) >= refresh
        if changed or due:
            self._last_bag_snapshot = bag_snapshot
            self._last_bag_inject_turn = turn_count
            return True
        return False

    @staticmethod
    def _build_compact_party_status(party_data: Dict[str, Any]) -> str:
        """Cheap fallback when full party block is skipped."""
        health = party_data.get("health") or {}
        if not health:
            return ""
        alive = int(health.get("alive", 0) or 0)
        fainted = int(health.get("fainted", 0) or 0)
        parts = [
            f"PARTY SNAPSHOT: {alive}/{alive + fainted} alive",
            f"Team HP:{health.get('total_hp_pct', '?')}%",
        ]
        status_count = int(health.get("status_count", 0) or 0)
        if status_count > 0:
            parts.append(f"{status_count} status")
        if health.get("low_pp"):
            parts.append("LOW PP")
        recommendation = health.get("recommendation")
        if recommendation:
            parts.append(f"HEAL: {recommendation}")
        return " | ".join(parts)

    @staticmethod
    def _build_compact_bag_status(bag_data: Dict[str, Any]) -> str:
        """Cheap fallback for battle turns when full bag block is skipped."""
        assessment = bag_data.get("assessment") or {}
        if not assessment:
            return ""
        return (
            "INVENTORY SNAPSHOT: "
            f"${assessment.get('money', 0)}"
            f" | Balls:{assessment.get('pokeballs', 0)}"
            f" | Medicine:{assessment.get('healing_items', 0)}"
            f" | Badges:{assessment.get('badge_count', 0)}/8"
        )

    def _build_spatial_text(
        self,
        spatial_data: Dict[str, Any],
        world_map: WorldMap,
        game_state: Any,
        last_map_name: Optional[str],
        stuck_count: int = 0,
        world_map_text: Optional[str] = None,
    ) -> str:
        """Build spatial context text with world map, goals, and NAV hint."""
        spatial_text = spatial_data["text"] if self._grid_in_prompt else spatial_data.get("api_text", spatial_data["text"])

        # Extract map info once for use in nudge and NAV blocks
        map_id = spatial_data.get("map_number")
        player_pos = spatial_data.get("player_pos")

        # Prepend "entered from" note for orientation after warps
        if last_map_name:
            spatial_text = f"[Entered from: {last_map_name}]\n" + spatial_text

        # Prepend goal header (strategic + tactical + side objectives)
        strategic = game_state.strategic_goal
        tactical = game_state.tactical_goal
        side_objs = game_state.side_objectives
        if strategic or tactical or side_objs:
            goal_header = f"STRATEGIC GOAL: {strategic or '(none)'}"
            if tactical:
                goal_header += f"\nTACTICAL GOAL: {tactical}"
            if side_objs:
                goal_header += f"\nSIDE OBJECTIVES: {' | '.join(side_objs)}"
            spatial_text = goal_header + "\n" + spatial_text

        # Inject exploration nudge when map is largely unexplored
        # Suppressed when stuck (stuck recovery takes priority)
        if map_id is not None and stuck_count < 5:
            fr = world_map.frontier_ratio(map_id)
            if fr > 0.5:
                pct = int(fr * 100)
                explore_nudge = (
                    f"⚑ EXPLORE: This area is {pct}% unexplored. Build your "
                    f"mental map before routing to exits. Look for paths, items, "
                    f"NPCs, and warps you haven't visited."
                )
                # Insert after goal header lines, before spatial data
                split_lines = spatial_text.split("\n")
                insert_idx = 0
                for i, line in enumerate(split_lines):
                    if line.startswith(("STRATEGIC GOAL:", "TACTICAL GOAL:", "SIDE OBJECTIVES:")):
                        insert_idx = i + 1
                    else:
                        if insert_idx > 0:
                            break
                split_lines.insert(insert_idx, explore_nudge)
                spatial_text = "\n".join(split_lines)

        # Append accumulated world map and run NAV pipeline
        if map_id is not None and player_pos is not None:
            # Use pre-rendered world map from captured_state if available
            if world_map_text is None:
                world_map_text = world_map.render_summary(
                    map_id, player_pos,
                    dead_end_zones=world_map.dead_ends.get(map_id, []),
                    current_turn=game_state.turn_count,
                ) or ""
            if world_map_text:
                spatial_text += "\n" + world_map_text
            # Inject marker summary only when render_summary didn't include it
            if not world_map_text:
                current_markers = world_map.markers.get(map_id, {})
                if current_markers:
                    marker_lines = " | ".join(
                        f"({x},{y}): {label}" for (x, y), label in sorted(current_markers.items())
                    )
                    spatial_text += f"\nMARKERS on this map: {marker_lines}"

            # World-map A* NAV: map graph BFS → compass fallback → inject hint
            # Use tactical goal for NAV routing (more precise map match),
            # with strategic goal as fallback for map-graph BFS.
            nav_goal = tactical or strategic or ""
            # Escalate pathfinding variance when stuck: 1→1, 2→2, 3+→3
            nav_variance = min(3, max(0, stuck_count))
            nav_result = compute_nav(
                world_map, map_id, player_pos,
                goal_text=nav_goal,
                spatial_text=spatial_text,
                npc_positions=spatial_data.get("npc_abs_positions"),
                strategic_goal_text=strategic,
                current_turn=game_state.turn_count,
                variance=nav_variance,
            )
            spatial_text = nav_result.spatial_text
            # Store nav result on the builder so game_agent can read it
            self._last_nav_result = nav_result
        return spatial_text

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
