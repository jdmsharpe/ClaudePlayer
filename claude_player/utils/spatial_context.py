import logging
from typing import Dict, List, Tuple, Optional, Any
from pyboy import PyBoy

# Game Boy visible screen: 160x144 pixels = 20x18 tiles of 8x8 pixels
SCREEN_TILES_X = 20
SCREEN_TILES_Y = 18
TILEMAP_SIZE = 32  # Background tilemap is always 32x32
MAX_SPRITES = 40

# Characters assigned to unique tile IDs (62 slots)
TILE_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

# Markers overlaid on the grid for on-screen sprites
# Avoids '#' which is used for blocked tiles in walkability mode
SPRITE_MARKERS = "@$%&*!~+=^"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pokemon Red/Blue RAM addresses (from pret/pokered disassembly)
# ---------------------------------------------------------------------------
_ADDR_CUR_MAP = 0xD35E
_ADDR_PLAYER_Y = 0xD361       # Map-block coordinate
_ADDR_PLAYER_X = 0xD362       # Map-block coordinate
_ADDR_MAP_HEIGHT = 0xD368     # In blocks
_ADDR_MAP_WIDTH = 0xD369      # In blocks
_ADDR_NUM_WARPS = 0xD3AE
_ADDR_WARP_ENTRIES = 0xD3AF   # 4 bytes each: Y, X, dest_warp_id, dest_map
_MAX_WARPS = 32

# Map connections — seamless edge transitions (e.g. Pallet Town ↔ Route 1)
# wMapConnections bitfield: bit3=North, bit2=South, bit1=West, bit0=East
_ADDR_MAP_CONNECTIONS = 0xD370
# Each connection header is 11 bytes; first byte = connected map ID
_ADDR_NORTH_CONNECTED_MAP = 0xD371
_ADDR_SOUTH_CONNECTED_MAP = 0xD37C
_ADDR_WEST_CONNECTED_MAP  = 0xD387
_ADDR_EAST_CONNECTED_MAP  = 0xD392

# NPC / sprite RAM addresses
_ADDR_NUM_SPRITES = 0xD4E1     # Number of map sprites (excl. player)
_ADDR_SPRITE_STATE1 = 0xC100   # 16 bytes/sprite; offset 0x00 = picture ID
_ADDR_SPRITE_STATE2 = 0xC200   # 16 bytes/sprite; offset 0x04 = map Y, 0x05 = map X

# Game state detection addresses (from pret/pokered disassembly)
_ADDR_TEXT_BOX_ID    = 0xD125   # wTextBoxID – non-zero = text box active
_ADDR_IS_IN_BATTLE   = 0xD057   # wIsInBattle – 0=overworld, 1=wild, 2=trainer
_ADDR_WALK_COUNTER   = 0xCFC5   # wWalkCounter – non-zero = mid-step animation
_ADDR_JOY_IGNORE     = 0xCC6B   # wJoyIgnore – button ignore bitmask (retained for reference; stale like wTextBoxID)
_ADDR_STATUS_FLAGS5  = 0xD730   # bit5=joypad disabled, bit7=scripted movement
_ADDR_WINDOW_Y       = 0xFF4A   # WY register – Window layer Y position (144 = off-screen)

# Sprite picture ID → readable name (from pokered sprite_constants.asm)
_SPRITE_NAMES = {
    0x01: "Player",
    0x02: "Rival",
    0x03: "Prof. Oak",
    0x04: "Bug Catcher",
    0x05: "Slowbro",
    0x06: "Lass",
    0x07: "Boy",
    0x08: "Little Girl",
    0x09: "Bird",
    0x0A: "Old Man",
    0x0B: "Gambler",
    0x0C: "Boy",
    0x0D: "Girl",
    0x0E: "Hiker",
    0x0F: "Woman",
    0x10: "Gentleman",
    0x11: "Daisy",
    0x12: "Biker",
    0x13: "Sailor",
    0x14: "Cook",
    0x15: "Bike Shop Guy",
    0x16: "Mr. Fuji",
    0x17: "Giovanni",
    0x18: "Rocket",
    0x19: "Channeler",
    0x1A: "Waiter",
    0x1B: "Erika",
    0x1C: "Mom",
    0x1D: "Balding Man",
    0x1E: "Young Boy",
    0x1F: "Gameboy Kid",
    0x20: "Clefairy",
    0x21: "Agatha",
    0x22: "Bruno",
    0x23: "Lorelei",
    0x24: "Seel",
    0x25: "Swimmer",
    0x26: "Item Ball",
    0x27: "Omanyte",
    0x28: "Boulder",
    0x29: "Sign",
    0x2A: "Book",
    0x2B: "Clipboard",
    0x2C: "Snorlax",
    0x2D: "Old Amber",
    0x2E: "Fossil",
}
_ITEM_SPRITE_ID = 0x26

# Common early-game map names for readability
_MAP_NAMES = {
    0x00: "Pallet Town",
    0x01: "Viridian City",
    0x02: "Pewter City",
    0x03: "Cerulean City",
    0x04: "Lavender Town",
    0x05: "Vermilion City",
    0x06: "Celadon City",
    0x07: "Fuchsia City",
    0x08: "Cinnabar Island",
    0x09: "Indigo Plateau",
    0x0A: "Saffron City",
    0x0C: "Route 1",
    0x0D: "Route 2",
    0x0E: "Route 3",
    0x0F: "Route 4",
    0x14: "Route 22",
    0x25: "Red's House 1F",
    0x26: "Red's House 2F",
    0x27: "Blue's House",
    0x28: "Oak's Lab",
    0x29: "Pokemon Center (Viridian)",
    0x2A: "Viridian Mart",
    0x2F: "Viridian Forest",
    0x3B: "Pokemon Center (Pewter)",
    0x3C: "Pewter Gym",
    0xFF: "outside (last map)",
}


def _extract_visible_tilemap(pyboy: PyBoy) -> Tuple[List[List[int]], Tuple[int, int]]:
    """Extract the 20x18 visible tile region from the 32x32 background tilemap.

    Returns (visible, (scx_tile, scy_tile)) where visible is [y][x] indexed.
    """
    (scx_px, scy_px), _ = pyboy.screen.get_tilemap_position()
    scx = scx_px // 8
    scy = scy_px // 8

    bg = pyboy.tilemap_background[:, :]  # [y][x], 32x32 list-of-lists

    visible = []
    for ty in range(SCREEN_TILES_Y):
        row = []
        for tx in range(SCREEN_TILES_X):
            map_y = (ty + scy) % TILEMAP_SIZE
            map_x = (tx + scx) % TILEMAP_SIZE
            row.append(bg[map_y][map_x])
        visible.append(row)

    return visible, (scx, scy)


def _build_tile_legend(visible: List[List[int]]) -> Tuple[Dict[int, str], str]:
    """Assign a unique character to each unique tile ID in the visible grid.

    Returns (tile_to_char, legend_text).
    """
    unique_ids = sorted({tile for row in visible for tile in row})

    tile_to_char = {}
    for i, tile_id in enumerate(unique_ids):
        if i < len(TILE_CHARS):
            tile_to_char[tile_id] = TILE_CHARS[i]
        else:
            tile_to_char[tile_id] = "?"

    legend_parts = [f"{char}={tid}" for tid, char in
                    sorted(tile_to_char.items(), key=lambda x: x[1])]
    return tile_to_char, " ".join(legend_parts)


def _extract_collision_data(pyboy: PyBoy) -> Optional[List[List[int]]]:
    """Extract walkability collision data from PyBoy's game wrapper.

    Available for Pokemon Red/Blue via the built-in game wrapper plugin.
    Returns [y][x] grid where 0 = blocked, non-zero = walkable, or None.
    """
    try:
        collision = pyboy.game_area_collision()
        if collision is not None:
            if hasattr(collision, 'tolist'):
                return collision.tolist()
            return [list(row) for row in collision]
    except Exception as e:
        logger.debug(f"Collision data unavailable: {e}")
    return None


def _extract_sprites(pyboy: PyBoy) -> List[Dict[str, Any]]:
    """Extract all on-screen sprites with tile-coordinate positions."""
    sprites = []
    for i in range(MAX_SPRITES):
        sprite = pyboy.get_sprite(i)
        if sprite.on_screen:
            sprites.append({
                "index": i,
                "tile_x": sprite.x // 8,
                "tile_y": sprite.y // 8,
                "tile_id": sprite.tile_identifier,
            })
    return sprites



def _detect_player_movement(
    current_pos: Optional[Tuple[int, int]],
    previous_pos: Optional[Tuple[int, int]],
    game_state_info: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Compare current and previous player map-block positions.

    Returns a human-readable movement summary using directions instead of
    raw map coordinates (which confuse the model when they don't match
    the viewport grid).  Suppresses "didn't move" during dialogue/battle/
    cutscenes where the player isn't expected to move.
    """
    if current_pos is None:
        return None
    if previous_pos is None:
        return None  # No useful info on first reading

    if current_pos == previous_pos:
        # Don't report "didn't move" when the player can't move anyway
        if game_state_info and game_state_info.get("state") not in ("overworld",):
            return None
        return "Player didn't move (same position as last turn)"

    cx, cy = current_pos
    px, py = previous_pos
    dx = cx - px
    dy = cy - py

    parts = []
    if dy < 0:
        parts.append(f"{abs(dy)} UP")
    elif dy > 0:
        parts.append(f"{dy} DOWN")
    if dx < 0:
        parts.append(f"{abs(dx)} LEFT")
    elif dx > 0:
        parts.append(f"{dx} RIGHT")

    return f"Player moved {', '.join(parts)} since last turn"


# ---------------------------------------------------------------------------
# Warp / map data from Pokemon Red RAM
# ---------------------------------------------------------------------------

def _extract_warp_data(pyboy: PyBoy) -> Optional[Dict[str, Any]]:
    """Read warp positions and map info from Pokemon Red RAM.

    Returns a dict with map info, player map position, and a list of warps
    with relative directions from the player.  Returns None when data is
    unavailable (e.g. during title screen or non-Pokemon-Red games).
    """
    try:
        map_number = pyboy.memory[_ADDR_CUR_MAP]
        player_y = pyboy.memory[_ADDR_PLAYER_Y]
        player_x = pyboy.memory[_ADDR_PLAYER_X]
        map_height = pyboy.memory[_ADDR_MAP_HEIGHT]
        map_width = pyboy.memory[_ADDR_MAP_WIDTH]
        num_warps = pyboy.memory[_ADDR_NUM_WARPS]

        # Sanity checks — during menus / title screen these may be garbage
        if num_warps > _MAX_WARPS or map_height == 0 or map_width == 0:
            return None

        warps = []
        for i in range(num_warps):
            base = _ADDR_WARP_ENTRIES + (i * 4)
            wy = pyboy.memory[base]
            wx = pyboy.memory[base + 1]
            dest_warp = pyboy.memory[base + 2]
            dest_map = pyboy.memory[base + 3]

            dy = wy - player_y   # +south / -north
            dx = wx - player_x   # +east  / -west

            warps.append({
                "map_y": wy, "map_x": wx,
                "dy": dy, "dx": dx,
                "dest_map": dest_map,
                "dest_name": _MAP_NAMES.get(dest_map, f"Map 0x{dest_map:02X}"),
            })

        # Read map connections (seamless edge transitions like Pallet Town ↔ Route 1)
        connections = []
        try:
            conn_flags = pyboy.memory[_ADDR_MAP_CONNECTIONS]
            conn_addrs = [
                (0x08, "NORTH", _ADDR_NORTH_CONNECTED_MAP),
                (0x04, "SOUTH", _ADDR_SOUTH_CONNECTED_MAP),
                (0x02, "WEST",  _ADDR_WEST_CONNECTED_MAP),
                (0x01, "EAST",  _ADDR_EAST_CONNECTED_MAP),
            ]
            for bit, direction, addr in conn_addrs:
                if conn_flags & bit:
                    dest_map = pyboy.memory[addr]
                    connections.append({
                        "direction": direction,
                        "dest_map": dest_map,
                        "dest_name": _MAP_NAMES.get(dest_map, f"Map 0x{dest_map:02X}"),
                    })
        except Exception as e:
            logger.debug(f"Connection data unavailable: {e}")

        return {
            "map_number": map_number,
            "map_name": _MAP_NAMES.get(map_number, f"Map 0x{map_number:02X}"),
            "player_y": player_y,
            "player_x": player_x,
            "map_height": map_height,
            "map_width": map_width,
            "warps": warps,
            "connections": connections,
        }
    except Exception as e:
        logger.debug(f"Warp data unavailable: {e}")
        return None


def _extract_npc_data(pyboy: PyBoy) -> Optional[List[Dict[str, Any]]]:
    """Read NPC/item sprite data from Pokemon Red RAM.

    Returns a list of dicts with name, relative position (dy/dx in map tiles),
    picture ID, and an is_item flag.  Returns None when unavailable.
    """
    try:
        player_y = pyboy.memory[_ADDR_PLAYER_Y]
        player_x = pyboy.memory[_ADDR_PLAYER_X]
        num_sprites = pyboy.memory[_ADDR_NUM_SPRITES]

        if num_sprites == 0 or num_sprites > 15:
            logger.debug(f"NPC skip: num_sprites={num_sprites}")
            return None

        # Calibrate offset: sprite-state coords include a map-border offset
        # that wYCoord/wXCoord don't.  Derive it from sprite 0 (player).
        offset_y = pyboy.memory[_ADDR_SPRITE_STATE2 + 0x04] - player_y
        offset_x = pyboy.memory[_ADDR_SPRITE_STATE2 + 0x05] - player_x

        npcs = []
        for n in range(1, num_sprites + 1):
            pic_id = pyboy.memory[_ADDR_SPRITE_STATE1 + n * 0x10]
            if pic_id == 0:
                continue  # empty slot

            raw_y = pyboy.memory[_ADDR_SPRITE_STATE2 + n * 0x10 + 0x04]
            raw_x = pyboy.memory[_ADDR_SPRITE_STATE2 + n * 0x10 + 0x05]
            npc_y = raw_y - offset_y
            npc_x = raw_x - offset_x
            dy = npc_y - player_y
            dx = npc_x - player_x
            name = _SPRITE_NAMES.get(pic_id, "NPC")

            logger.debug(
                f"Sprite {n}: {name} (pic=0x{pic_id:02X}) "
                f"raw=({raw_x},{raw_y}) map=({npc_x},{npc_y}) "
                f"rel=({dx},{dy})"
            )

            npcs.append({
                "name": name,
                "dy": dy,
                "dx": dx,
                "pic_id": pic_id,
                "is_item": pic_id == _ITEM_SPRITE_ID,
            })

        logger.info(f"NPC extraction: {num_sprites} sprites on map, {len(npcs)} with pic_id != 0")
        return npcs if npcs else None
    except Exception as e:
        logger.warning(f"NPC data extraction failed: {e}", exc_info=True)
        return None


def _detect_game_state(pyboy: PyBoy) -> Dict[str, str]:
    """Detect the current high-level game state from RAM.

    Returns a dict with:
        state: "battle" | "scripted_event" | "dialogue" | "overworld"
        details: Human-readable description of the state
        input_hint: What inputs are valid in this state

    Dialogue detection uses wStatusFlags5 bit 0 (set by DisplayTextID,
    cleared by CloseTextDisplay) instead of wTextBoxID alone.  wTextBoxID
    is never cleared by the engine and stays stale after every text box —
    causing false "dialogue active" for entire sessions.  wJoyIgnore and
    wMenuWatchedKeys have the same staleness problem.  Bit 0 of
    wStatusFlags5 is the only address the engine reliably toggles.
    """
    try:
        battle = pyboy.memory[_ADDR_IS_IN_BATTLE]
        if battle != 0:
            kind = "wild battle" if battle == 1 else "trainer battle"
            return {
                "state": "battle",
                "details": f"In {kind}",
                "input_hint": "Use battle commands (A to select, B to cancel, arrows to navigate)",
            }

        status5 = pyboy.memory[_ADDR_STATUS_FLAGS5]
        if status5 & 0x20:  # bit 5 – joypad disabled
            scripted = "scripted movement" if status5 & 0x80 else "cutscene"
            return {
                "state": "scripted_event",
                "details": f"Input disabled ({scripted})",
                "input_hint": "Wait for the event to finish, then press A/B to advance",
            }

        text_box = pyboy.memory[_ADDR_TEXT_BOX_ID]
        walk = pyboy.memory[_ADDR_WALK_COUNTER]

        # Gate on bit 0 of wStatusFlags5: DisplayTextID sets it,
        # CloseTextDisplay clears it.  wTextBoxID alone is stale.
        if text_box != 0 and (status5 & 0x01):
            return {
                "state": "dialogue",
                "details": "Text/menu box active",
                "input_hint": "Press A to advance/select, B to cancel. If menu visible, use Up/Down to navigate",
            }

        # Fallback: Window layer visible means a text box or menu is on screen.
        # Some dialogues (e.g. Oak's Route 1 speech) don't set wStatusFlags5 bit 0
        # but still display via the Window layer.  WY < 144 = window is on screen.
        wy = pyboy.memory[_ADDR_WINDOW_Y]
        if wy < 144:
            return {
                "state": "dialogue",
                "details": "Text/menu visible (Window layer active)",
                "input_hint": "Press A to advance/select, B to cancel. Arrows to navigate menus.",
            }

        if walk != 0:
            return {
                "state": "overworld",
                "details": "Free movement (mid-step animation)",
                "input_hint": "Move with directions (U/D/L/R), A to interact, Start for menu",
            }

        return {
            "state": "overworld",
            "details": "Free movement",
            "input_hint": "Move with directions (U/D/L/R), A to interact, Start for menu",
        }
    except Exception as e:
        logger.debug(f"Game state detection unavailable: {e}")
        return {
            "state": "unknown",
            "details": "Could not detect game state",
            "input_hint": "",
        }


def _format_warp_text(
    warp_data: Optional[Dict[str, Any]],
    grid: Optional[List[List[str]]] = None,
    player_pos: Optional[Tuple[int, int]] = None,
) -> str:
    """Format warp/connection data with A*-computed paths when available."""
    if not warp_data:
        return ""

    has_connections = bool(warp_data.get("connections"))
    has_warps = bool(warp_data.get("warps"))

    if not has_connections and not has_warps:
        return ""

    # Import pathfinding lazily to avoid circular imports
    from claude_player.utils.pathfinding import find_path, find_path_to_edge, path_to_buttons

    can_pathfind = grid is not None and player_pos is not None

    lines = [
        f"Map: {warp_data['map_name']} (size={warp_data['map_width']}x{warp_data['map_height']})",
    ]

    # Map edge connections — walk off the edge to transition to adjacent map
    if has_connections:
        lines.append("Map edges (walk off edge — no warp tile needed):")
        _extra_step = {"NORTH": "U16", "SOUTH": "D16", "WEST": "L16", "EAST": "R16"}
        for conn in warp_data["connections"]:
            hint = ""
            if can_pathfind:
                edge_path = find_path_to_edge(grid, player_pos, conn["direction"])
                if edge_path:
                    buttons = path_to_buttons(edge_path)
                    # Add one extra tile to walk OFF the edge
                    extra = _extra_step.get(conn["direction"], "")
                    if buttons and extra:
                        buttons += f" {extra}"
                    elif extra:
                        buttons = extra
                    hint = f"  [path: {buttons}]" if buttons else ""
                else:
                    # Path blocked on visible grid — suggest perpendicular
                    # movement to scroll the view and reveal a gap.
                    perp = {"NORTH": "EAST or WEST", "SOUTH": "EAST or WEST",
                            "EAST": "NORTH or SOUTH", "WEST": "NORTH or SOUTH"}
                    hint = f"  [path blocked on screen — move {perp.get(conn['direction'], 'sideways')} to find a way around]"
            lines.append(f"  {conn['direction']} edge → {conn['dest_name']}{hint}")

    # Warp tiles — doors, stairs, cave entrances (step onto W tile)
    if has_warps:
        lines.append("Doors/Warps (step onto W tile):")
        for i, w in enumerate(warp_data["warps"]):
            dy, dx = w["dy"], w["dx"]

            parts = []
            if dy < 0:
                parts.append(f"{abs(dy)} UP")
            elif dy > 0:
                parts.append(f"{dy} DOWN")
            if dx < 0:
                parts.append(f"{abs(dx)} LEFT")
            elif dx > 0:
                parts.append(f"{dx} RIGHT")
            direction = ", ".join(parts) if parts else "ON THIS TILE"

            # Compute A* path to the warp tile
            hint = ""
            if can_pathfind:
                warp_grid_pos = (player_pos[0] + dx, player_pos[1] + dy)
                wgx, wgy = warp_grid_pos
                grid_h = len(grid)
                grid_w = len(grid[0]) if grid else 0
                if 0 <= wgx < grid_w and 0 <= wgy < grid_h:
                    warp_path = find_path(grid, player_pos, warp_grid_pos)
                    if warp_path:
                        buttons = path_to_buttons(warp_path)
                        if buttons:
                            hint = f"  [path: {buttons}]"
                        else:
                            # ON THIS TILE — need to re-trigger
                            hint = "  [on this tile — walk D16 to re-trigger exit]"
                    else:
                        hint = "  [no path found]"
                else:
                    # Warp is off the visible grid — give straight-line estimate
                    cmds = []
                    if dy < 0:
                        cmds.append(f"U{abs(dy) * 16}")
                    elif dy > 0:
                        cmds.append(f"D{dy * 16}")
                    if dx < 0:
                        cmds.append(f"L{abs(dx) * 16}")
                    elif dx > 0:
                        cmds.append(f"R{dx * 16}")
                    hint = f"  [not on screen, est. {' '.join(cmds)}]" if cmds else ""
            else:
                # Fallback: straight-line when no collision grid available
                cmds = []
                if dy < 0:
                    cmds.append(f"U{abs(dy) * 16}")
                elif dy > 0:
                    cmds.append(f"D{dy * 16}")
                if dx < 0:
                    cmds.append(f"L{abs(dx) * 16}")
                elif dx > 0:
                    cmds.append(f"R{dx * 16}")
                if cmds:
                    hint = f"  [straight-line: {' '.join(cmds)}]"
                else:
                    hint = "  [on this tile — walk D16 to re-trigger exit]"

            lines.append(
                f"  W{i}: {direction} -> {w['dest_name']}{hint}"
            )

    return "\n".join(lines)


def _overlay_npcs_on_grid(
    grid: List[List[str]],
    npc_data: Optional[List[Dict[str, Any]]],
    player_screen: Optional[Tuple[int, int]],
    scale: int = 2,
) -> None:
    """Overlay NPC/item markers on the grid.

    Digits 1-9 for NPCs, 'i' for item balls.
    scale: grid cells per map tile (1 for metatile grid, 2 for screen-tile grid).
    """
    if not npc_data or not player_screen:
        return

    grid_h = len(grid)
    grid_w = len(grid[0]) if grid else 0
    px, py = player_screen

    npc_num = 0
    for npc in npc_data:
        gx = px + npc["dx"] * scale
        gy = py + npc["dy"] * scale
        if 0 <= gx < grid_w and 0 <= gy < grid_h:
            if npc["is_item"]:
                grid[gy][gx] = "i"
            else:
                npc_num += 1
                grid[gy][gx] = str(npc_num) if npc_num <= 9 else "n"


def _format_npc_text(
    npc_data: Optional[List[Dict[str, Any]]],
    grid: Optional[List[List[str]]] = None,
    player_pos: Optional[Tuple[int, int]] = None,
) -> str:
    """Format NPC/item data with A*-computed paths when available."""
    if not npc_data:
        return ""

    from claude_player.utils.pathfinding import find_path, path_to_buttons

    can_pathfind = grid is not None and player_pos is not None

    npcs = [n for n in npc_data if not n["is_item"]]
    items = [n for n in npc_data if n["is_item"]]
    lines = []

    # Direction → face command (sub-16-frame press = turn without moving)
    _face_cmd = {(0, -1): "U1", (0, 1): "D1", (-1, 0): "L1", (1, 0): "R1"}

    def _dir_line(marker, entity):
        dy, dx = entity["dy"], entity["dx"]
        parts = []
        if dy < 0:
            parts.append(f"{abs(dy)} UP")
        elif dy > 0:
            parts.append(f"{dy} DOWN")
        if dx < 0:
            parts.append(f"{abs(dx)} LEFT")
        elif dx > 0:
            parts.append(f"{dx} RIGHT")
        direction = ", ".join(parts) if parts else "HERE"

        hint = ""
        if can_pathfind:
            px, py = player_pos
            tx, ty = px + dx, py + dy  # target grid position
            grid_h = len(grid)
            grid_w = len(grid[0]) if grid else 0

            if 0 <= tx < grid_w and 0 <= ty < grid_h:
                if entity["is_item"]:
                    # Items: path directly TO the item tile (step on it)
                    item_path = find_path(grid, player_pos, (tx, ty))
                    if item_path:
                        buttons = path_to_buttons(item_path)
                        hint = f"  [path: {buttons}]" if buttons else "  [already here]"
                    else:
                        hint = "  [no path found]"
                else:
                    # NPCs: path to best adjacent tile, then face + interact
                    adjacent = [(tx, ty - 1), (tx, ty + 1), (tx - 1, ty), (tx + 1, ty)]
                    best_path = None
                    best_adj = None
                    for ax, ay in adjacent:
                        if not (0 <= ax < grid_w and 0 <= ay < grid_h):
                            continue
                        if (ax, ay) == player_pos:
                            # Already adjacent — no movement needed
                            best_path = [player_pos]
                            best_adj = (ax, ay)
                            break
                        adj_path = find_path(grid, player_pos, (ax, ay))
                        if adj_path and (best_path is None or len(adj_path) < len(best_path)):
                            best_path = adj_path
                            best_adj = (ax, ay)

                    if best_path is not None and best_adj is not None:
                        buttons = path_to_buttons(best_path)
                        # Append facing command toward the NPC
                        face_dx = tx - best_adj[0]
                        face_dy = ty - best_adj[1]
                        face = _face_cmd.get((face_dx, face_dy), "")
                        if face:
                            buttons = f"{buttons} {face} A1".strip()
                        else:
                            buttons = f"{buttons} A1".strip()
                        hint = f"  [path: {buttons}]" if buttons else "  [already here]"
                    else:
                        hint = "  [no path found]"
            else:
                # Off screen — straight-line fallback
                cmds = []
                if dy < 0:
                    cmds.append(f"U{abs(dy) * 16}")
                elif dy > 0:
                    cmds.append(f"D{dy * 16}")
                if dx < 0:
                    cmds.append(f"L{abs(dx) * 16}")
                elif dx > 0:
                    cmds.append(f"R{dx * 16}")
                hint = f"  [off screen: {' '.join(cmds)}]" if cmds else ""
        else:
            # No collision grid — straight-line fallback
            cmds = []
            if dy < 0:
                cmds.append(f"U{abs(dy) * 16}")
            elif dy > 0:
                cmds.append(f"D{dy * 16}")
            if dx < 0:
                cmds.append(f"L{abs(dx) * 16}")
            elif dx > 0:
                cmds.append(f"R{dx * 16}")
            suggested = " ".join(cmds) if cmds else "(here)"
            hint = f"  [straight-line: {suggested}]"

        return f"  {marker}: {entity['name']} - {direction}{hint}"

    if npcs:
        lines.append("NPCs:")
        for idx, npc in enumerate(npcs, 1):
            marker = str(idx) if idx <= 9 else "n"
            lines.append(_dir_line(marker, npc))

    if items:
        lines.append("Items:")
        for item in items:
            lines.append(_dir_line("i", item))

    return "\n".join(lines)


def _format_spatial_text(
    collision: Optional[List[List[int]]],
    visible: List[List[int]],
    tile_to_char: Dict[int, str],
    sprites: List[Dict[str, Any]],
    warp_data: Optional[Dict[str, Any]],
    player_movement_text: Optional[str],
    npc_data: Optional[List[Dict[str, Any]]] = None,
    game_state_info: Optional[Dict[str, str]] = None,
    story_progress: Optional[Dict[str, Any]] = None,
) -> str:
    """Build simplified spatial context for continuous mode.

    The grid is downsampled to metatile resolution (1 cell = 1 map tile = 16px)
    so that grid cells, warp distances, and input frames all share one coordinate
    system.  Each cell on the grid corresponds to 16 frames of directional input.
    """
    has_collision = collision is not None
    src_height = len(collision) if has_collision else SCREEN_TILES_Y
    src_width = len(collision[0]) if has_collision and collision else SCREEN_TILES_X

    # Downsample to metatile resolution (2x2 screen-tiles → 1 cell)
    grid_height = src_height // 2
    grid_width = src_width // 2

    grid = []
    if has_collision:
        for y in range(grid_height):
            row = []
            for x in range(grid_width):
                # Blocked if ANY cell in the 2x2 block is 0
                walkable = all(
                    collision[y * 2 + dy][x * 2 + dx] != 0
                    for dy in range(2) for dx in range(2)
                )
                row.append("." if walkable else "#")
            grid.append(row)
    else:
        # Fallback: take top-left tile of each 2x2 block
        for y in range(grid_height):
            row = []
            for x in range(grid_width):
                row.append(tile_to_char.get(visible[y * 2][x * 2], "?"))
            grid.append(row)

    # Player screen position from OAM sprite 0
    # Pokemon uses 8x16 sprites — the OAM Y is the sprite top, but we need
    # the feet (collision) position which is 16px (2 tiles = 1 metatile) lower.
    # We must add +1 AFTER the //2 division, not before, so integer division
    # doesn't absorb the offset when tile_y is even.
    player_screen_pos = None
    if sprites:
        tx = sprites[0]["tile_x"] // 2
        ty = sprites[0]["tile_y"] // 2 + 1
        player_screen_pos = (tx, ty)

    # Overlay order: NPCs → player (@) → warps (W)
    _overlay_npcs_on_grid(grid, npc_data, player_screen_pos, scale=1)
    if player_screen_pos:
        px, py = player_screen_pos
        if 0 <= px < grid_width and 0 <= py < grid_height:
            grid[py][px] = "@"
    _overlay_warps_on_grid(grid, warp_data, player_screen_pos, scale=1)

    # Assemble output
    lines = ["=== SPATIAL CONTEXT ==="]

    # Game state from RAM-based heuristics
    if game_state_info and game_state_info["state"] != "unknown":
        state_line = f"GAME STATE: {game_state_info['state']} — {game_state_info['details']}"
        if game_state_info["input_hint"]:
            state_line += f" | {game_state_info['input_hint']}"
        lines.append(state_line)

    # Story progress from event flags
    if story_progress and story_progress.get("progress_summary"):
        lines.append(f"PROGRESS: {story_progress['progress_summary']}")

    # Player grid position (viewport-relative, matches the grid below)
    if player_screen_pos:
        lines.append(f"Player @ is at grid column {player_screen_pos[0]}, row {player_screen_pos[1]}")

    # Player movement status (directional, no raw map coords)
    if player_movement_text:
        lines.append(player_movement_text)

    # Grid with column index header
    lines.append("   " + "".join(str(x % 10) for x in range(grid_width)))
    for y in range(grid_height):
        lines.append(f"{y:2d} " + "".join(grid[y]))

    # Brief legend
    if has_collision:
        lines.append(". = walkable  # = blocked  W = exit  @ = player  1-9 = NPC  i = item  (1 cell = 16 frames)")

    # NPC/item text with A* paths
    npc_text = _format_npc_text(npc_data, grid if has_collision else None, player_screen_pos)
    if npc_text:
        lines.append(npc_text)

    # Warp/connection text with A* paths
    warp_text = _format_warp_text(warp_data, grid if has_collision else None, player_screen_pos)
    if warp_text:
        lines.append(warp_text)

    return "\n".join(lines)


def _overlay_warps_on_grid(
    grid: List[List[str]],
    warp_data: Optional[Dict[str, Any]],
    player_screen: Optional[Tuple[int, int]],
    scale: int = 2,
) -> None:
    """Best-effort overlay of 'W' markers on the grid for visible warps.

    scale: how many grid cells per map block.
           2 for screen-tile grids (8x8), 1 for metatile grids (16x16).
    """
    if not warp_data or not player_screen:
        return

    grid_h = len(grid)
    grid_w = len(grid[0]) if grid else 0
    px, py = player_screen

    for w in warp_data["warps"]:
        gx = px + w["dx"] * scale
        gy = py + w["dy"] * scale
        if 0 <= gx < grid_w and 0 <= gy < grid_h:
            grid[gy][gx] = "W"


def extract_spatial_context(
    pyboy: PyBoy,
    previous_tilemap: Optional[List[List[int]]] = None,
    previous_player_pos: Optional[Tuple[int, int]] = None,
) -> Dict[str, Any]:
    """Extract tilemap spatial context for the current frame.

    Args:
        pyboy: The PyBoy emulator instance.
        previous_tilemap: The visible tile grid from the previous turn (for change detection).
        previous_player_pos: (x, y) map-block position from the previous turn (for movement detection).

    Returns:
        {"text": str, "visible_tilemap": list, "player_pos": tuple|None}
    """
    try:
        visible, _ = _extract_visible_tilemap(pyboy)
        tile_to_char, _ = _build_tile_legend(visible)
        sprites = _extract_sprites(pyboy)
        collision = _extract_collision_data(pyboy)
        warp_data = _extract_warp_data(pyboy)
        npc_data = _extract_npc_data(pyboy)
        from claude_player.utils.event_flags import check_story_progress
        try:
            story_progress = check_story_progress(pyboy.memory.__getitem__)
        except Exception as e:
            logger.debug(f"Story progress unavailable: {e}")
            story_progress = None

        # Filter out hidden/event sprites whose positions are outside the map.
        # Pokemon Red loads ALL map sprites (including event-hidden ones like
        # Oak waiting on Route 1) — their MapY/MapX values are stale/invalid.
        if npc_data and warp_data:
            map_h = warp_data["map_height"]
            map_w = warp_data["map_width"]
            before = len(npc_data)
            npc_data = [
                npc for npc in npc_data
                if abs(npc["dy"]) <= map_h and abs(npc["dx"]) <= map_w
            ]
            if len(npc_data) < before:
                logger.info(
                    f"Filtered {before - len(npc_data)} out-of-bounds sprites "
                    f"(map {map_w}x{map_h}, kept {len(npc_data)})"
                )
            npc_data = npc_data or None

        player_map_pos = None
        if warp_data:
            player_map_pos = (warp_data["player_x"], warp_data["player_y"])
        game_state_info = _detect_game_state(pyboy)
        player_movement_text = _detect_player_movement(player_map_pos, previous_player_pos, game_state_info)

        text = _format_spatial_text(
            collision=collision,
            visible=visible,
            tile_to_char=tile_to_char,
            sprites=sprites,
            warp_data=warp_data,
            player_movement_text=player_movement_text,
            npc_data=npc_data,
            game_state_info=game_state_info,
            story_progress=story_progress,
        )

        return {
            "text": text,
            "visible_tilemap": visible,
            "player_pos": player_map_pos,
            "game_state": game_state_info,
            "story_progress": story_progress,
        }
    except Exception as e:
        logger.error(f"Error extracting spatial context: {e}")
        return {"text": "", "visible_tilemap": previous_tilemap, "player_pos": previous_player_pos, "game_state": None, "story_progress": None}
