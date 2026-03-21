# ClaudePlayer

AI agent that plays Pokemon Red via PyBoy emulator, controlled by Claude API.
The emulator runs in real-time; the AI observes via screenshots and RAM reads, then sends button inputs asynchronously.

## Setup & Run

```bash
pipenv install && pipenv shell
python play.py          # Run the agent (accepts --config path)
python emu_setup.py     # Manual save state creation utility
```

Requires `.env` with `ANTHROPIC_API_KEY` and `red.gb` in project root.

## Project Structure

```text
claude_player/
  agent/      # game_agent.py (main loop), memory_manager.py (KB subagent),
              # knowledge_base.py, nav_planner.py, turn_context.py, goal_deriver.py
  config/     # TypedDict config schema, JSON loader with deep merge, GBC palettes
  data/       # Static tables: pokemon.py (species, moves, RARE_POKEMON, status move IDs),
              # items.py (inventory, badges, HMs), maps.py (map ID → name)
  interface/  # Claude API: system prompt, streaming, prompt caching
  state/      # Mutable state: three-tier goals, turn count, story progress
  tools/      # Decorator-based registry + definitions (send_inputs, goals, markers, etc.)
  utils/      # RAM readers (spatial, battle, party, bag, menu, text), world_map.py,
              # pathfinding.py, warp_overrides.py, cost_tracker.py, sound_output.py
  web/        # Flask dashboard: MJPEG stream, state API, audio streaming
```

Entry: `play.py` → `claude_player/main.py` → `game_agent.py` main loop.

## Code Conventions

- **Python 3.12**, Pipenv (no pyproject.toml/setup.py)
- **Type hints**: `Dict[str, Any]`, `Optional[str]`, `TypedDict` for configs
- **Google-style docstrings** with `Args:` / `Returns:`
- **No linter** — match existing style
- **Constants**: UPPER_SNAKE_CASE; module-private with `_` prefix (`_ADDR_MAP_HEIGHT`)
- **RAM addresses**: prefixed `ADDR_`; shared in `utils/ram_constants.py`, module-specific stay local
- **Game data**: static tables in `claude_player/data/`; logic modules import from there
- **Shared helpers**: `read_word()` and `decode_status()` in `ram_constants.py`
- **Logging**: `logging` module; file=INFO+, console=WARNING+; rotating (5MB, 2 backups)
  - Key tags: `TURN_SUMMARY`, `OUTCOME`, `STATS` (every 25 turns), `INTERRUPT`, `RECOVERY`, `NO-ACTION TURN`, `THINKING-ONLY RESPONSE` — all include `t=N` for correlation
- **No test suite**
- **Imports**: stdlib → third-party → local (`claude_player.*`)

## Dependencies

pyboy, pillow, anthropic, flask, python-dotenv (see Pipfile)

## Architecture Notes

- **Turn loop**: screenshot → RAM reads → API call → tool execution → repeat. `TurnContextBuilder` in `agent/turn_context.py` assembles context (spatial, battle, party, stuck warnings, etc.).

- **Three-tier goals**: `strategic_goal` (milestone, auto-set from story flags — don't use `set_strategic_goal` for temp needs), `tactical_goal` (map-specific, auto-derived by `goal_deriver.py`; 200-char cap with `"… (see location notes)"` suffix), `side_objectives` (persistent across map changes, max 5). Auto-heal gate: total offensive PP ≤10 injects "URGENT: Heal" objective (removed when PP > 20). Level gates: `MILESTONE_LEVEL_GATES` in `event_flags.py` auto-injects training objective when party is underleveled.

- **Prompt caching**: 3 `cache_control` breakpoints: (1) static system prompt text, (2) KB block, (3) tool definitions. **System prompt must stay ≥2048 tokens** when extended thinking is enabled — undocumented requirement (thinking doubles the normal 1024 Sonnet minimum). KB staleness uses absolute turn number (not relative) so the text stays identical between updates and avoids cache invalidation. Tool-level `cache_control` does NOT work with thinking.

- **Knowledge Base**: `saves/knowledge/` — `party.md`, `strategy.md`, `lessons.md` (`[CRITICAL]`/`[RULE]`/`[STRATEGY]` prefixes), `locations/<map>.md`. Two-layer injection: (1) cached system prompt block updated every `MEMORY_INTERVAL` turns by background `MemoryManager` subagent; (2) per-turn user message with current map's location notes. `KB_SYSTEM_PROMPT` must also stay ≥2048 tokens. `_sanitize_strategy()` strips ephemeral battle state lines the subagent writes despite instructions.

- **World map** (`world_map.py`): persistent tile accumulator + A* + **map connectivity graph** (`map_graph`) for BFS hop routing. `ensure_graph_edge()` guarantees bidirectional edges on every map transition. **Pathfinding variance** (0–3): cost jitter forces alternate routes when stuck; escalated from `stuck_count` in `turn_context.py`. **Route cache**: paths confirmed by successful warp transition reused via `get_cached_route()`; skipped when variance>0. **Warp cycling**: detected after 3+2 transitions in both directions; cycling pair added to `_cycling_maps` (excluded from BFS); decays after 30 turns. **Frontier-first**: when `frontier_ratio() > 0.3` and not stuck, NAV routes to nearest frontier tile before goal-directed routing. **Map markers**: `place_marker`/`remove_marker` tools; max 8/map; rendered `*` with legend; injected into spatial text each turn. Persisted in `world_map.json`.

- **NAV pipeline** (`nav_planner.py`, entry `compute_nav()`): (0) frontier-first, (0b) route cache, (1) map graph BFS → A\* to next-hop warp (retries up to 3× excluding failed first hops), (2a) frontier fallback when all hops exhausted or 2 consecutive "exhausted" results, (2b) COMPASS from goal text, (3) frontier exploration A\*, (4) ledge-aware path truncation. `last_nav_method` records which stage resolved.

- **Warp dest_name overrides** (`warp_overrides.py`): disambiguates caves with multiple warps to same `dest_map`. When adding overrides: pokered ASM uses **1-based** warp indices; RAM uses **0-based**. `MAP_HINTS` in `event_flags.py` must use exact override name strings for NAV regex matching.

- **Tile pair collisions** (from pokered `pair_collision_tile_ids.asm`): CAVERN (tileset 17) and FOREST (tileset 3) block movement between specific tile pairs even when tiles are individually walkable. `_TILE_PAIR_COLLISIONS` in `spatial_context.py`. Cave lower tiles (`:`) vs upper floor (`.`) — upper NORTH of lower is passable; lower NORTH of upper blocked; E/W always blocked. Persisted as `pair_blocked_edges` in `world_map.json`.

- **Movement feedback**: position before/after injected at next turn start. UNCHANGED includes blocked-direction accumulation (dirs tried/failed at current pos, resets on move/warp). Only pure movement tokens (U/D/L/R) update tracking — A/B/W/T/S/X excluded. Suppressed during battle.

- **T token** (auto-dialog): auto-advances all dialogue (A-presses until text closes). Stale-text detection bails after 3 unchanged A-presses; 600-frame safety cap. Example: `"U32 L2 A T"` = walk 2 up, face left, interact, clear all dialogue.

- **NO-ACTION / THINKING-ONLY recovery**: nudge appended and turn retried. Max retries prevent loops.

- **Screen text**: `text_context.py` decodes wTileMap (0xC3A0) tile indices → characters. No OCR. Gated on text-box-active flag or WY < 144.

- **Post-battle menu skip**: `_was_in_battle` flag suppresses menu injection on first post-battle turn. `extract_menu_context()` rejects cursor > max_item (catches stale battle cursor Y=14 X=15 indefinitely).

- **Web dashboard**: Flask in daemon thread. Audio at `GET /audio/chunk` (WAV chunks from PyBoy APU).

## Cost Tracking

`CostTracker` in `utils/cost_tracker.py`. Pricing per MTok:

| Model             | Input  | Output | Cache read | Cache write |
| ----------------- | ------ | ------ | ---------- | ----------- |
| Opus 4.5/4.6      | $5     | $25    | $0.50      | $6.25       |
| Opus 4/4.1        | $15    | $75    | $1.50      | $18.75      |
| Sonnet 4/4.5/4.6  | $3     | $15    | $0.30      | $3.75       |
| Haiku 4.5         | $1     | $5     | $0.10      | $1.25       |
| Haiku 3.5         | $0.80  | $4     | $0.08      | $1.00       |
| Haiku 3           | $0.25  | $1.25  | $0.03      | $0.30       |

Cumulative stats persist in `saves/session_stats.json`.

## RAM / Emulation

Addresses from pret/pokered disassembly. Shared in `utils/ram_constants.py`; module-specific stay local.

- Coordinates (`ADDR_PLAYER_Y`/`ADDR_PLAYER_X`): **block units** (1 block = 2×2 tiles = 16px step)
- `hTileAnimations` 0xFFD7: 0=indoor, 1=cave, 2=outdoor
- `0xCC2F` dual-purpose: party index outside fight submenu; last A-confirmed fight slot (0–3) inside it
- Battle context: stats, moves (with `id`), HP, PP, stat stages (CD1A–CD33, 0–12, 7=neutral), turn counter (CCD5), half-turn (FFF3), last move indices (CCDC/CCDD). Fight TIPs show PP as `pp/base_pp PP`; append `T` to auto-advance post-attack text.
- **Rare Pokemon** (`RARE_POKEMON` in `data/pokemon.py`): triggers multi-phase catch TIP — (A) inflict sleep/paralysis, (B) weaken without KO, (C) switch if all moves would KO, (D) throw best ball. Logged at WARNING (`RARE ENCOUNTER:`).
- Battle start settle: `_BATTLE_START_SETTLE = 6.0s` before analysis (lets intro animations finish)
- Sprite data: 0xC100, 16-byte stride; player facing at 0xC109
- Event flags: `ADDR_EVENT_FLAGS`; direction constants canonical in `pathfinding.py`

When modifying RAM readers, verify against <https://github.com/pret/pokered>.

## Config

`config.json` — see `config/config_class.py` for full TypedDict schema.
Key sections: `MODEL_DEFAULTS` (MAX_TOKENS=2048, THINKING_BUDGET=1024), `ACTION`, `MEMORY`, `STUCK`.
`ENABLE_SOUND` (default true): disables PyBoy APU sampling and audio streaming when false.

## System Prompt

`claude_interface.py: _build_system_prompt` — must stay **≥2048 tokens** for prompt caching with extended thinking.

Sections: `<notation>`, `<spatial_context>`, `<navigation>` (COMPASS vs NAV priority rules — prevents compass bearings being converted to frame counts like "6 LEFT" → `L96`), `<battle_context>`, `<menu_context>`, `<authority>`, `<memory>`.

NAV planner outputs button sequence in spatial text for direct model use. Verify token count after edits: `client.messages.count_tokens()`.
