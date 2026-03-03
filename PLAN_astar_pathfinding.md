# Plan: A* Pathfinding for Navigation

## Context

The AI agent navigates using `[straight-line: U64 R16]` hints appended to warps, NPCs, and map edges. These compute a direct vector ignoring walls and warp tiles. In practice, straight-line paths frequently cross warp tiles — e.g., in Pallet Town the agent does `U64` to reach Route 1 north but walks into Oak's Lab's front door instead, bouncing between the two maps for 10+ turns.

**Fix**: Replace `[straight-line: ...]` with `[path: ...]` computed by A* on the collision grid. The model already reads and trusts these suggestions — no new tool needed.

## Key Design Decisions

- **Warp avoidance**: All `W` tiles are BLOCKED unless the warp IS the A* destination. Only the specific goal tile gets exempted.
- **NPC interaction**: Path to the best adjacent walkable tile, then append a facing command (`U1`/`D1`/`L1`/`R1`) + `A1`.
- **Map edges**: Multi-goal A* to find the nearest reachable walkable cell on the target edge row/column.
- **Fallbacks**: If A* fails → `[no path found]`. If target is off-screen → `[off screen]` with straight-line fallback. If no collision data → old straight-line behavior.
- **Performance**: Grid is 10x9 = 90 cells. A* terminates in microseconds. ~10 calls per turn is negligible.

## Files

### 1. CREATE: `claude_player/utils/pathfinding.py`

Pure A* module, no PyBoy dependency. Three functions:

**`find_path(grid, start, goal, blocked_chars) -> Optional[List[Tuple[int,int]]]`**

- Standard A* with Manhattan heuristic on 4-connected grid
- `grid[y][x]` character grid, `start`/`goal` are `(x, y)` tuples
- `blocked_chars` defaults to `{'#', 'W', '1'-'9', 'n', 'i'}` — everything except `.` and `@`
- The goal tile is ALWAYS passable regardless of its character (so we can path TO a warp without unlocking all warps)
- Returns list of `(x, y)` from start to goal, or `None`

**`find_path_to_edge(grid, start, edge, blocked_chars) -> Optional[List[Tuple[int,int]]]`**

- Finds shortest path to any walkable cell on the specified edge (`"NORTH"`, `"SOUTH"`, `"EAST"`, `"WEST"`)
- Uses multi-goal A* (single expansion, goal test is `current in goal_set`)

**`path_to_buttons(path, frames_per_tile=16) -> str`**

- Converts `[(4,4), (4,3), (4,2), (5,2)]` → `"U32 R16"`
- Merges consecutive same-direction steps

### 2. MODIFY: `claude_player/utils/spatial_context.py`

**`_format_warp_text(warp_data, grid, player_pos)`** — add `grid` and `player_pos` params:

- For each warp: compute grid position `(px + dx, py + dy)`, run `find_path`, convert to buttons
- For map edge connections: run `find_path_to_edge`, convert to buttons
- Output: `W0: 3 DOWN, 2 LEFT -> Pallet Town  [path: D16 L32 D32]`
- Fallback: `[no path found]` or `[off screen]`

**`_format_npc_text(npc_data, grid, player_pos)`** — add `grid` and `player_pos` params:

- For NPCs: find the 4 adjacent tiles, filter to walkable, A* to nearest, append facing + `A1`
- For items: path directly to item tile (step on it to pick up)
- Output: `1: Nurse Joy - 3 DOWN  [path: D32 R16 D1 A1]`

**`_format_spatial_text(...)`** — pass `grid` and `player_screen_pos` to the two formatters above

### 3. MODIFY: `claude_player/interface/claude_interface.py`

Update line 54 in the `<spatial_context>` block:

```text
NAVIGATION: Follow [path: ...] suggestions — they route around walls and avoid accidental warps.
If [no path found], try 1-tile exploratory moves. For long paths, execute the first 3-4 tiles, then re-check spatial context.
```

Remove line 60: `Prefer SHORT moves (1-3 tiles)` — A* paths are pre-validated, no need to limit length. (Actually keep a softer version since screen scrolling can invalidate long paths.)

## Implementation Order

1. `pathfinding.py` — pure algorithm, zero dependencies, can be tested standalone
2. `spatial_context.py` — integrate A* into `_format_warp_text`, `_format_npc_text`, `_format_spatial_text`
3. `claude_interface.py` — update navigation guidance in system prompt

## Verification

1. `python -c "from claude_player.utils.pathfinding import find_path, path_to_buttons; ..."` — test with a sample grid
2. Run the agent in Pallet Town — verify warps/edges show `[path: ...]` that routes AROUND Oak's Lab
3. Verify the NORTH edge path to Route 1 avoids the Oak's Lab warp tile
4. Verify NPC paths go to adjacent tile with facing command
5. Check logs for reasonable path strings (no `U64` through buildings)
