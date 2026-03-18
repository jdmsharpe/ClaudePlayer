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
  agent/          # Game loop (game_agent.py), memory subagent (memory_manager.py),
                  #   NAV planner (nav_planner.py), turn context builder (turn_context.py),
                  #   goal deriver (goal_deriver.py)
  config/         # TypedDict config schema, JSON loader with deep merge, GBC palettes
  data/           # Static game data tables: pokemon.py (species, moves, types),
                  #   items.py (inventory, badges, HMs), maps.py (map ID → name)
  interface/      # Claude API: system prompt construction, streaming, prompt caching
  state/          # Mutable game state: two-tier goals (strategic + tactical), turn count, story progress
  tools/          # Decorator-based tool registry + tool definitions (send_inputs, set_strategic_goal, set_tactical_goal, etc.)
  utils/          # RAM readers (spatial, battle, party, bag, menu), world map, pathfinding, cost tracker, sound output
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
- **Logging**: `logging` module everywhere; file=INFO+, console=WARNING+; rotating handler (5MB, 2 backups)
- **No test suite** — no pytest, unittest, or test files exist
- **Imports**: stdlib -> third-party (pyboy, flask, anthropic) -> local (claude_player.*)

## Dependencies

pyboy, pillow, anthropic, flask, python-dotenv (see Pipfile)

## Architecture Notes

- **Turn loop**: screenshot -> context extraction (RAM reads) -> API call -> tool execution -> repeat. Turn context assembly (injecting spatial, battle, party, bag, stuck warnings, etc.) is handled by `TurnContextBuilder` in `agent/turn_context.py`.
- **Two-tier goal system**: `GameState` holds `strategic_goal` (milestone, e.g. "Beat Brock") and `tactical_goal` (map-specific action, e.g. "Enter Pewter Gym from north"). Strategic goals are auto-set from `STORY_PROGRESSION` event flags. Tactical goals are auto-derived each turn by `derive_tactical_goal()` in `agent/goal_deriver.py` from the `MAP_HINTS` table in `event_flags.py`, keyed by `(next_flag, current_map_id)`. The agent can override tactical goals via `set_tactical_goal` tool (cleared on map change) or redirect the mission via `set_strategic_goal`. The `current_goal` property returns `tactical_goal or strategic_goal` for backward compatibility. NAV pipeline uses tactical goal for routing, with strategic as fallback for map-graph BFS matching.
- **Prompt caching**: system prompt cached via `cache_control` breakpoint; requires **2048+ tokens** when extended thinking is enabled (undocumented — thinking mode doubles the normal 1024 Sonnet minimum). Tool-level `cache_control` breakpoints do NOT work with thinking.
- **Movement feedback**: `send_inputs` records player position before/after execution and injects feedback at the start of the next turn ("moved (x,y)→(x,y)", "position UNCHANGED — blocked", or "map changed — warped"). Prevents the agent from retrying blocked paths.
- **Post-battle menu skip**: Menu context injection is suppressed on the turn immediately after battle ends (`_was_in_battle` flag), because battle cursor RAM (Y=14 X=15) persists and gets misidentified as `item_submenu`.
- **Tool registry**: decorator pattern (`@registry.register(...)`) in `tool_registry.py`
- **Memory system**: background Haiku subagent updates `saves/MEMORY.md` every N turns (80-line cap)
- **World map**: persistent per-map tile accumulator with A* pathfinding in `world_map.py`. Includes a **map connectivity graph** (`map_graph`) that records bidirectional edges between maps from warps and connections. BFS on this graph provides map-level pathfinding so the NAV pipeline can identify the correct next-hop map (e.g. "go to Mt. Moon 1F" instead of "go to Cerulean City" when ledges block the direct route). The graph builds incrementally as maps are visited and persists in `world_map.json`. Map 0xFF ("outside / last map") warps are resolved to the actual previous map ID via `last_map_id`.
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
- Battle context reads Pokemon stats, moves, HP, PP, menu cursor, **stat stage modifiers** (CD1A–CD33, 0–12 where 7=neutral), **turn counter** (CCD5), **whose half-turn** (FFF3), and last confirmed move indices (CCDC/CCDD)
- `0xCC2F` is dual-purpose: party index of sent-out Pokemon outside the fight submenu, last A-confirmed fight slot (0–3) inside it
- Event flags at `ADDR_EVENT_FLAGS` track story progression milestones
- Sprite data starts at `0xC100` with 16-byte stride per sprite
- Direction constants (`DIR_BUTTONS`, `LEDGE_ALLOWED_DIR`, `NEIGHBORS`) are canonical in `pathfinding.py`; `world_map.py` imports from there

When modifying RAM readers, verify addresses against <https://github.com/pret/pokered>.

## Config

Runtime config in `config.json` — see `config/config_class.py` for the full TypedDict schema.
Key sections: `MODEL_DEFAULTS`, `ACTION`, `MEMORY`, `STUCK` detection thresholds.
`ENABLE_SOUND` (default `true`): when `false`, PyBoy skips APU sampling entirely (`tick(sound=False)`). Browser audio streaming via `SoundOutput` is also disabled.

## System Prompt & Navigation

The system prompt (`claude_interface.py: _build_system_prompt`) must stay **above 2048 tokens** for prompt caching to work with extended thinking enabled. It contains:

- `<notation>` — button input format rules
- `<spatial_context>` — grid legend, movement rules, warp mechanics
- `<navigation>` — **critical**: COMPASS vs NAV priority rules, stuck recovery, warp pathing, dead-end behavior. Added to prevent the agent from converting compass bearings into frame inputs (e.g. "6 LEFT" → "L96") which walks into walls.
- **NAV pipeline** (in `agent/nav_planner.py`, entry point `compute_nav()`): (1) **Map graph** — extract target map from goal text (tactical first, then strategic fallback), BFS to find next hop, A\* to that hop's warp. If A\* fails (e.g. ledges block), exclude and retry BFS up to 3 times. (2) **COMPASS fallback** — parse direction from goal text, match against COMPASS entries. (3) **Frontier exploration** — A\* to nearest unexplored tile edge.
- **Viewport warp pathing**: `find_path()` in `pathfinding.py` accepts `extra_passable` positions. When computing overshoot paths to warps, the target warp tile is marked passable so A\* routes *through* it (not around it). Without this, 'W' tiles are blocked in `DEFAULT_BLOCKED`, causing paths to circle around warps without triggering them.
- `<battle_context>` — battle menu layout, RUN sequences, type matchups, healing thresholds
- `<menu_context>` — menu navigation, Pokemon menu, save mechanics
- `<authority>` / `<memory>` — data trust hierarchy

When editing the system prompt, verify the token count stays above 2048 using `client.messages.count_tokens()`.
