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
