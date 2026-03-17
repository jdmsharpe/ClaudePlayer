# ClaudePlayer

AI agent that plays Pokemon Red via PyBoy emulator, controlled by Claude API.
The emulator only ticks when the AI sends inputs (turn-based, not real-time).

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
  agent/          # Game loop (game_agent.py), memory subagent (memory_manager.py)
  config/         # TypedDict config schema, JSON loader with deep merge, GBC palettes
  interface/      # Claude API: system prompt construction, streaming, prompt caching
  state/          # Mutable game state: goal, turn count, story progress
  tools/          # Decorator-based tool registry + tool definitions (send_inputs, set_goal, etc.)
  utils/          # RAM readers (spatial, battle, party, bag, menu), world map, pathfinding
  web/            # Flask dashboard: MJPEG stream, state API, runs as daemon thread
```

Key entry: `play.py` -> `claude_player/main.py` -> `game_agent.py` main loop.

## Code Conventions

- **Python 3.12**, managed with Pipenv (no pyproject.toml/setup.py)
- **Type hints** used throughout: `Dict[str, Any]`, `Optional[str]`, `TypedDict` for configs
- **Google-style docstrings** with `Args:` and `Returns:` sections
- **No linter/formatter configured** — match existing style when editing
- **Constants**: UPPER_SNAKE_CASE, private with leading underscore (`_ADDR_PLAYER_Y`)
- **RAM addresses**: prefixed with `ADDR_`, sourced from pret/pokered disassembly
- **Logging**: `logging` module everywhere; file=INFO+, console=WARNING+; rotating handler (5MB, 2 backups)
- **No test suite** — no pytest, unittest, or test files exist
- **Imports**: stdlib -> third-party (pyboy, flask, anthropic) -> local (claude_player.*)

## Dependencies

pyboy, pillow, anthropic, flask, python-dotenv (see Pipfile)

## Architecture Notes

- **Turn loop**: screenshot -> context extraction (RAM reads) -> API call -> tool execution -> repeat
- **Prompt caching**: system prompt + tool defs cached with Anthropic cache control headers
- **Tool registry**: decorator pattern (`@registry.register(...)`) in `tool_registry.py`
- **Memory system**: background Haiku subagent updates `saves/MEMORY.md` every N turns (80-line cap)
- **World map**: persistent per-map tile accumulator with A* pathfinding in `world_map.py`
- **Web dashboard**: Flask in daemon thread, shares state via `TerminalDisplay` with thread locks
- **Config**: `config.json` auto-created on first run; deep-merged with defaults from `config_loader.py`

## Cost Tracking

Per-turn and cumulative USD cost is estimated in `_estimate_cost()` (top of `game_agent.py`).

**What's tracked** (matches the Anthropic `usage` response object fields):

- `input_tokens` — regular (non-cached) input tokens at the base input rate
- `output_tokens` — all output tokens, **including extended thinking/reasoning tokens** (billed at output rate)
- `cache_creation_input_tokens` — tokens written to prompt cache (5-min TTL tier, 1.25× input rate)
- `cache_read_input_tokens` — tokens served from cache (0.1× input rate)

Tool-use system prompt overhead (~346 tokens/call) is already included in `input_tokens` by the API.

**Pricing table** (`_MODEL_PRICING` dict — keys matched by substring, more-specific first):

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

All RAM addresses reference the pret/pokered disassembly. Key areas:

- Spatial context reads tilemap, collisions, warps, NPCs, map connections from RAM
- Coordinates (`wYCoord`/`wXCoord` at `0xD361`/`0xD362`) are in **block units** (1 block = 2×2 tiles = 16px step); warp entries and NPC sprite positions share this space (sprite state adds a constant +4 border offset)
- `hTileAnimations` at `0xFFD7`: 0=indoor/building (no animations), 1=cave (water animated), 2=outdoor (water+flower animated) — sourced from annotated hram.asm
- HRAM constants live in `ram_constants.py` under `ADDR_TILE_PLAYER_ON` (FF93), `ADDR_JOY_HELD` (FFB4), `ADDR_UI_LAYOUT_FLAGS` (FFF6), `ADDR_DISABLE_JOYPAD` (FFF9)
- Battle context reads Pokemon stats, moves, HP, PP, menu cursor, **stat stage modifiers** (CD1A–CD33, 0–12 where 7=neutral), **turn counter** (CCD5), **whose half-turn** (FFF3), and last confirmed move indices (CCDC/CCDD)
- `0xCC2F` is dual-purpose: party index of sent-out Pokemon outside the fight submenu, last A-confirmed fight slot (0–3) inside it
- Event flags at `0xD747` track story progression milestones
- Sprite data starts at `0xC100` with 16-byte stride per sprite

When modifying RAM readers, verify addresses against <https://github.com/pret/pokered>.

## Config

Runtime config in `config.json` — see `config/config_class.py` for the full TypedDict schema.
Key sections: `MODEL_DEFAULTS`, `ACTION`, `MEMORY`, `STUCK` detection thresholds.
