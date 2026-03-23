# Exploration-First Hint Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shift the agent from walkthrough-following to exploration-driven navigation by rewriting hints to be vague nudges, raising the frontier exploration threshold, adding a persistent exploration nudge, improving frontier pathfinding, and reframing the system prompt.

**Architecture:** The MAP_HINTS and STORY_PROGRESSION text are rewritten in-place (no structural changes). The NAV pipeline's frontier threshold is raised from 0.3→0.5. A new ⚑ EXPLORE nudge is injected in turn_context.py when frontier_ratio is high. The system prompt's `<navigation>` section is rewritten to prioritize exploration. Frontier pathfinding gains cluster-density bias.

**Tech Stack:** Python 3.12, no new dependencies

**Spec:** `docs/superpowers/specs/2026-03-23-exploration-first-hints-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `claude_player/utils/event_flags.py` | Modify | Rewrite ~120 MAP_HINTS entries + ~25 STORY_PROGRESSION goal strings |
| `claude_player/agent/nav_planner.py` | Modify | Raise `_FRONTIER_RATIO_THRESHOLD` from 0.3 to 0.5 |
| `claude_player/agent/turn_context.py` | Modify | Add persistent ⚑ EXPLORE nudge in `_build_spatial_text()` |
| `claude_player/utils/world_map.py` | Modify | Add frontier cluster density scoring to `find_frontier_path()` |
| `claude_player/interface/claude_interface.py` | Modify | Rewrite `<navigation>` section for exploration-first framing |

---

### Task 1: Rewrite STORY_PROGRESSION goal strings

**Files:**
- Modify: `claude_player/utils/event_flags.py:60-92` (STORY_PROGRESSION list)

The ~25 goal strings (third element of each tuple) need to be objective-focused, stripping routing directions. Each must still contain at least one map name recognizable by `world_map.map_names` for NAV target extraction.

- [ ] **Step 1: Rewrite all STORY_PROGRESSION goal strings**

Replace the goal text (third element) in each tuple. Rules:
- Strip all step-by-step routing ("Head NORTH through X", "Go SOUTH from Y via Z")
- Keep the objective and destination name ("Pick up Oak's Parcel from the Viridian City Poké Mart")
- Every goal must contain at least one recognizable map/location name
- Use Pokémon/Poké with accent (é)

```python
STORY_PROGRESSION: List[Tuple[int, str, str]] = [
    (0x027, "Oak appeared in Pallet",              "Go outside and walk into the tall grass on Route 1 to trigger Oak's appearance"),
    (0x022, "Got starter Pokémon",                 "Choose a starter Pokémon from Oak's Lab in Pallet Town"),
    (0x023, "Battled rival in Oak's Lab",          "Walk toward the exit of Oak's Lab — your rival will challenge you automatically"),
    (0x039, "Got Oak's Parcel",                    "Pick up Oak's Parcel from the Viridian City Poké Mart"),
    (0x025, "Got Pokédex",                         "Deliver the parcel to Prof. Oak in his lab in Pallet Town to receive the Pokédex"),
    (  -1,  "Through Viridian Forest",             "Travel through Viridian Forest to reach Pewter City"),
    (0x077, "Beat Brock",                          "Defeat Brock at Pewter City Gym to earn the Boulder Badge"),
    (  -2,  "Through Mt. Moon",                    "Navigate through Mt. Moon to reach Cerulean City"),
    (0x0BF, "Beat Misty",                          "Defeat Misty at Cerulean City Gym to earn the Cascade Badge"),
    (  -3,  "Got S.S. Ticket from Bill",           "Visit Bill's House at the end of Route 25 and help him to receive the S.S. Ticket"),
    (0x5E0, "Got HM01 Cut",                        "Board the S.S. Anne in Vermilion City and get HM01 Cut from the captain"),
    (0x167, "Beat Lt. Surge",                      "Teach Cut to a Pokémon, clear the tree at Vermilion City Gym, and defeat Lt. Surge for the Thunder Badge"),
    (  -4,  "Got Silph Scope from Rocket Hideout", "Find the Rocket Hideout beneath Celadon City Game Corner and defeat Giovanni to get the Silph Scope"),
    (0x1A9, "Beat Erika",                          "Defeat Erika at Celadon City Gym to earn the Rainbow Badge"),
    (0x128, "Got Poké Flute",                      "Climb Pokémon Tower in Lavender Town, use the Silph Scope on the ghost, and rescue Mr. Fuji for the Poké Flute"),
    (0x259, "Beat Koga",                           "Defeat Koga at Fuchsia City Gym to earn the Soul Badge"),
    (0x880, "Got HM03 Surf",                       "Find the Secret House in Fuchsia City Safari Zone to receive HM03 Surf"),
    (  -5,  "Got Gold Teeth",                      "Find the Gold Teeth item in Fuchsia City Safari Zone"),
    (  -6,  "Got HM04 Strength from Warden",       "Give the Gold Teeth to the Safari Zone Warden in Fuchsia City to receive HM04 Strength"),
    (  -7,  "Bought Celadon Dept Store drink",     "Buy a Fresh Water, Soda Pop, or Lemonade from the Celadon City Dept Store rooftop vending machines"),
    (  -8,  "Cleared Silph Co.",                   "Enter Silph Co. in Saffron City and defeat Giovanni to liberate the building"),
    (0x361, "Beat Sabrina",                        "Defeat Sabrina at Saffron City Gym to earn the Marsh Badge"),
    (  -9,  "Surfed to Cinnabar Island",           "Use Surf to reach Cinnabar Island"),
    ( -10,  "Got Secret Key from Pokémon Mansion", "Find the Secret Key in the basement of Pokémon Mansion on Cinnabar Island"),
    (0x299, "Beat Blaine",                         "Defeat Blaine at Cinnabar Island Gym (unlocked with the Secret Key) to earn the Volcano Badge"),
    (0x051, "Beat Giovanni (Viridian Gym)",        "Defeat Giovanni at Viridian City Gym to earn the Earth Badge"),
    ( -11,  "Reached Victory Road",                "Travel through Route 22 and Route 23 to reach the Victory Road entrance"),
    ( -12,  "Through Victory Road",                "Navigate Victory Road's three floors using Strength to push boulders and reach Indigo Plateau"),
    (0x8FE, "Beat Lance (Elite Four)",             "Defeat all four Elite Four members at Indigo Plateau: Lorelei, Bruno, Agatha, and Lance"),
    (0x901, "Beat Champion",                       "Defeat Blue (your rival) at Indigo Plateau to become Pokémon Champion"),
    (0x8C1, "Caught Mewtwo",                       "Enter Cerulean Cave and catch Mewtwo"),
]
```

- [ ] **Step 2: Verify all goals contain a map name**

Manually check each rewritten goal contains at least one of: Pallet Town, Route 1, Viridian City, Viridian Forest, Pewter City, Mt. Moon, Cerulean City, Route 25, Vermilion City, Celadon City, Lavender Town, Fuchsia City, Safari Zone, Saffron City, Cinnabar Island, Pokémon Mansion, Victory Road, Indigo Plateau, Cerulean Cave, or their sublocations (S.S. Anne, Silph Co., etc.).

- [ ] **Step 3: Commit**

```bash
git add claude_player/utils/event_flags.py
git commit -m "Rewrite STORY_PROGRESSION goals to be objective-focused"
```

---

### Task 2: Rewrite MAP_HINTS entries

**Files:**
- Modify: `claude_player/utils/event_flags.py:208-361` (MAP_HINTS dict)

Rewrite all ~90 entries following the overworld/dungeon templates from the spec. This is the largest single change.

- [ ] **Step 1: Rewrite MAP_HINTS — early game (Oak through Brock)**

Lines 208-245. Apply overworld template: vague direction + destination type, no step-by-step routing.

```python
MAP_HINTS: Dict[Tuple[int, int], str] = {
    # Oak appeared (0x027)
    (0x027, 0x26): "Head downstairs to leave the house.",
    (0x027, 0x25): "Exit through the front door.",
    (0x027, 0x00): "Walk toward Route 1 to the north.",
    (0x027, 0x0C): "Walk into the tall grass — Oak will appear.",
    # Got starter (0x022)
    (0x022, 0x00): "Oak's Lab is in the south part of town.",
    (0x022, 0x28): "Choose a starter Pokémon from the table.",
    # Battled rival (0x023)
    (0x023, 0x28): "Walk toward the exit — your rival will challenge you automatically.",
    # Got Oak's Parcel (0x039)
    (0x039, 0x28): "The Poké Mart is in a town to the north.",
    (0x039, 0x00): "There's a town to the north with a Poké Mart.",
    (0x039, 0x0C): "Keep heading toward the town ahead.",
    (0x039, 0x01): "Enter the Poké Mart to pick up the parcel.",
    (0x039, 0x2A): "Talk to the shopkeeper to get the parcel.",
    # Got Pokédex (0x025)
    (0x025, 0x2A): "Oak's Lab is back to the south.",
    (0x025, 0x01): "Head back toward Pallet Town.",
    (0x025, 0x0C): "Continue south toward Pallet Town.",
    (0x025, 0x00): "Deliver the parcel to Prof. Oak in his lab.",
    (0x025, 0x28): "Talk to Prof. Oak to deliver the parcel.",
    # Through Viridian Forest (-1)
    (  -1,  0x00): "There's a forest to the north between here and Pewter City.",
    (  -1,  0x0C): "Continue toward the city ahead.",
    (  -1,  0x01): "The forest entrance is to the north.",
    (  -1,  0x0D): "Continue toward Pewter City.",
    (  -1,  0x32): "Enter the forest ahead.",
    (  -1,  0x33): "The forest exit is to the north. Explore to find the path through.",
    (  -1,  0x2F): "Pewter City is just ahead.",
    # Beat Brock (0x077)
    (0x077, 0x02): "The gym is somewhere in this city. Explore to find it.",
    (0x077, 0x36): "Defeat Brock at the back of the gym.",
```

- [ ] **Step 2: Rewrite MAP_HINTS — Mt. Moon and Cerulean**

Lines 246-261. Apply dungeon template for Mt. Moon (floor progression, no warp coords) and overworld template for Cerulean.

```python
    # Through Mt. Moon (-2)
    (  -2,  0x02): "Stock up on Potions and Antidotes before heading east — there's a cave ahead with no shops.",
    (  -2,  0x0E): "The cave entrance is at the east end of this route.",
    (  -2,  0x44): "Rest at the Pokémon Center before entering the cave.",
    (  -2,  0x3B): "This cave has multiple floors. Explore to find stairs going down.",
    (  -2,  0x3C): "This floor connects deeper into the cave. Explore to find the way down.",
    (  -2,  0x3D): "The exit to Route 4 is on this floor. Explore to find it.",
    (  -2,  0x0F): "Cerulean City is to the east.",
    # Beat Misty (0x0BF)
    (0x0BF, 0x03): "The gym is in the northeast part of this city.",
    (0x0BF, 0x41): "Defeat Misty at the back of the gym.",
    # Got S.S. Ticket from Bill (-3)
    (  -3,  0x03): "Bill's House is to the north and east, past Route 24 and Route 25.",
    (  -3,  0x23): "Continue east toward Bill's House.",
    (  -3,  0x24): "Bill's House is at the eastern end of this route.",
    (  -3,  0x58): "Talk to Bill and help him to receive the S.S. Ticket.",
```

- [ ] **Step 3: Rewrite MAP_HINTS — Vermilion through Celadon**

Lines 262-284. Overworld templates for routes and cities; dungeon template for Rocket Hideout.

```python
    # Got HM01 Cut (0x5E0)
    (0x5E0, 0x03): "Vermilion City is to the south.",
    (0x5E0, 0x10): "Continue south toward Vermilion City.",
    (0x5E0, 0x11): "Continue south toward Vermilion City.",
    (0x5E0, 0x05): "The S.S. Anne is docked to the southeast. Show your ticket to board.",
    (0x5E0, 0x5E): "Show the S.S. Ticket to board the ship.",
    (0x5E0, 0x5F): "Explore the ship to find the captain's cabin.",
    (0x5E0, 0x65): "Talk to the captain to receive HM01 Cut.",
    # Beat Lt. Surge (0x167)
    (0x167, 0x05): "The gym is in the south part of town. Use Cut on the tree blocking the entrance.",
    (0x167, 0x5C): "Search trash cans to find the switches that unlock the door, then defeat Lt. Surge.",
    # Got Silph Scope (-4)
    (  -4,  0x04): "Celadon City is to the west.",
    (  -4,  0x05): "Celadon City is to the north and west.",
    (  -4,  0x06): "Stock up before going underground. The Game Corner hides a secret entrance.",
    (  -4,  0x87): "There's a hidden entrance to the Rocket Hideout somewhere in this building.",
    (  -4,  0xC7): "The hideout has multiple basement floors. Explore to find stairs going down.",
    (  -4,  0xC8): "Continue deeper into the hideout.",
    (  -4,  0xC9): "Continue deeper — the boss is on the lowest floor.",
    (  -4,  0xCA): "Defeat Giovanni to receive the Silph Scope.",
    # Beat Erika (0x1A9)
    (0x1A9, 0x06): "The gym is on the west side of town. Look for a way in from behind.",
    (0x1A9, 0x86): "Defeat Erika at the back of the gym.",
```

- [ ] **Step 4: Rewrite MAP_HINTS — Pokémon Tower through Safari Zone**

Lines 285-311. Dungeon template for Pokémon Tower (floor progression); overworld for Fuchsia/Safari.

```python
    # Got Poké Flute (0x128)
    (0x128, 0x04): "Stock up before climbing — Pokémon Tower has no shop. The tower is in this town.",
    (0x128, 0x8E): "Climb through the tower. Stairs lead up to higher floors.",
    (0x128, 0x8F): "Continue climbing through the tower.",
    (0x128, 0x90): "Continue climbing through the tower.",
    (0x128, 0x91): "Continue climbing through the tower.",
    (0x128, 0x92): "Continue climbing through the tower.",
    (0x128, 0x93): "Continue climbing through the tower.",
    (0x128, 0x94): "Use the Silph Scope near the ghost, defeat it, then rescue Mr. Fuji at the top for the Poké Flute.",
    # Beat Koga (0x259)
    (0x259, 0x07): "The gym is in this city. Explore to find it.",
    (0x259, 0x9D): "The gym has invisible walls. Navigate carefully and defeat Koga.",
    # Got HM03 Surf (0x880)
    (0x880, 0x07): "The Safari Zone entrance is in this city.",
    (0x880, 0x9C): "Explore the Safari Zone to find the Secret House.",
    (0x880, 0xDC): "Explore further into the Safari Zone.",
    (0x880, 0xDB): "The Secret House is somewhere in this area.",
    (0x880, 0xDE): "Talk to the person inside to receive HM03 Surf.",
    # Got Gold Teeth (-5)
    (  -5,  0x9C): "The Gold Teeth are somewhere on the ground in the Safari Zone.",
    (  -5,  0xDC): "Explore further into the Safari Zone.",
    (  -5,  0xDB): "Search this area for the Gold Teeth item on the ground.",
    (  -5,  0x07): "Enter the Safari Zone to find the Gold Teeth.",
    # Got HM04 Strength from Warden (-6)
    (  -6,  0x9C): "The Warden's House is in town, near the Pokémon Center.",
    (  -6,  0x07): "The Warden's House is near the Pokémon Center in this city.",
    (  -6,  0x9B): "Give the Gold Teeth to the Warden to receive HM04 Strength.",
```

- [ ] **Step 5: Rewrite MAP_HINTS — Celadon drink through Silph Co.**

Lines 312-329. Overworld for shops/travel; dungeon template for Silph Co.

```python
    # Bought Celadon drink (-7)
    (  -7,  0x07): "Head toward Celadon City to visit the Dept Store.",
    (  -7,  0x06): "The Dept Store is the large building in this city. Take the elevator to the rooftop.",
    (  -7,  0x7A): "Take the elevator to the rooftop.",
    (  -7,  0x7E): "Buy a drink from the vending machines — needed to enter Saffron City.",
    # Cleared Silph Co. (-8)
    (  -8,  0x06): "Saffron City is to the east — show a drink to the gate guard to enter.",
    (  -8,  0x0A): "Stock up before entering Silph Co. — it's a long multi-floor building with no shop. Silph Co. is the large tower in the center of this city.",
    (  -8,  0xB5): "Silph Co. has many floors. Explore to find the Lift Key and work your way up.",
    (  -8,  0xCF): "Explore this floor. The Lift Key may be here.",
    (  -8,  0xD0): "Continue exploring Silph Co.",
    (  -8,  0xD1): "Continue exploring Silph Co.",
    (  -8,  0xD2): "Continue exploring Silph Co.",
    (  -8,  0xD3): "Continue exploring Silph Co.",
    (  -8,  0xD4): "Defeat Giovanni somewhere on this floor to liberate the building.",
    # Beat Sabrina (0x361)
    (0x361, 0x0A): "The gym is in this city. It has teleport pads.",
    (0x361, 0xB2): "Use the teleport pads to navigate the gym and defeat Sabrina.",
```

- [ ] **Step 6: Rewrite MAP_HINTS — Cinnabar through Elite Four**

Lines 330-361. Overworld for routes; dungeon template for Pokémon Mansion and Victory Road.

```python
    # Surfed to Cinnabar Island (-9)
    (  -9,  0x00): "Use Surf to head south toward Cinnabar Island.",
    (  -9,  0x07): "Cinnabar Island is to the south via the sea routes.",
    (  -9,  0x1E): "Continue south through the sea route.",
    (  -9,  0x1F): "Continue west through the sea route.",
    (  -9,  0x20): "Continue south toward Cinnabar Island.",
    # Got Secret Key from Pokémon Mansion (-10)
    ( -10,  0x08): "Stock up before entering — Pokémon Mansion has no shop. The Mansion is the ruined building in this town.",
    ( -10,  0xA5): "The Mansion has multiple floors. The Secret Key is in the basement. Explore to find stairs.",
    ( -10,  0xD6): "Continue exploring — find stairs to higher or lower floors.",
    ( -10,  0xD7): "Continue exploring — the basement is accessible from this floor.",
    ( -10,  0xD8): "Search this floor for the Secret Key on the ground.",
    # Beat Blaine (0x299)
    (0x299, 0x08): "The gym is now unlocked with the Secret Key.",
    (0x299, 0xA6): "Answer the quiz questions or fight trainers, then defeat Blaine.",
    # Beat Giovanni Viridian Gym (0x051)
    (0x051, 0x01): "Viridian City Gym is now open. Explore to find it.",
    (0x051, 0x2D): "Navigate the gym's puzzle and defeat Giovanni for the Earth Badge.",
    # Reached Victory Road (-11)
    ( -11,  0x01): "Route 22 is to the west, leading toward the Pokémon League.",
    ( -11,  0x21): "Continue north toward the Pokémon League.",
    ( -11,  0x22): "Badge checkers verify your badges along this route. Keep going north.",
    # Through Victory Road (-12)
    ( -12,  0x22): "Stock up on Full Restores and Revives before entering — no shops inside Victory Road.",
    ( -12,  0x6C): "Victory Road has three floors. Use Strength to push boulders and find stairs to progress.",
    ( -12,  0xC2): "Continue through Victory Road. Find stairs to the next floor.",
    ( -12,  0xC6): "Continue through Victory Road toward the exit.",
    ( -12,  0x09): "You've reached Indigo Plateau! The Pokémon League is inside.",
    # Beat Lance / Elite Four (0x8FE)
    (0x8FE, 0x09): "Heal and stock up at the Pokémon Center and Mart before facing the Elite Four.",
    (0x8FE, 0xAE): "Heal at the Pokémon Center, then face the Elite Four: Lorelei, Bruno, Agatha, Lance.",
}
```

- [ ] **Step 7: Commit**

```bash
git add claude_player/utils/event_flags.py
git commit -m "Rewrite MAP_HINTS to vague directional nudges"
```

---

### Task 3: Raise frontier threshold in nav_planner.py

**Files:**
- Modify: `claude_player/agent/nav_planner.py:173`

- [ ] **Step 1: Change the threshold constant**

```python
# BEFORE (line 173):
_FRONTIER_RATIO_THRESHOLD = 0.3
# AFTER:
_FRONTIER_RATIO_THRESHOLD = 0.5
```

- [ ] **Step 2: Verify no other code references the old 0.3 value**

Search for `0.3` or `FRONTIER_RATIO` in the codebase to confirm this is the only place.

- [ ] **Step 3: Commit**

```bash
git add claude_player/agent/nav_planner.py
git commit -m "Raise frontier-first threshold from 0.3 to 0.5"
```

---

### Task 4: Add persistent exploration nudge in turn_context.py

**Files:**
- Modify: `claude_player/agent/turn_context.py:169-232` (`_build_spatial_text`)

- [ ] **Step 1: Add the exploration nudge injection**

Insert after the goal header is prepended (line 194) but before the world map / NAV section (line 196). The nudge fires when `frontier_ratio > 0.5` and `stuck_count < 5`.

In `_build_spatial_text`, modify the goal header block (lines 188-194) to insert the nudge between the goal header and the rest of spatial_text:

```python
        # Prepend goal header (strategic + tactical + side objectives)
        strategic = game_state.strategic_goal
        tactical = game_state.tactical_goal
        side_objs = game_state.side_objectives
        map_id = spatial_data.get("map_number")
        player_pos = spatial_data.get("player_pos")

        if strategic or tactical or side_objs:
            goal_header = f"STRATEGIC GOAL: {strategic or '(none)'}"
            if tactical:
                goal_header += f"\nTACTICAL GOAL: {tactical}"
            if side_objs:
                goal_header += f"\nSIDE OBJECTIVES: {' | '.join(side_objs)}"
            spatial_text = goal_header + "\n" + spatial_text

        # Inject exploration nudge between goal header and spatial context
        if map_id is not None and stuck_count < 5:
            fr = world_map.frontier_ratio(map_id)
            if fr > 0.5:
                pct = int(fr * 100)
                explore_nudge = (
                    f"⚑ EXPLORE: This area is {pct}% unexplored. Build your "
                    f"mental map before routing to exits. Look for paths, items, "
                    f"NPCs, and warps you haven't visited.\n"
                )
                # Insert after goal header lines, before [Entered from:] / spatial data
                lines = spatial_text.split("\n", 1)
                # Find where goal header ends (after SIDE OBJECTIVES or TACTICAL/STRATEGIC line)
                header_end = 0
                for i, line in enumerate(spatial_text.split("\n")):
                    if line.startswith(("STRATEGIC GOAL:", "TACTICAL GOAL:", "SIDE OBJECTIVES:")):
                        header_end = i + 1
                    else:
                        break
                all_lines = spatial_text.split("\n")
                all_lines.insert(header_end, explore_nudge.rstrip())
                spatial_text = "\n".join(all_lines)
```

Note: `map_id` and `player_pos` are now extracted once at the top and reused in the existing NAV block below (remove the duplicate extraction at the old line 197).

- [ ] **Step 2: Remove duplicate map_id/player_pos extraction**

The old lines 197-198 (`map_id = spatial_data.get("map_number")` and `player_pos = spatial_data.get("player_pos")`) are now redundant — they were moved up in Step 1. Remove them and update the `if map_id is not None and player_pos is not None:` guard at the old line 199 to use the already-extracted variables.

- [ ] **Step 3: Commit**

```bash
git add claude_player/agent/turn_context.py
git commit -m "Add persistent exploration nudge when frontier_ratio > 0.5"
```

---

### Task 5: Add frontier cluster density bias in world_map.py

**Files:**
- Modify: `claude_player/utils/world_map.py:888-950` (`find_frontier_path`)

- [ ] **Step 1: Add density scoring before the A* search**

After the frontiers set is computed (line 923) and before the heuristic function (line 932), add a density map:

```python
        # Pre-compute frontier density: count unexplored tiles within Manhattan dist 3
        _DENSITY_RADIUS = 3
        density: Dict[Tuple[int, int], int] = {}
        for fx, fy in frontiers:
            count = 0
            for ddx in range(-_DENSITY_RADIUS, _DENSITY_RADIUS + 1):
                for ddy in range(-_DENSITY_RADIUS, _DENSITY_RADIUS + 1):
                    if abs(ddx) + abs(ddy) > _DENSITY_RADIUS:
                        continue
                    nb = (fx + ddx, fy + ddy)
                    if nb not in tile_map:  # unexplored
                        count += 1
                density[(fx, fy)] = count
```

- [ ] **Step 2: Modify the heuristic to use density as tie-breaker**

Update `_h()` to incorporate density. Density is a small bonus (divided by 10) so it breaks ties without overriding distance:

```python
        def _h(x: int, y: int) -> float:
            """Heuristic: direction bias + density tie-breaker for frontier tiles."""
            bonus = 0
            if bias_dx or bias_dy:
                dx = x - start[0]
                dy = y - start[1]
                bonus = dx * bias_dx + dy * bias_dy
            # Density bonus: prefer frontier tiles with more unexplored neighbors
            d_bonus = density.get((x, y), 0) / 10
            return -min(bonus, 10) - d_bonus
```

- [ ] **Step 3: Commit**

```bash
git add claude_player/utils/world_map.py
git commit -m "Bias frontier pathfinding toward high-density unexplored clusters"
```

---

### Task 6: Rewrite system prompt navigation section

**Files:**
- Modify: `claude_player/interface/claude_interface.py:63-74` (`<navigation>` section)

- [ ] **Step 1: Replace the `<navigation>` section**

Replace lines 63-74 with the exploration-first framing. Preserve the COMPASS warning, stuck recovery, and connections guidance — but lead with exploration.

```python
<navigation>
EXPLORATION: Your primary strategy is to explore each area and build a mental map. Move into unexplored territory, discover warps, paths, and landmarks. Your location notes grow from what you observe. When ⚑ EXPLORE is active, focus on uncovering the map — don't rush to exits. Visit corridors, check dead ends, talk to NPCs. The more you explore, the better your future routing becomes.
NAV ASSISTANCE: Once you've explored enough of an area, NAV(map) provides A* routing to your next destination. Trust NAV paths over COMPASS bearings — COMPASS shows crow-flies direction and distance to off-screen exits, NOT walkable paths. NEVER convert compass block distances into frame inputs (e.g. "6 LEFT, 3 DOWN" does NOT mean "L96 D48"). If NAV(map) is present, follow it. If only COMPASS is available, move in the general compass direction using 1-tile steps (U16/D16/L16/R16) and re-evaluate each turn.
STUCK RECOVERY: If your position is unchanged after a move, you walked into a wall. Do NOT retry the same direction. Try perpendicular directions or follow NAV(map) detour suggestions. If STUCK warnings appear, you are looping — pick a direction you have NOT tried in the last 5 turns.
WARP PATHING: Warps often require indirect paths through corridors. A warp that is "3 DOWN, 6 LEFT" may require going UP first. Trust NAV(map) for warp routing.
DEAD ENDS: If the context says "dead-end" or "looping", leave immediately in the suggested direction.
EXPLORED MAP: The large map shows all tiles you've visited with @ as your position. Use it to identify corridors you haven't explored yet. Head toward unexplored edges to discover new paths.
DUNGEONS: Caves have multiple floors connected by ladder warps. Explore each floor to discover the connections. The exit may require going deeper before you can go up.
CONNECTIONS: Map edges marked in COMPASS as connections are reached by walking off the map edge — no warp tile needed.
GOALS: Three tiers. STRATEGIC GOAL = milestone objective (auto-set from story flags) — do NOT override for temporary needs. TACTICAL GOAL = immediate map-specific action (auto-derived from your location). SIDE OBJECTIVES = persistent secondary tasks. NAV routes toward the TACTICAL GOAL when present. Use set_tactical_goal for in-map sub-tasks; use add_side_objective for temporary missions. Tactical goals auto-clear on map change; side objectives persist until completed.
</navigation>
```

- [ ] **Step 2: Verify token count stays ≥2048**

The system prompt must stay ≥2048 tokens for prompt caching with extended thinking. Count tokens after the edit to verify. The new navigation section is roughly the same length as the old one, so this should pass.

- [ ] **Step 3: Commit**

```bash
git add claude_player/interface/claude_interface.py
git commit -m "Reframe system prompt navigation section for exploration-first"
```

---

### Task 7: Final verification and integration commit

- [ ] **Step 1: Run the agent briefly to verify no crashes**

```bash
cd /home/onyx/ClaudePlayer
pipenv run python play.py
```

Let it run for a few turns to verify:
- No import errors or crashes
- ⚑ EXPLORE nudge appears in the turn context for new maps
- MAP_HINTS display as vague nudges
- Strategic goals display correctly
- NAV pipeline still produces routes (check logs for `NAV` entries)

- [ ] **Step 2: Spot-check a few hint entries**

Manually verify 3-4 MAP_HINTS entries look right:
- An overworld hint (should be vague directional)
- A dungeon hint (should have floor progression, no warp coords)
- A STORY_PROGRESSION goal (should be objective-focused with map name)

- [ ] **Step 3: Final commit if any adjustments needed**

```bash
git add -A
git commit -m "Exploration-first hint redesign: final adjustments"
```
