# Exploration-First Hint Redesign

**Date:** 2026-03-23
**Status:** Approved

## Problem

The agent's MAP_HINTS system (~90 entries) acts as a walkthrough layer, providing directive routing instructions like "Head NORTH through Route 1 to Viridian City Mart." Combined with detailed STORY_PROGRESSION goal text, the agent follows prescribed routes rather than exploring areas and building a mental map organically.

## Goal

Shift the agent from walkthrough-following to exploration-driven navigation. The agent should:
- Receive vague directional nudges instead of turn-by-turn routing
- Explore unfamiliar areas before bee-lining to exits
- Build up location knowledge (via the existing KB location notes system) through discovery
- Still have efficient routing on familiar/revisited maps via accumulated world map data

## Design Decisions

- **Hint style:** Vague directional nudges for overworld, floor progression for dungeons (no warp coordinates anywhere)
- **Location notes:** Left as-is — these are the earned mental map the agent builds through exploration
- **Approach:** Hint rewrite + exploration encouragement (raised frontier threshold, persistent exploration nudge, system prompt reframing)

---

## Section 1: Hint Rewriting

### MAP_HINTS (event_flags.py)

Rewrite all ~90 entries following two templates:

**Overworld template** — vague direction + destination type, no routing:
```python
# BEFORE:
(0x039, 0x28): "Exit the lab and head NORTH through Route 1 to Viridian City Mart.",
# AFTER:
(0x039, 0x28): "There's a town with a Mart to the north.",

# BEFORE:
(-1, 0x33): "Viridian Forest is a winding maze — HEAD NORTH to reach ..."
# AFTER:
(-1, 0x33): "The forest exit is to the north. Explore to find the path through.",
```

**Dungeon template** — floor progression + general structure, no warp coords:
```python
# BEFORE:
(-2, 0x3C): "Reach B2F, then find W3(5,7) to exit to upper corridor → Route 4."
# AFTER:
(-2, 0x3C): "This cave has multiple floors. The exit to the east requires going deeper first.",

# BEFORE:
(-12, 0x6C): "Navigate Victory Road 1F — use HM04 Strength to push boulders. Find stairs to Victory Road 2F."
# AFTER:
(-12, 0x6C): "Victory Road has three floors. Push boulders with Strength to clear paths. Find stairs to progress upward."
```

### STORY_PROGRESSION goal text (event_flags.py)

Rewrite ~25 goal strings to be objective-focused, stripping routing:
```python
# BEFORE:
(0x039, "Got Oak's Parcel", "Go NORTH through Route 1 to Viridian City and pick up Oak's Parcel from the Poke Mart clerk")
# AFTER:
(0x039, "Got Oak's Parcel", "Pick up Oak's Parcel from the Viridian City Poké Mart")

# BEFORE:
(-1, "Through Viridian Forest", "Head NORTH from Viridian City through Route 2 and navigate Viridian Forest to reach Pewter City on the other side")
# AFTER:
(-1, "Through Viridian Forest", "Travel through Viridian Forest to reach Pewter City")
```

### Rewrite rules:
1. Never include cardinal directions as step-by-step routing (no "head NORTH through X then EAST to Y"). Vague directional context ("to the north", "the exit is east") is acceptable.
2. Never include warp indices or coordinates
3. Dungeon hints describe floor structure (how many floors, general progression direction)
4. Overworld hints mention the destination type ("a town", "a gym", "a cave") and vague direction
5. Keep hints under ~80 chars where possible (well under the 200-char tactical goal cap)
6. Strategic goals name the destination (must contain at least one map name recognizable by `world_map.map_names` for NAV target extraction) but not the route
7. Always use Pokémon/Poké with the accent (é)
8. **Validation step:** After rewriting, verify every STORY_PROGRESSION goal text contains at least one map name that exists in `world_map.map_names` — NAV depends on this for routing when tactical hints are vague

---

## Section 2: Exploration System Changes

### 2a. Raise frontier-first threshold (nav_planner.py)

```python
# BEFORE:
if world_map.frontier_ratio(map_id) > 0.3:
# AFTER:
if world_map.frontier_ratio(map_id) > 0.5:
```

Agent explores until it's seen at least half the map before NAV starts routing to warps. On familiar maps (replayed areas), frontier_ratio is already low so NAV kicks in immediately.

### 2b. Persistent exploration nudge (turn_context.py)

When `frontier_ratio > 0.5`, inject a message every turn. Call `world_map.frontier_ratio(map_id)` inside `_build_spatial_text()` and insert the nudge text into `spatial_text` between the goal header (prepended at line ~194) and the `[Entered from:]` line, so the model sees it immediately after reading its objectives:

```
⚑ EXPLORE: This area is 73% unexplored. Build your mental map before
routing to exits. Look for paths, items, NPCs, and warps you haven't visited.
```

- Naturally fades as agent explores — disappears once ratio drops below 0.5
- No new state tracking needed — uses existing `world_map.frontier_ratio()`
- **Suppressed when stuck_count >= 5** (already available as parameter in `_build_spatial_text`) — stuck recovery takes priority over exploration
- Will fire on first turn of every new map (frontier_ratio ~1.0) — this is intentional

### 2c. NAV pipeline adjustment (nav_planner.py)

1. **Strategic goal fallback already exists:** `compute_nav` already accepts `strategic_goal_text` and iterates `[tactical, strategic]` for substring matching (lines 206-215). No code change needed here — just verify it works correctly with the new vaguer tactical hints. The dependency chain is: vague tactical hint → NAV fails to extract target from tactical text → falls through to strategic goal → strategic goal must contain a recognizable map name (enforced by rewrite rule 8 in Section 1).

2. **Frontier-first already replaces NAV during exploration:** The existing frontier-first step (line 173-199) already fires when frontier_ratio exceeds the threshold and returns a `NAV(explore)` hint pointing to frontier tiles, short-circuiting the graph-routing step entirely. With the threshold raised to 0.5, this naturally covers the exploration phase. No pipeline restructuring needed — the current early-return behavior is correct.

### 2d. Frontier path cluster bias (world_map.py)

Modify `find_frontier_path()` to prefer frontier clusters over isolated gaps:

**Algorithm:**
1. Enumerate all frontier tiles (walkable tiles with ≥1 unexplored neighbor) — already computed by `frontier_ratio()`
2. For each frontier tile, compute a **density score**: count of unexplored tiles within Manhattan distance ≤ 3
3. Incorporate the density score into the A* heuristic as a tie-breaker: among frontier tiles at similar distances, prefer higher-density ones. Specifically, modify the existing `_h(x, y)` heuristic to subtract `density_score / 10` (small enough to not override distance, large enough to break ties)
4. Pre-compute a density map (dict of frontier tile → score) before the A* search begins. This is O(frontiers × neighborhood) which is bounded by map size (~40×40 max)

**Distance vs density trade-off:** A nearby low-density frontier (2 unexplored neighbors) should still be preferred over a distant high-density cluster, because the agent will naturally reach the cluster after clearing nearby tiles. The density score only breaks ties at similar distances — it does not override the distance heuristic.

---

## Section 3: System Prompt Changes (claude_interface.py)

Rewrite the `<navigation>` section to frame exploration as the primary strategy:

```
EXPLORATION: Your primary strategy is to explore each area and build
a mental map. Move into unexplored territory, discover warps, paths,
and landmarks. Your location notes grow from what you observe.

When ⚑ EXPLORE is active, focus on uncovering the map — don't rush
to exits. Visit corridors, check dead ends, talk to NPCs. The more
you explore, the better your future routing becomes.

NAV ASSISTANCE: Once you've explored enough of an area, NAV(map)
provides A* routing to your next destination. Trust NAV paths over
COMPASS bearings — COMPASS shows crow-flies direction, not walkable paths.
NEVER convert COMPASS block distances into frame inputs.

ROUTE PLANNING: When no NAV path is available, move in the general
compass direction using 1-tile steps (U16/D16/L16/R16) and re-evaluate
each turn.

DUNGEONS: Caves have multiple floors connected by ladder warps. Explore
each floor to discover the connections. The exit may require going
deeper before you can go up.
```

Key changes from current:
1. Exploration leads the section instead of NAV
2. Explains the ⚑ EXPLORE nudge so the model knows what it means
3. NAV framed as "assistance after exploring" rather than primary strategy
4. Dungeons: removed "follow NAV even when it leads away" — agent discovers this itself
5. Preserves critical COMPASS warning (don't convert distances to frames)

---

## Section 4: Edge Cases

1. **Stuck + unexplored map:** Suppress ⚑ EXPLORE when `stuck_count >= 5` — stuck recovery takes priority over exploration.

2. **Tiny maps (<15 tiles):** Already ignored by frontier system (`frontier_ratio` returns 0.0 for maps with <15 explored tiles). No exploration nudge fires.

3. **Returning to explored maps:** `frontier_ratio` already low on revisited maps → NAV routes normally, no nudge.

4. **Vague hints + NAV targeting dependency chain:** Vague tactical hint (e.g., "There's a gym in this city") → NAV fails to extract map name from tactical text → falls through to strategic goal (e.g., "Defeat Brock to earn the Boulder Badge" → matches "Pewter City") → routes successfully. If neither contains a map name → COMPASS/frontier fallback (acceptable during exploration). Rewrite rule 8 enforces that strategic goals always contain a recognizable map name.

5. **Token budget:** ⚑ EXPLORE line is ~20 tokens. System prompt `<navigation>` rewrite is roughly same length as current. No impact on ≥2048 token cache minimum.

---

## Files Changed

| File | Change |
|------|--------|
| `claude_player/utils/event_flags.py` | Rewrite ~90 MAP_HINTS entries (vague overworld / floor-progression dungeons) |
| `claude_player/utils/event_flags.py` | Rewrite ~25 STORY_PROGRESSION goal strings (objective-focused, no routing) |
| `claude_player/agent/nav_planner.py` | Raise frontier threshold 0.3 → 0.5 |
| `claude_player/agent/nav_planner.py` | Verify strategic goal fallback works with vaguer tactical hints (already implemented) |
| `claude_player/agent/turn_context.py` | Add persistent ⚑ EXPLORE nudge when frontier_ratio > 0.5 |
| `claude_player/agent/turn_context.py` | Suppress nudge when stuck_count >= 5 |
| `claude_player/utils/world_map.py` | Bias frontier pathfinding toward clusters (neighbor scoring) |
| `claude_player/interface/claude_interface.py` | Rewrite `<navigation>` section (exploration-first framing) |

## Unchanged

- Location notes (earned mental map) — unchanged
- World map accumulation, A*, tile protection — unchanged
- Warp cycling detection — unchanged
- Route caching — unchanged
- Battle system, menus, all other context — unchanged
- Knowledge base structure and memory manager — unchanged
