# ClaudePlayer

AI agent that plays Pokemon Red via PyBoy emulator, controlled by Claude API.
The emulator runs continuously in real-time; the AI observes via screenshots and RAM reads, then sends button inputs that execute asynchronously.

## Setup & Run

```bash
pipenv install          # Install dependencies
pipenv shell            # Activate virtualenv
python play.py          # Run the agent (accepts --config path)
python emu_setup.py     # Manual save state creation utility
```

Requires `.env` with `ANTHROPIC_API_KEY` and a Game Boy ROM (`red.gb`) in project root.

## Project Structure

```text
claude_player/
  agent/          # Game loop (game_agent.py), KB subagent (memory_manager.py),
                  #   knowledge base (knowledge_base.py), NAV planner (nav_planner.py),
                  #   turn context builder (turn_context.py), goal deriver (goal_deriver.py)
  config/         # TypedDict config schema, JSON loader with deep merge, GBC palettes
  data/           # Static game data tables: pokemon.py (species, moves, types,
                  #   RARE_POKEMON, SLEEP/PARALYZE_MOVE_IDS),
                  #   items.py (inventory, badges, HMs), maps.py (map ID → name)
  interface/      # Claude API: system prompt construction, streaming, prompt caching
  state/          # Mutable game state: three-tier goals (strategic + tactical + side objectives), turn count, story progress
  tools/          # Decorator-based tool registry + tool definitions (send_inputs, set_strategic_goal, set_tactical_goal, add_side_objective, etc.)
  utils/          # RAM readers (spatial, battle, party, bag, menu, text), world map, pathfinding, cost tracker, sound output
  web/            # Flask dashboard: MJPEG stream, state API, runs as daemon thread
```

Key entry: `play.py` -> `claude_player/main.py` -> `game_agent.py` main loop.

## Code Conventions

- **Python 3.12**, managed with Pipenv (no pyproject.toml/setup.py)
- **Type hints** used throughout: `Dict[str, Any]`, `Optional[str]`, `TypedDict` for configs
- **Google-style docstrings** with `Args:` and `Returns:` sections
- **No linter/formatter configured** — match existing style when editing
- **Constants**: UPPER_SNAKE_CASE; module-private constants use leading underscore (`_ADDR_MAP_HEIGHT`), shared constants are public (`ADDR_PLAYER_Y`, `POKEMON_NAMES`)
- **RAM addresses**: prefixed with `ADDR_`, sourced from pret/pokered disassembly. Shared addresses live in `utils/ram_constants.py`; module-specific addresses stay local. Import the raw name — no `as _` aliasing.
- **Game data**: static lookup tables (species, moves, items, maps) live in `claude_player/data/`; logic modules import from there
- **Shared helpers**: `read_word()` and `decode_status()` in `ram_constants.py` — used by both `battle_context` and `party_context`
- **Logging**: `logging` module everywhere; file=INFO+, console=WARNING+; rotating handler (5MB, 2 backups). Key log patterns for analysis:
  - `TURN_SUMMARY: t=N map=0xHH(Name) pos=(x,y) hp=N% [stuck=N] [nav=METHOD] goal="..." cost=$N tokens=N tools=... actions="..." duration=Ns` — one grepable line per turn with all dimensions. `stuck` field only appears when >0; `nav` shows which NAV pipeline stage resolved (`graph`, `cache`, `exhausted`, `frontier`, `compass`, `none`)
  - `OUTCOME: t=N Executed: ... — moved/UNCHANGED/warped` — action result (logged at execution time, not next turn). UNCHANGED outcomes include accumulated blocked-direction tracking: `BLOCKED directions at (x,y): UP,LEFT | Untried: DOWN,RIGHT`
  - `STATS: t=A-B blocked=P%(N/M) cost=$N thinking_only=N no_action=N session=$N` — periodic aggregate stats emitted every 25 turns
  - `THINKING: N chars — preview...` — 1-line INFO summary of model thinking (full text at DEBUG level)
  - `LOCATION: map=0xHH (Name) pos=(x,y)` — logged in turn header alongside GAME/GOAL/TURN
  - `INTERRUPT: t=N ...`, `RECOVERY: t=N ...`, `THINKING-ONLY RESPONSE: t=N ...`, `NO-ACTION TURN: t=N ...` — all events tagged with turn number for correlation
  - FPS metrics logged at DEBUG level only (not WARNING) — normal fluctuations during API calls are not actionable
- **No test suite** — no pytest, unittest, or test files exist
- **Imports**: stdlib -> third-party (pyboy, flask, anthropic) -> local (claude_player.*)

## Dependencies

pyboy, pillow, anthropic, flask, python-dotenv (see Pipfile)

## Architecture Notes

- **Turn loop**: screenshot -> context extraction (RAM reads) -> API call -> tool execution -> repeat. Turn context assembly (injecting spatial, battle, party, bag, stuck warnings, etc.) is handled by `TurnContextBuilder` in `agent/turn_context.py`.
- **Three-tier goal system**: `GameState` holds `strategic_goal` (milestone, e.g. "Beat Brock"), `tactical_goal` (map-specific action, e.g. "Enter Pewter Gym from north"), and `side_objectives` (persistent secondary tasks, e.g. "Heal at Pokémon Center", "Buy Potions"). Strategic goals are auto-set from `STORY_PROGRESSION` event flags — `set_strategic_goal` should NOT be used for temporary needs (use `add_side_objective` instead). Tactical goals are auto-derived each turn by `derive_tactical_goal()` in `agent/goal_deriver.py` from the `MAP_HINTS` table in `event_flags.py`, keyed by `(next_flag, current_map_id)`. When no hand-authored hint exists, `derive_nav_tactical_goal()` falls back to map-graph BFS routing, generating goals like "Navigate to Route 4 (toward Cerulean City)". Side objectives persist across map changes (unlike tactical goals) and are managed via `add_side_objective` / `complete_side_objective` tools (max 5). The agent can override tactical goals via `set_tactical_goal` tool (has a 1-map-change grace period so overrides survive dungeon floor transitions, then auto-clears). The `current_goal` property returns `tactical_goal or strategic_goal` for backward compatibility. NAV pipeline uses tactical goal for routing, with strategic as fallback for map-graph BFS matching.
- **Prompt caching**: three `cache_control` breakpoints in the system prompt: (1) static prompt text — always cache-hits, (2) KB block (party+strategy+lessons) — cache-hits for most turns until the KB subagent rewrites it every `MEMORY_INTERVAL` turns, (3) tool definitions — always cache-hits. The KB subagent (`MemoryManager`) also uses prompt caching — its system prompt is wrapped as a cached content block. Requires **2048+ tokens** in the system prompt when extended thinking is enabled (undocumented — thinking mode doubles the normal 1024 Sonnet minimum). Tool-level `cache_control` breakpoints do NOT work with thinking. KB staleness uses an absolute turn number (`updated_at_turn=N`) rather than a relative count so the text stays identical between KB updates and doesn't invalidate the cache.
- **Movement feedback**: `send_inputs` records player position before/after execution and injects feedback at the start of the next turn ("moved (x,y)→(x,y)", "position UNCHANGED — blocked", or "map changed — warped"). UNCHANGED feedback includes **blocked-direction tracking**: accumulates which directions (U/D/L/R) have been tried and failed at the current position across turns, and shows untried directions (resets on successful move or map change). Only pure movement actions (containing only U/D/L/R tokens) update blocked-direction tracking — actions with A/B/W/T/S/X tokens (menu/battle interactions) are excluded to prevent false blocking from non-movement sequences like `run_from_battle`. Prevents the agent from retrying blocked paths.
- **Auto-dialog (T token)**: The `T` button token in `send_inputs` auto-advances all dialogue by pressing A until the text box closes. Uses `wStatusFlags5` bit 0 and WY register to detect active text. Includes stale-text detection (bails after 3 unchanged A presses — catches YES/NO prompts and shop menus that need directional input) and a 600-frame safety cap (~10s). Logs A-press count and frames used. Example: `"U32 L2 A T"` = walk up 2 tiles, face left, interact, auto-clear all dialogue. Replaces manual `A A A A A` sequences, saving turns and API cost on dialogue-heavy sequences.
- **NO-ACTION / THINKING-ONLY recovery**: when the model produces a NO-ACTION turn (used tools but no `send_inputs`) or a THINKING-ONLY response (no output at all), a nudge message is appended and the turn is retried. NO-ACTION nudges remind the model to include `send_inputs`; THINKING-ONLY nudges prompt immediate action. Max retries prevent infinite loops.
- **Screen text reader**: `text_context.py` decodes on-screen text (dialogue, signs, item pickups) directly from wTileMap (0xC3A0) using `_TEXT_CHARS` — an extended version of `G1_CHARS` with punctuation (0x9A-0x9F), accented é (0xBA), contractions (0xBB-0xC1), and symbols. Gated on wStatusFlags5 bit 0 or Window Y < 144.  Uses WY register to determine which tile rows to decode (WY < 8 = full screen, WY ≥ 8 = bottom overlay starting at row WY//8).  Detects the ▼ continuation arrow (0xEE) and hints "press A for more". Injected into turn context after menu context as `<screen_text>` block. No OCR dependency — tile indices map directly to characters.
- **Post-battle menu skip**: Menu context has two defenses against stale battle cursor RAM (Y=14 X=15): (1) `_was_in_battle` flag suppresses menu injection on the first turn after battle, (2) `extract_menu_context()` rejects any state where `cursor > max_item` (impossible in a real menu, catches stale battle cursor indefinitely).
- **Tool registry**: decorator pattern (`@registry.register(...)`) in `tool_registry.py`
- **Knowledge Base**: categorized persistent memory in `saves/knowledge/` replaces the old flat `MEMORY.md`. Sections: `party.md` (team strategy, subjective only — RAM has facts), `strategy.md` (current plan), `lessons.md` (hard-won rules), and `locations/<map_name>.md` (per-map notes). Two-layer injection: (1) system prompt (cached): party + strategy + lessons via `KnowledgeBase.build_cached_block()` — changes every `MEMORY_INTERVAL` turns, gets cache-read pricing otherwise; (2) user message (per-turn): current map's `<location_notes>` — small, changes on map transition. Background subagent (`MemoryManager`) updates sections independently every `MEMORY_INTERVAL` turns, with section selection based on context (always updates strategy+party; lessons every 3rd cycle; location when map is known). Migration from old `MEMORY.md` runs automatically on first startup.
- **World map**: persistent per-map tile accumulator with A* pathfinding in `world_map.py`. Includes a **map connectivity graph** (`map_graph`) that records bidirectional edges between maps from warps and connections. BFS on this graph provides map-level pathfinding so the NAV pipeline can identify the correct next-hop map (e.g. "go to Mt. Moon 1F" instead of "go to Cerulean City" when ledges block the direct route). The graph builds incrementally as maps are visited and persists in `world_map.json`. Map 0xFF ("outside / last map") warps are resolved to the actual previous map ID via `last_map_id`. BFS `find_map_path` excludes maps only as the **first hop** (where A\* on the current map failed to reach their warps); excluded maps can still appear as the destination through longer alternate routes (e.g. Route 4 can't A\* east to Cerulean due to ledges, but Route 4 → Mt. Moon → … → Cerulean works). **Pathfinding variance**: `find_path_to()` accepts `variance` (0–3) which adds random per-tile cost jitter, causing A\* to explore alternate routes when the optimal path keeps failing. Escalated automatically from `stuck_count` in `turn_context.py` (1→1, 2→2, 3+→3). `stuck_count` only resets on confirmed player movement or leaving overworld — screen animations (cave water, grass) no longer reset it. **Verified route cache**: `route_cache` in WorldMap stores paths that successfully led to a warp (confirmed by map transition via `confirm_route()`). On future visits, `get_cached_route()` splices from the nearest point within 3 tiles of the player. Skipped when variance>0 (stuck = don't reuse failing route). Pending routes discarded after 3 stuck turns. `record_warp_transition()` accepts `arrival_pos` and auto-exhausts the arrival-side warp on the destination map (within 2 tiles) so NAV doesn't immediately route back through the entry warp. When WARP CYCLING is detected (requires 3+2 transitions in each direction — 2 full round-trips), route cache for cycling maps is invalidated (except the `from_map` whose route was just confirmed). Intra-map warps (same map stairs) are excluded from cycling detection. `_exhausted_warps` and `_warp_transitions` persist in `world_map.json` across restarts. **Tile pair collisions**: Pokemon Red blocks movement between specific tile pairs even though both tiles are individually walkable (e.g. cave platform edges ↔ ground floor). `_TILE_PAIR_COLLISIONS` in `spatial_context.py` hardcodes these from pokered's `pair_collision_tile_ids.asm` for CAVERN (tileset 17) and FOREST (tileset 3). `_extract_terrain_data()` computes blocked-transition edges as `Set[((x1,y1),(x2,y2))]` and threads them through all A\* calls (viewport `find_path`, world-map `find_path_to`, `find_frontier_path`). Elevated cave tiles are shown as `':'` in the grid for visual distinction; ground tiles remain `'.'`. Blocked edges are persisted in `pair_blocked_edges` in `world_map.json`. **Directionality**: N/S cave elevation boundaries are directional — upper floor (`.`, 0x05) NORTH of lower ground (`:`, 0x20/etc) is passable (step down south, climb up north). Lower NORTH of upper is blocked both ways. E/W transitions always blocked. **Cross-map loop detection**: `get_cross_map_stuck_warning()` in `world_map.py` scans `_warp_transitions` for repetitive map pairs — if the same two maps account for 6+ transitions (3 round-trips) and only 2-3 distinct maps appear in the last 8 warps, a `CROSS-MAP LOOP` warning is injected into turn context by `turn_context.py`. Complements the existing per-map cycling/dead-end detection which resets on map change. Map-change detection uses `wCurMap` ID comparison only (position-jump heuristic removed — large caves caused false positives).
- **Web dashboard**: Flask in daemon thread, shares state via `TerminalDisplay` with thread locks. Browser audio streaming via `SoundOutput` (`utils/sound_output.py`) buffers PyBoy APU frames into WAV chunks served at `GET /audio/chunk`.
- **Config**: `config.json` auto-created on first run; deep-merged with defaults from `config_loader.py`

## Cost Tracking

Per-turn and cumulative USD cost is estimated by `CostTracker` in `utils/cost_tracker.py`.

**What's tracked** (matches the Anthropic `usage` response object fields):

- `input_tokens` — regular (non-cached) input tokens at the base input rate
- `output_tokens` — all output tokens, **including extended thinking/reasoning tokens** (billed at output rate)
- `cache_creation_input_tokens` — tokens written to prompt cache (5-min TTL tier, 1.25× input rate)
- `cache_read_input_tokens` — tokens served from cache (0.1× input rate)

Tool-use system prompt overhead (~346 tokens/call) is already included in `input_tokens` by the API.

**Pricing table** (`_MODEL_PRICING` dict in `cost_tracker.py` — keys matched by substring, more-specific first):

| Model family          | Input  | Output  | Cache read | Cache write (5m) |
| --------------------- | ------ | ------- | ---------- | ---------------- |
| Opus 4.5 / 4.6        | $5     | $25     | $0.50      | $6.25            |
| Opus 4 / 4.1          | $15    | $75     | $1.50      | $18.75           |
| Sonnet 4 / 4.5 / 4.6  | $3     | $15     | $0.30      | $3.75            |
| Haiku 4.5             | $1     | $5      | $0.10      | $1.25            |
| Haiku 3.5             | $0.80  | $4      | $0.08      | $1.00            |
| Haiku 3               | $0.25  | $1.25   | $0.03      | $0.30            |

All prices are per million tokens (MTok). The 1-hour cache write tier (2× input rate) is not distinguishable in the API response, so it is treated as 5-min writes — cost may be slightly underestimated if long-TTL caching is in use.

Cumulative stats persist across runs in `saves/session_stats.json`.

## RAM / Emulation

All RAM addresses reference the pret/pokered disassembly. Shared addresses (player pos, Pokédex, badges, money, event flags, menu cursor, battle detection) live in `utils/ram_constants.py`; module-specific addresses stay in their consumer files.

- Coordinates (`ADDR_PLAYER_Y`/`ADDR_PLAYER_X`) are in **block units** (1 block = 2×2 tiles = 16px step); warp entries and NPC sprite positions share this space (sprite state adds a constant +4 border offset)
- `hTileAnimations` at `0xFFD7`: 0=indoor/building (no animations), 1=cave (water animated), 2=outdoor (water+flower animated) — sourced from annotated hram.asm
- HRAM constants: `ADDR_TILE_PLAYER_ON` (FF93), `ADDR_DISABLE_JOYPAD` (FFF9)
- Battle context reads Pokemon stats, moves (with `id` field for move ID matching), HP, PP, menu cursor, **stat stage modifiers** (CD1A–CD33, 0–12 where 7=neutral), **turn counter** (CCD5), **whose half-turn** (FFF3), and last confirmed move indices (CCDC/CCDD). `_read_party_levels()` reads alive party member levels via `_PARTY_LEVEL_OFFSET = 0x21` per party slot. TIP generation filters out moves with 0x type effectiveness (immunity) before recommending — if all damage moves are immune, falls through to RUN/switch advice. **Fixed-damage moves** (NIGHT SHADE, SEISMIC TOSS, DRAGON RAGE, etc.) bypass type immunity filtering in `_effective_power()` since they ignore the type chart in Gen 1. Fight-menu TIP compound sequences use `W32` (32 frames) settle wait after entering the fight submenu to ensure "No PP left!" text clears before cursor navigation. **Leveling TIP**: `_generate_battle_tip()` accepts `min_party_level`; when any alive party member is below the enemy's level in a wild battle, prepends "TRAIN: Team needs XP — FIGHT!" to the fight recommendation. System prompt directs the agent to fight when TIP says TRAIN and to use overworld START → POKEMON to put underleveled mons in the lead slot for full XP.
- **Rare Pokemon catch system**: `RARE_POKEMON` set in `data/pokemon.py` defines ~25 species (low encounter rate, one-per-game, legendaries) that trigger aggressive catch TIPs. Multi-phase strategy in `_generate_battle_tip()`: (A) inflict sleep/paralysis for catch bonus using `SLEEP_MOVE_IDS`/`PARALYZE_MOVE_IDS`, (B) weaken with gentlest move via `_pick_catch_move()` + `_estimate_damage()` (Gen 1 formula, picks lowest max-damage move that won't KO), (C) switch to weaker party member if all moves would KO, (D) throw best ball via `_find_best_ball()` (prefers Ultra > Great > Poke, skips Master Ball). Standard (non-rare) catch logic unchanged: triggers at ≤40% HP with open party slot, or ≤20% HP, or enemy asleep/frozen. Rare encounters logged at WARNING level (`RARE ENCOUNTER:` prefix).
- **Battle start settle**: `_BATTLE_START_SETTLE = 6.0s` in `game_agent.py` — analysis is gated until 6s after battle interrupt to let intro animations finish. `run_from_battle` preamble (A W64 × 3 + W64) provides additional safety for the "unknown" submenu case.
- `0xCC2F` is dual-purpose: party index of sent-out Pokemon outside the fight submenu, last A-confirmed fight slot (0–3) inside it
- Event flags at `ADDR_EVENT_FLAGS` track story progression milestones
- Sprite data starts at `0xC100` with 16-byte stride per sprite
- Direction constants (`DIR_BUTTONS`, `LEDGE_ALLOWED_DIR`, `NEIGHBORS`) are canonical in `pathfinding.py`; `world_map.py` imports from there
- **Tile pair collisions** (hardcoded from pokered `data/tilesets/pair_collision_tile_ids.asm`): `_TILE_PAIR_COLLISIONS` in `spatial_context.py` maps tileset ID → list of (tile1, tile2) raw wTileMap values that block movement between them. CAVERN (17): `{(0x20,0x05), (0x41,0x05), (0x2A,0x05), (0x05,0x21)}` — elevated platform tiles vs ground. FOREST (3): 7 pairs involving tile 0x2E. Pre-computed as `_TILE_PAIR_SETS` (frozenset lookup). Lower CAVERN ground tiles (`_CAVE_LOWER_TILES = {0x20, 0x21, 0x2A, 0x41}`) are rendered as `':'` in the terrain grid; upper floor tile 0x05 stays `'.'`. `PairBlockedEdges` type alias in `pathfinding.py`

When modifying RAM readers, verify addresses against <https://github.com/pret/pokered>.

## Config

Runtime config in `config.json` — see `config/config_class.py` for the full TypedDict schema.
Key sections: `MODEL_DEFAULTS` (default MAX_TOKENS=2048, THINKING_BUDGET=1024), `ACTION`, `MEMORY`, `STUCK` detection thresholds.
`ENABLE_SOUND` (default `true`): when `false`, PyBoy skips APU sampling entirely (`tick(sound=False)`). Browser audio streaming via `SoundOutput` is also disabled.

## System Prompt & Navigation

The system prompt (`claude_interface.py: _build_system_prompt`) must stay **above 2048 tokens** for prompt caching to work with extended thinking enabled. It contains:

- `<notation>` — button input format rules
- `<spatial_context>` — grid legend, movement rules, warp mechanics
- `<navigation>` — **critical**: COMPASS vs NAV priority rules, ROUTE PLANNING (`check_tiles` before moving when no NAV), stuck recovery, warp pathing, dead-end behavior. Added to prevent the agent from converting compass bearings into frame inputs (e.g. "6 LEFT" → "L96") which walks into walls.
- **NAV pipeline** (in `agent/nav_planner.py`, entry point `compute_nav()`): (0) **Route cache** — if a verified cached route exists for the destination and player is within 3 tiles of it, use the cached path (skipped when variance>0). (1) **Map graph** — extract target map from goal text (tactical first, then strategic fallback), BFS to find next hop, A\* to that hop's warp. If A\* fails (e.g. ledges block), exclude that hop as a **first hop only** and retry BFS up to 3 times — the excluded map can still appear as the destination through longer alternate routes. (2a) **Frontier-first fallback** — when graph routing was tried but all hops unreachable (common in partially-explored caves), push into unexplored territory instead of routing backward to the previous floor. (2b) **COMPASS fallback** — parse direction from goal text, match against COMPASS entries. (3) **Frontier exploration** — A\* to nearest unexplored tile edge. (4) **Ledge-aware truncation** — before emitting buttons, scan the path for tiles adjacent to one-way ledges where the player is in the "launch position" (could accidentally jump). Truncates 1 step before the danger zone unless the path intentionally crosses the ledge. The computed button sequence is included in the spatial text for the model to use directly. Module-level `last_nav_method` records which stage resolved (`graph`, `cache`, `exhausted`, `frontier`, `compass`, `none`) — read by `game_agent.py` for TURN_SUMMARY diagnostics.
- **Viewport warp pathing**: `find_path()` in `pathfinding.py` accepts `extra_passable` positions. When computing overshoot paths to warps, the target warp tile is marked passable so A\* routes *through* it (not around it). Without this, 'W' tiles are blocked in `DEFAULT_BLOCKED`, causing paths to circle around warps without triggering them.
- `<battle_context>` — battle menu layout, RUN sequences, type matchups, healing thresholds
- `<menu_context>` — menu navigation, Pokemon menu, save mechanics
- `<authority>` / `<memory>` — data trust hierarchy, KB injection description

When editing the system prompt, verify the token count stays above 2048 using `client.messages.count_tokens()`.
