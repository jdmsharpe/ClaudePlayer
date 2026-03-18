import logging
from typing import Dict, List, Tuple, Optional, Any
from pyboy import PyBoy

from claude_player.utils.ram_constants import (
    ADDR_IS_IN_BATTLE,
    ADDR_CUR_MAP,
    ADDR_PLAYER_Y,
    ADDR_PLAYER_X,
    ADDR_STATUS_FLAGS5,
    ADDR_WINDOW_Y,
    ADDR_TILE_PLAYER_ON,
    ADDR_DISABLE_JOYPAD,
)
from claude_player.data.maps import MAP_NAMES

# Game Boy visible screen: 160x144 pixels = 20x18 tiles of 8x8 pixels
SCREEN_TILES_X = 20
SCREEN_TILES_Y = 18
TILEMAP_SIZE = 32  # Background tilemap is always 32x32
MAX_SPRITES = 40

# Characters assigned to unique tile IDs (62 slots)
TILE_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pokemon Red/Blue RAM addresses (spatial-specific, from pret/pokered)
# ---------------------------------------------------------------------------
_ADDR_CUR_MAP = ADDR_CUR_MAP
_ADDR_PLAYER_Y = ADDR_PLAYER_Y
_ADDR_PLAYER_X = ADDR_PLAYER_X
_ADDR_MAP_HEIGHT = 0xD368     # In blocks
_ADDR_MAP_WIDTH = 0xD369      # In blocks
_ADDR_NUM_WARPS = 0xD3AE
_ADDR_WARP_ENTRIES = 0xD3AF   # 4 bytes each: Y, X, dest_warp_id, dest_map
_MAX_WARPS = 32
# Sign list immediately follows the variable-length warp list in RAM.
# Address computed at runtime: D3AF + num_warps * 4
_MAX_SIGNS = 16

# Maps where signs are PC terminals (Pokemon Centers)
_POKEMON_CENTER_MAPS = {
    0x29, 0x3A, 0x40, 0x44, 0x51, 0x59, 0x85, 0x8D, 0x9A, 0xAB, 0xB6,
}

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

# Sprite facing direction (offset 0x09 within SPRITESTATEDATA1, 16-byte stride)
# Values from pret/pokered: $00=DOWN, $04=UP, $08=LEFT, $0C=RIGHT
_FACING_OFFSET = 0x09
_FACING_MAP: Dict[int, str] = {
    0x00: "DOWN",
    0x04: "UP",
    0x08: "LEFT",
    0x0C: "RIGHT",
}

# Game state detection addresses (spatial-specific)
_ADDR_TEXT_BOX_ID    = 0xD125   # wTextBoxID – non-zero = text box active
_ADDR_IS_IN_BATTLE   = ADDR_IS_IN_BATTLE
_ADDR_WALK_COUNTER   = 0xCFC5   # wWalkCounter – non-zero = mid-step animation
_ADDR_STATUS_FLAGS5  = ADDR_STATUS_FLAGS5
_ADDR_WINDOW_Y       = ADDR_WINDOW_Y
_ADDR_SIM_JOYPAD_IDX = 0xCD38   # wSimulatedJoypadStatesIndex – non-zero = game running scripted input
_ADDR_DISABLE_JOYPAD = ADDR_DISABLE_JOYPAD
_ADDR_TILE_PLAYER_ON = ADDR_TILE_PLAYER_ON

# Terrain classification RAM addresses
_ADDR_GRASS_TILE       = 0xD535   # wGrassTile — grass tile value for current map
_ADDR_TILESET_TYPE     = 0xFFD7   # Tileset type: 0=indoor/building, 1=cave/dungeon, 2=outdoor (grass+water animations)
_ADDR_TILESET_COLL_PTR = 0xD530   # wTilesetCollisionPtr (2 bytes LE) → ROM collision pairs

# Ledge tile → direction (VRAM IDs = raw byte + 0x100)
# From pret/pokered data/tilesets/ledge_tiles.asm
_LEDGE_TILES: Dict[int, str] = {
    0x37 + 0x100: 'v',   # south ledge
    0x36 + 0x100: 'v',   # south ledge variant
    0x27 + 0x100: '<',   # west ledge
    0x0D + 0x100: '>',   # east ledge
    0x1D + 0x100: '>',   # east ledge variant
}

_WATER_TILE_VRAM = 0x14 + 0x100  # Primary water tile

# Cuttable tree BLOCK IDs (metatile-level, from pret/pokered data/tilesets/cut_tree_blocks.asm)
# Block IDs are unique per metatile, unlike VRAM sub-tiles which are shared
# between cuttable and decorative trees.
_CUT_TREE_BLOCKS_OVERWORLD = {0x32, 0x33, 0x34, 0x35, 0x60}  # Standard + variant
_CUT_GRASS_BLOCKS_OVERWORLD = {0x0B}                          # Cuttable tall grass

# RAM addresses for block map reading
_ADDR_MAP_TILESET       = 0xD367   # wCurMapTileset (0=OVERWORLD, 7=GYM, etc.)
_ADDR_VIEW_BLOCK_PTR    = 0xD35F   # wCurrentTileBlockMapViewPointer (2 bytes LE)
_ADDR_Y_BLOCK_COORD     = 0xD363   # wYBlockCoord: 0 or 1, sub-block offset
_ADDR_X_BLOCK_COORD     = 0xD364   # wXBlockCoord: 0 or 1, sub-block offset
_ADDR_OVERWORLD_MAP     = 0xC6E8   # wOverworldMap: block map buffer (1300 bytes)

_BOULDER_SPRITE_ID = 0x3F  # SPRITE_BOULDER — pushable with Strength

# Missable sprite detection — the engine's HideSprite/ShowSprite system.
# image_index == 0xFF is NOT reliable for ghost detection: off-screen sprites
# also get 0xFF since the Game Boy only writes image data for on-screen sprites.
# The correct approach: check wMissableObjectFlags, a packed bitfield where each
# bit corresponds to a global missable index.  wMissableObjectList maps
# (sprite_slot → missable_index) for the current map.
_ADDR_MISSABLE_FLAGS = 0xD5A6    # 32 bytes = 256 bits, one per missable object
_ADDR_MISSABLE_LIST  = 0xD5CE    # Pairs: (sprite_slot, missable_index), terminated by 0xFF

# Sprite picture ID → readable name (from pret/pokered sprite_constants.asm)
_SPRITE_NAMES = {
    0x01: "Player",
    0x02: "Rival",
    0x03: "Prof. Oak",
    0x04: "Youngster",
    0x05: "Monster",           # decoration (Slowbro, Nidorino)
    0x06: "Cooltrainer",
    0x07: "Cooltrainer",
    0x08: "Little Girl",
    0x09: "Bird",              # decoration
    0x0A: "Man",
    0x0B: "Gambler",
    0x0C: "Nerd",
    0x0D: "Girl",
    0x0E: "Hiker",
    0x0F: "Beauty",
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
    0x1B: "Worker",            # Silph Co. female worker
    0x1C: "Woman",
    0x1D: "Brunette Girl",
    0x1E: "Lance",
    0x1F: "Scientist",
    0x20: "Scientist",
    0x21: "Rocker",
    0x22: "Swimmer",
    0x23: "Safari Worker",
    0x24: "Gym Guide",
    0x25: "Gramps",
    0x26: "Clerk",
    0x27: "Fishing Guru",
    0x28: "Granny",
    0x29: "Nurse",
    0x2A: "Receptionist",
    0x2B: "Silph President",
    0x2C: "Worker",            # Silph Co. male worker
    0x2D: "Warden",
    0x2E: "Captain",
    0x2F: "Fisher",
    0x30: "Koga",
    0x31: "Guard",
    0x33: "Mom",
    0x34: "Balding Guy",
    0x35: "Little Boy",
    0x37: "Gameboy Kid",
    0x38: "Fairy",             # decoration (Clefairy)
    0x39: "Agatha",
    0x3A: "Bruno",
    0x3B: "Lorelei",
    0x3C: "Seel",              # decoration
    0x3D: "Poke Ball",
    0x3E: "Fossil",
    0x3F: "Boulder",
    0x40: "Sign",
    0x41: "Pokedex",
    0x42: "Clipboard",
    0x43: "Snorlax",
    0x45: "Old Amber",
    0x48: "Old Man (asleep)",  # Viridian coffee old man; pret calls it SPRITE_GAMBLER_ASLEEP
}
_ITEM_SPRITE_ID = 0x3D  # SPRITE_POKE_BALL

# Non-person interactable objects (bookshelves, fossils, boulders, etc.)
# Listed separately from NPCs in the spatial context output.
_OBJECT_SPRITE_IDS = {0x3E, 0x3F, 0x40, 0x41, 0x42, 0x43, 0x45}

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


def _build_tile_legend(visible: List[List[int]]) -> Dict[int, str]:
    """Assign a unique character to each unique tile ID in the visible grid."""
    unique_ids = sorted({tile for row in visible for tile in row})

    tile_to_char = {}
    for i, tile_id in enumerate(unique_ids):
        if i < len(TILE_CHARS):
            tile_to_char[tile_id] = TILE_CHARS[i]
        else:
            tile_to_char[tile_id] = "?"

    return tile_to_char


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


def _extract_terrain_data(pyboy: PyBoy) -> Optional[List[List[str]]]:
    """Classify visible tiles into terrain types for grid rendering.

    Returns a 9x10 (height x width) grid of single-char terrain markers:
        '.' = normal walkable
        '#' = blocked (wall/tree/etc.)
        ',' = tall grass (walkable, triggers wild encounters)
        'v' = south-facing ledge (one-way jump down)
        '>' = east-facing ledge (one-way jump right)
        '<' = west-facing ledge (one-way jump left)
        '=' = water (blocked without Surf)
        'T' = cuttable tree (blocked, need Cut HM)

    Walkability uses wTileMap (0xC3A0) raw tile IDs compared directly against
    the raw collision table — the same comparison CheckForCollision performs,
    with no VRAM addressing involved.  VRAM tile IDs are still read for ledge
    and water detection, which only applies on the OVERWORLD tileset.
    """
    try:
        grid_w = SCREEN_TILES_X // 2   # 10
        grid_h = SCREEN_TILES_Y // 2   # 9

        # wTileMap at 0xC3A0: 18 rows × 20 cols of raw tile IDs (0x00-0xFF),
        # already scroll-adjusted by the game engine.  Sample bottom-left tile
        # of each 2×2 metatile: row = my*2+1, col = mx*2.
        wmap_raw: List[List[int]] = []
        for my in range(grid_h):
            row: List[int] = []
            for mx in range(grid_w):
                idx = 0xC3A0 + (my * 2 + 1) * SCREEN_TILES_X + mx * 2
                row.append(pyboy.memory[idx])
            wmap_raw.append(row)

        # VRAM tilemap — only needed for ledge/water tile detection (overworld).
        tileset_type = pyboy.memory[_ADDR_TILESET_TYPE]
        map_tileset  = pyboy.memory[_ADDR_MAP_TILESET]
        need_vram = tileset_type > 0  # outdoor tileset: ledges, water, grass possible
        metatile_ids: List[List[int]] = []
        if need_vram:
            (scx_px, scy_px), _ = pyboy.screen.get_tilemap_position()
            scx = scx_px // 8
            scy = scy_px // 8
            bg = pyboy.tilemap_background[:, :]
            for my in range(grid_h):
                row_v: List[int] = []
                for mx in range(grid_w):
                    ty = (my * 2 + 1 + scy) % TILEMAP_SIZE
                    tx = (mx * 2 + scx) % TILEMAP_SIZE
                    row_v.append(bg[ty][tx])
                metatile_ids.append(row_v)

        # Build walkable set from raw collision table (no +0x100 offset).
        # The game engine compares raw wTileMap values directly against these.
        coll_ptr = (pyboy.memory[_ADDR_TILESET_COLL_PTR]
                    | (pyboy.memory[_ADDR_TILESET_COLL_PTR + 1] << 8))
        walkable_raw: set = set()
        for i in range(0x180):
            tile_val = pyboy.memory[coll_ptr + i]
            if tile_val == 0xFF:
                break
            walkable_raw.add(tile_val)

        # Grass tile (raw ID, only on outdoor tilesets)
        grass_raw = 0xFF
        if need_vram:
            grass_raw = pyboy.memory[_ADDR_GRASS_TILE]
            if grass_raw != 0xFF:
                walkable_raw.add(grass_raw)  # grass is walkable

        # Classify each metatile
        terrain: List[List[str]] = []
        for my in range(grid_h):
            row_out: List[str] = []
            for mx in range(grid_w):
                raw = wmap_raw[my][mx]
                tid = metatile_ids[my][mx] if need_vram else 0

                if need_vram and grass_raw != 0xFF and raw == grass_raw:
                    row_out.append(',')
                elif (need_vram and map_tileset == 0 and tid in _LEDGE_TILES):
                    # Ledge VRAM IDs only mean ledges in the OVERWORLD tileset (ID=0).
                    row_out.append(_LEDGE_TILES[tid])
                elif need_vram and tid == _WATER_TILE_VRAM:
                    row_out.append('=')
                elif raw in walkable_raw:
                    row_out.append('.')
                else:
                    row_out.append('#')
            terrain.append(row_out)

        return terrain
    except Exception as e:
        logger.debug(f"Terrain data unavailable: {e}")
        return None


def _extract_cut_tree_positions(pyboy: PyBoy) -> Optional[set]:
    """Detect cuttable trees by reading block IDs from the map block buffer.

    Returns a set of (grid_x, grid_y) positions where cuttable trees exist,
    or None if not in a relevant tileset.  Block IDs are unique per metatile
    so this avoids the false positives from VRAM sub-tile matching.
    """
    try:
        tileset = pyboy.memory[_ADDR_MAP_TILESET]
        if tileset == 0:  # OVERWORLD
            cuttable = _CUT_TREE_BLOCKS_OVERWORLD | _CUT_GRASS_BLOCKS_OVERWORLD
        else:
            return None

        view_ptr_lo = pyboy.memory[_ADDR_VIEW_BLOCK_PTR]
        view_ptr_hi = pyboy.memory[_ADDR_VIEW_BLOCK_PTR + 1]
        view_ptr = view_ptr_lo | (view_ptr_hi << 8)

        map_width = pyboy.memory[_ADDR_MAP_WIDTH]
        stride = map_width + 6  # 3-block border on each side

        # Sub-block offset: which metatile within the top-left block
        # aligns with grid position (0,0)
        x_off = pyboy.memory[_ADDR_X_BLOCK_COORD]
        y_off = pyboy.memory[_ADDR_Y_BLOCK_COORD]

        positions = set()
        # Read 6x6 blocks to cover the full 10x9 metatile grid + margin
        for by in range(6):
            for bx in range(6):
                addr = view_ptr + by * stride + bx
                block_id = pyboy.memory[addr]
                if block_id in cuttable:
                    # Each block covers a 2x2 metatile area in the grid.
                    # Only mark the bottom-left sub-tile (trunk / interaction
                    # point).  The top two tiles are canopy and the bottom-right
                    # is grass — marking all 4 creates a confusing triad of T's.
                    base_gx = bx * 2 - x_off
                    base_gy = by * 2 - y_off
                    positions.add((base_gx, base_gy + 1))  # bottom-left = trunk

        return positions if positions else None
    except Exception as e:
        logger.debug(f"Cut tree detection unavailable: {e}")
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


def _extract_player_facing(pyboy: PyBoy) -> Optional[str]:
    """Read the player sprite's facing direction from RAM.

    Returns "UP", "DOWN", "LEFT", "RIGHT", or None if unavailable.
    """
    try:
        raw = pyboy.memory[_ADDR_SPRITE_STATE1 + _FACING_OFFSET]
        return _FACING_MAP.get(raw)
    except Exception:
        return None


def _check_missable_hidden(pyboy: PyBoy, sprite_slot: int) -> bool:
    """Check if a sprite is hidden via wMissableObjectFlags.

    The engine's HideSprite command sets a bit in wMissableObjectFlags.
    wMissableObjectList maps sprite slots to global missable indices
    for the current map.

    Returns True if the sprite's missable flag is set (truly hidden).
    Returns False if the sprite is not missable or its flag is not set.
    """
    try:
        # Scan wMissableObjectList: pairs of (sprite_slot, missable_index)
        for i in range(0, 64, 2):  # max 32 entries
            slot = pyboy.memory[_ADDR_MISSABLE_LIST + i]
            if slot == 0xFF:
                break  # end of list
            if slot == sprite_slot:
                missable_index = pyboy.memory[_ADDR_MISSABLE_LIST + i + 1]
                byte_offset = missable_index // 8
                bit_offset = missable_index % 8
                flags_byte = pyboy.memory[_ADDR_MISSABLE_FLAGS + byte_offset]
                return bool(flags_byte & (1 << bit_offset))
        return False  # Not in missable list = always visible
    except Exception:
        return False  # On error, assume visible



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

        from claude_player.utils.warp_overrides import WARP_POSITION_OVERRIDES

        warps = []
        for i in range(num_warps):
            base = _ADDR_WARP_ENTRIES + (i * 4)
            wy = pyboy.memory[base]
            wx = pyboy.memory[base + 1]
            dest_warp = pyboy.memory[base + 2]
            dest_map = pyboy.memory[base + 3]

            # Apply per-map warp position correction if configured.
            override = WARP_POSITION_OVERRIDES.get((map_number, i))
            logger.debug(f"WARP_RAW map=0x{map_number:02X} warp={i} raw=({wy},{wx}) dest=0x{dest_map:02X} override={override}")
            if override:
                wy, wx = override

            dy = wy - player_y   # +south / -north
            dx = wx - player_x   # +east  / -west

            # Overshoot: 1 tile further in approach direction so agent walks
            # through the trigger tile rather than stopping just short of it.
            over_dy = dy + (1 if dy > 0 else -1 if dy < 0 else 0)
            over_dx = dx + (1 if dx > 0 else -1 if dx < 0 else 0)

            warps.append({
                "map_y": wy, "map_x": wx,
                "dy": dy, "dx": dx,           # raw RAM — used for display/overlay
                "over_dy": over_dy, "over_dx": over_dx,  # A* overshoot target
                "dest_map": dest_map,
                "dest_name": MAP_NAMES.get(dest_map, f"Map 0x{dest_map:02X}"),
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
                        "dest_name": MAP_NAMES.get(dest_map, f"Map 0x{dest_map:02X}"),
                    })
        except Exception as e:
            logger.debug(f"Connection data unavailable: {e}")

        # Read sign list. Follows the variable-length warp list: D3AF + num_warps * 4.
        is_pokecenter = map_number in _POKEMON_CENTER_MAPS
        addr_num_signs = _ADDR_WARP_ENTRIES + num_warps * 4
        num_signs = pyboy.memory[addr_num_signs]
        addr_sign_entries = addr_num_signs + 1
        signs = []
        if num_signs <= _MAX_SIGNS:
            for i in range(num_signs):
                base = addr_sign_entries + (i * 3)
                sy = pyboy.memory[base]
                sx = pyboy.memory[base + 1]
                signs.append({
                    "map_y": sy, "map_x": sx,
                    "dy": sy - player_y,
                    "dx": sx - player_x,
                    "label": "P" if is_pokecenter else "s",
                })

        return {
            "map_number": map_number,
            "map_name": MAP_NAMES.get(map_number, f"Map 0x{map_number:02X}"),
            "player_y": player_y,
            "player_x": player_x,
            "map_height": map_height,
            "map_width": map_width,
            "warps": warps,
            "connections": connections,
            "signs": signs,
        }
    except Exception as e:
        logger.debug(f"Warp data unavailable: {e}")
        return None


def _extract_npc_data(pyboy: PyBoy, map_number: Optional[int] = None) -> Optional[List[Dict[str, Any]]]:
    """Read NPC/item sprite data from Pokemon Red RAM.

    Returns a list of dicts with name, relative position (dy/dx in map tiles),
    picture ID, and an is_item flag.  Returns None when unavailable.
    """
    try:
        from claude_player.utils.npc_overrides import NPC_NAME_OVERRIDES
        player_y = pyboy.memory[_ADDR_PLAYER_Y]
        player_x = pyboy.memory[_ADDR_PLAYER_X]
        num_sprites = pyboy.memory[_ADDR_NUM_SPRITES]

        if num_sprites == 0 or num_sprites > 15:
            logger.debug(f"NPC skip: num_sprites={num_sprites}")
            return None

        # Calibrate offset: sprite-state coords include a map-border offset
        # that wYCoord/wXCoord don't.  Derive it from sprite 0 (player).
        # C2x4/C2x5 use 2x2 tile grid with base value 4 (Data Crystal RAM map).
        # During scripted events, sprite 0 may be (0,0) — fall back to the
        # standard border offset of 4.
        player_sprite_y = pyboy.memory[_ADDR_SPRITE_STATE2 + 0x04]
        player_sprite_x = pyboy.memory[_ADDR_SPRITE_STATE2 + 0x05]
        if player_sprite_y == 0 and player_sprite_x == 0:
            # Sprite state not initialised (scripted event) — use known border offset
            offset_y = 4
            offset_x = 4
            logger.debug(
                f"NPC offset: sprite0=(0,0) — using default border offset=4, "
                f"player_map=({player_x},{player_y})"
            )
        else:
            offset_y = player_sprite_y - player_y
            offset_x = player_sprite_x - player_x
            logger.debug(
                f"NPC offset: player_map=({player_x},{player_y}) "
                f"sprite0=({player_sprite_x},{player_sprite_y}) "
                f"offset=({offset_x},{offset_y})"
            )

        sprite_log = logger.debug  # always debug — terminal display already shows NPCs

        npcs = []
        for n in range(1, num_sprites + 1):
            pic_id = pyboy.memory[_ADDR_SPRITE_STATE1 + n * 0x10]
            if pic_id == 0:
                continue  # empty slot

            # Ghost detection via wMissableObjectFlags — the engine's actual
            # HideSprite/ShowSprite system.  image_index == 0xFF is NOT reliable:
            # off-screen sprites also get 0xFF since the Game Boy only writes
            # image data for sprites within the 160x144 viewport.  This caused
            # Prof. Oak (at the top of his lab, off-viewport) to be ghost-filtered
            # even though he's fully interactable.
            movement_status = pyboy.memory[_ADDR_SPRITE_STATE1 + n * 0x10 + 0x01]
            image_index = pyboy.memory[_ADDR_SPRITE_STATE1 + n * 0x10 + 0x02]
            is_ghost = _check_missable_hidden(pyboy, n)

            # Facing direction (offset 0x09): $00=DOWN, $04=UP, $08=LEFT, $0C=RIGHT
            facing_raw = pyboy.memory[_ADDR_SPRITE_STATE1 + n * 0x10 + _FACING_OFFSET]
            facing = _FACING_MAP.get(facing_raw)

            raw_y = pyboy.memory[_ADDR_SPRITE_STATE2 + n * 0x10 + 0x04]
            raw_x = pyboy.memory[_ADDR_SPRITE_STATE2 + n * 0x10 + 0x05]
            npc_y = raw_y - offset_y
            npc_x = raw_x - offset_x
            dy = npc_y - player_y
            dx = npc_x - player_x
            name = NPC_NAME_OVERRIDES.get((map_number, n), _SPRITE_NAMES.get(pic_id, "NPC"))

            sprite_log(
                f"Sprite {n}: {name} (pic=0x{pic_id:02X}) "
                f"raw=({raw_x},{raw_y}) map=({npc_x},{npc_y}) "
                f"rel=({dx},{dy}) facing={facing} img=0x{image_index:02X} mvst=0x{movement_status:02X}{' [ghost]' if is_ghost else ''}"
            )

            npcs.append({
                "name": name,
                "dy": dy,
                "dx": dx,
                "pic_id": pic_id,
                "is_item": pic_id == _ITEM_SPRITE_ID,
                "is_object": pic_id in _OBJECT_SPRITE_IDS,
                "is_ghost": is_ghost,
                "facing": facing,
            })

        sprite_log(f"NPC extraction: {num_sprites} sprites on map, {len(npcs)} with pic_id != 0")
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
        disable_joy = pyboy.memory[_ADDR_DISABLE_JOYPAD]  # hDisableJoypadPolling
        if (status5 & 0x20) or disable_joy:  # WRAM bit5 or HRAM joypad-disable flag
            scripted = "scripted movement" if status5 & 0x80 else "cutscene"
            return {
                "state": "scripted_event",
                "details": f"Input disabled ({scripted})",
                "input_hint": "Wait for the event to finish, then press A/B to advance",
            }

        # Simulated joypad: game is replaying a scripted input sequence
        # (e.g. Oak walking to the table, player being moved by a script).
        # Player input is ignored while this index is non-zero.
        sim_joy = pyboy.memory[_ADDR_SIM_JOYPAD_IDX]
        if sim_joy != 0:
            return {
                "state": "scripted_event",
                "details": "Scripted movement in progress",
                "input_hint": "Wait — game is running an automatic sequence. Press A to advance when it ends.",
            }

        text_box = pyboy.memory[_ADDR_TEXT_BOX_ID]
        walk = pyboy.memory[_ADDR_WALK_COUNTER]

        # Gate on bit 0 of wStatusFlags5: DisplayTextID sets it,
        # CloseTextDisplay clears it.  wTextBoxID alone is stale.
        if text_box != 0 and (status5 & 0x01):
            return {
                "state": "dialogue",
                "details": "Text/menu box active",
                "input_hint": "Press A to advance/select, B to cancel. If menu visible, use Up/Down to navigate. For multi-line dialogue, send A A A A A to advance quickly.",
            }

        # Fallback: Window layer visible means a text box or menu is on screen.
        # Some dialogues (e.g. Oak's Route 1 speech) don't set wStatusFlags5 bit 0
        # but still display via the Window layer.  WY < 144 = window is on screen.
        wy = pyboy.memory[_ADDR_WINDOW_Y]
        if wy < 144:
            return {
                "state": "dialogue",
                "details": "Text/menu visible (Window layer active)",
                "input_hint": "Press A to advance/select, B to cancel. Arrows to navigate menus. For multi-line dialogue, send A A A A A to advance quickly.",
            }

        # Player sprite not initialised — likely a map script is controlling
        # the player (e.g. Oak's Lab intro).  Sprite0 at C204/C205 should have
        # the border offset (≥4); (0,0) means the entry hasn't been written.
        # BUT: also check picture ID at C100 — if non-zero the sprite IS loaded
        # (state2 coords lag behind during map transitions).
        sprite0_y = pyboy.memory[_ADDR_SPRITE_STATE2 + 0x04]
        sprite0_x = pyboy.memory[_ADDR_SPRITE_STATE2 + 0x05]
        sprite0_pic = pyboy.memory[_ADDR_SPRITE_STATE1]
        if sprite0_y == 0 and sprite0_x == 0 and sprite0_pic == 0:
            return {
                "state": "scripted_event",
                "details": "Player sprite not initialised (script may be running)",
                "input_hint": "Press A to try advancing, otherwise wait for the script to finish.",
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
    from claude_player.utils.pathfinding import find_path, find_path_to_edge, path_to_buttons, DEFAULT_BLOCKED

    can_pathfind = grid is not None and player_pos is not None

    def _edge_opening_hint(direction: str) -> str:
        """Scan grid edge for nearest walkable cell and return a directional hint.

        Only gives directional advice when A* can verify a path to the opening.
        Otherwise tells the agent to explore freely toward the direction.
        """
        if not grid or not player_pos:
            return ""
        gh, gw = len(grid), len(grid[0])
        px, py = player_pos

        # Collect openings on the target edge
        openings: list = []
        if direction in ("NORTH", "SOUTH"):
            row = 0 if direction == "NORTH" else gh - 1
            for x in range(gw):
                if grid[row][x] not in DEFAULT_BLOCKED:
                    openings.append((x, row))
        elif direction in ("EAST", "WEST"):
            col = gw - 1 if direction == "EAST" else 0
            for y in range(gh):
                if grid[y][col] not in DEFAULT_BLOCKED:
                    openings.append((col, y))

        if openings:
            # Sort by Manhattan distance to player
            openings.sort(key=lambda pos: abs(pos[0] - px) + abs(pos[1] - py))
            # Try A* to closest openings — only trust hint if path exists
            for ox, oy in openings[:3]:
                path = find_path(grid, player_pos, (ox, oy))
                if path:
                    buttons = path_to_buttons(path)
                    return f"  [path to edge opening: {buttons}]" if buttons else ""
            # A* failed for all openings — don't give misleading directional hint
            return f"  [no reachable path to {direction} edge on current screen — explore or scroll view toward {direction}]"

        perp = {"NORTH": "EAST or WEST", "SOUTH": "EAST or WEST",
                "EAST": "NORTH or SOUTH", "WEST": "NORTH or SOUTH"}
        return f"  [edge fully blocked — move {perp.get(direction, 'sideways')} to scroll view]"

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
                    hint = _edge_opening_hint(conn["direction"])
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

            # Compute A* path to the warp tile.
            # Try overshoot (1 tile past trigger) first so agent walks through it;
            # fall back to exact warp tile if overshoot is off-screen or blocked.
            hint = ""
            if can_pathfind:
                over_dy, over_dx = w["over_dy"], w["over_dx"]
                grid_h = len(grid)
                grid_w = len(grid[0]) if grid else 0

                over_pos = (player_pos[0] + over_dx, player_pos[1] + over_dy)
                exact_pos = (player_pos[0] + dx, player_pos[1] + dy)

                ovx, ovy = over_pos
                over_in_grid = 0 <= ovx < grid_w and 0 <= ovy < grid_h
                # Mark the warp tile as passable so A* routes THROUGH it
                # to reach the overshoot (not around it).
                over_path = find_path(grid, player_pos, over_pos,
                                      extra_passable={exact_pos}) if over_in_grid else None

                if over_path:
                    buttons = path_to_buttons(over_path)
                    hint = f"  [path: {buttons}]" if buttons else "  [on this tile — step onto W]"
                else:
                    # Overshoot blocked or off-screen — path to exact trigger tile
                    wgx, wgy = exact_pos
                    if 0 <= wgx < grid_w and 0 <= wgy < grid_h:
                        warp_path = find_path(grid, player_pos, exact_pos)
                        if warp_path:
                            buttons = path_to_buttons(warp_path)
                            hint = f"  [path: {buttons}]" if buttons else "  [on this tile — step onto W]"
                        else:
                            hint = ("  [Warp visible but walls block direct path —"
                                    " requires an indirect route."
                                    " Try a different approach direction (e.g. SOUTH or EAST)"
                                    " to find a path that curves around to this warp]")
                    else:
                        hint = "  [off screen — use compass below]"
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
                    hint = "  [on this tile — step onto W]"

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

    Digits 1-9 for NPCs, 'i' for item balls, 'o' for objects.
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
            if npc.get("is_ghost"):
                grid[gy][gx] = "g"
            elif npc["is_item"]:
                grid[gy][gx] = "i"
            elif npc.get("is_object"):
                if npc.get("pic_id") == _BOULDER_SPRITE_ID:
                    grid[gy][gx] = "B"
                else:
                    grid[gy][gx] = "o"
            else:
                npc_num += 1
                grid[gy][gx] = str(npc_num) if npc_num <= 9 else "n"


def _format_npc_text(
    npc_data: Optional[List[Dict[str, Any]]],
    grid: Optional[List[List[str]]] = None,
    player_pos: Optional[Tuple[int, int]] = None,
    player_facing: Optional[str] = None,
) -> str:
    """Format NPC/item data with A*-computed paths when available."""
    if not npc_data:
        return ""

    from claude_player.utils.pathfinding import find_path, path_to_buttons

    can_pathfind = grid is not None and player_pos is not None

    # Exclude ghosts from agent text — they show on grid as 'g' but aren't interactable
    active = [n for n in npc_data if not n.get("is_ghost")]
    npcs = [n for n in active if not n["is_item"] and not n.get("is_object")]
    objects = [n for n in active if n.get("is_object")]
    items = [n for n in active if n["is_item"]]
    lines = []

    # Direction → face command (sub-16-frame press = turn without moving)
    _face_cmd = {(0, -1): "U2", (0, 1): "D2", (-1, 0): "L2", (1, 0): "R2"}
    # Delta → facing name for comparison with player_facing
    _face_dir_name = {(0, -1): "UP", (0, 1): "DOWN", (-1, 0): "LEFT", (1, 0): "RIGHT"}

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
                    # Items: try path directly TO the tile (overworld pickups).
                    # If blocked (item on table), fall through to adjacent+face.
                    item_path = find_path(grid, player_pos, (tx, ty))
                    if item_path:
                        buttons = path_to_buttons(item_path)
                        hint = f"  [path: {buttons}]" if buttons else "  [already here]"
                if not entity["is_item"] or (entity["is_item"] and not hint):
                    # NPCs/objects: path to best adjacent tile, then face + interact.
                    # Also used as fallback for items on blocked tiles (tables).
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
                        # Append facing command toward the NPC (skip if already facing)
                        face_dx = tx - best_adj[0]
                        face_dy = ty - best_adj[1]
                        needed_facing = _face_dir_name.get((face_dx, face_dy))
                        already_facing = False
                        if len(best_path) >= 2:
                            # After walking, player faces the last step direction
                            last_dx = best_path[-1][0] - best_path[-2][0]
                            last_dy = best_path[-1][1] - best_path[-2][1]
                            already_facing = (last_dx, last_dy) == (face_dx, face_dy)
                        elif player_facing and needed_facing == player_facing:
                            # Standing still — use RAM-read facing
                            already_facing = True
                        if already_facing:
                            buttons = f"{buttons} A".strip()
                        else:
                            face = _face_cmd.get((face_dx, face_dy), "")
                            if face:
                                buttons = f"{buttons} {face} A".strip()
                            else:
                                buttons = f"{buttons} A".strip()
                        hint = f"  [path: {buttons}]" if buttons else "  [already here]"
                    else:
                        hint = "  [UNREACHABLE — walls block all paths, do NOT attempt]"
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

    if objects:
        lines.append("Objects:")
        for obj in objects:
            lines.append(_dir_line("o", obj))

    return "\n".join(lines)


def _format_spatial_text(
    collision: Optional[List[List[int]]],
    visible: List[List[int]],
    tile_to_char: Dict[int, str],
    sprites: List[Dict[str, Any]],
    warp_data: Optional[Dict[str, Any]],
    npc_data: Optional[List[Dict[str, Any]]] = None,
    game_state_info: Optional[Dict[str, str]] = None,
    story_progress: Optional[Dict[str, Any]] = None,
    terrain: Optional[List[List[str]]] = None,
    cut_tree_positions: Optional[set] = None,
    player_facing: Optional[str] = None,
    tile_terrain: Optional[str] = None,
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
    if terrain is not None:
        # Terrain is walkability ground truth: _extract_terrain_data now uses
        # the correct $8800 VRAM ID conversion, so it matches the game engine.
        grid = [row[:] for row in terrain]
        grid_height = len(grid)
        grid_width = len(grid[0]) if grid else 0
    elif has_collision:
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

    # Overlay cuttable trees: mark blocked cells whose block IDs are cuttable
    if cut_tree_positions:
        for y in range(grid_height):
            for x in range(grid_width):
                if grid[y][x] == '#' and (x, y) in cut_tree_positions:
                    grid[y][x] = 'T'

    # Player screen position from OAM sprite 0
    # Pokemon uses 8x16 sprites — the OAM Y is the sprite top.  The feet
    # (collision point) are in the LOWER 8px of the sprite = tile_y + 1.
    # We must add +1 BEFORE the //2 division so that even tile_y values
    # (e.g. tile_y=10, feet at tile 11 = metatile 5) don't get bumped to
    # the next metatile (10//2+1=6 is wrong; (10+1)//2=5 is correct).
    player_screen_pos = None
    if sprites:
        tx = sprites[0]["tile_x"] // 2
        ty = (sprites[0]["tile_y"] + 1) // 2
        player_screen_pos = (tx, ty)

    # Overlay order: NPCs → player (@) → warps (W)
    _overlay_npcs_on_grid(grid, npc_data, player_screen_pos, scale=1)
    if player_screen_pos:
        px, py = player_screen_pos
        if 0 <= px < grid_width and 0 <= py < grid_height:
            grid[py][px] = "@"
    _overlay_warps_on_grid(grid, warp_data, player_screen_pos, scale=1)
    _overlay_signs_on_grid(grid, warp_data, player_screen_pos, scale=1)

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

    # Context-aware hint for current milestone + map
    if story_progress and story_progress.get("next") and warp_data:
        from claude_player.utils.event_flags import get_map_hint
        hint = get_map_hint(story_progress["next"][0], warp_data["map_number"])
        if hint:
            lines.append(f"HINT: {hint}")

    # Absolute map position + compass for off-screen exits
    _compass_targets: list = []  # (direction_label, manhattan_dist, dest_name, is_pokecenter)
    if warp_data:
        px_abs = warp_data["player_x"]
        py_abs = warp_data["player_y"]
        mw = warp_data["map_width"]
        mh = warp_data["map_height"]
        # Player coords are in step units (2 steps per map block), map dims are in blocks.
        # Multiply dims by 2 so the coordinate spaces match (avoids "at edge" confusion).
        map_name = warp_data["map_name"]
        map_line = f"MAP (RAM): {map_name} | Position: ({px_abs}, {py_abs}) of {mw*2}x{mh*2} — trust this over visual appearance"
        if tile_terrain:
            map_line += f" | on: {tile_terrain}"
        lines.append(map_line)

        # Pre-compute which immediate directions are blocked (for compass warnings)
        _immediate_blocked: set = set()
        _warp_adjacent: set = set()  # directions with a building-warp tile immediately adjacent
        _ledge_pass = {'v': (0, 1), '>': (1, 0), '<': (-1, 0)}
        _walkable = {'.', ',', '@', 'g'}  # floor, grass, player, ghost — NOT 'W' (warp teleports) or signs (solid)
        if player_screen_pos and grid:
            _cpx, _cpy = player_screen_pos
            for _lbl, _dx, _dy in [("UP", 0, -1), ("DOWN", 0, 1),
                                    ("LEFT", -1, 0), ("RIGHT", 1, 0)]:
                _nx, _ny = _cpx + _dx, _cpy + _dy
                if 0 <= _nx < grid_width and 0 <= _ny < grid_height:
                    _c = grid[_ny][_nx]
                    if _c in _ledge_pass:
                        if (_dx, _dy) != _ledge_pass[_c]:
                            _immediate_blocked.add(_lbl)
                    elif _c == 'W':
                        _warp_adjacent.add(_lbl)  # building entrance — teleports, warn separately
                    elif _c not in _walkable:
                        _immediate_blocked.add(_lbl)

        # Compass bearings to warps/connections that are far off-screen
        viewport_half_w = grid_width // 2  # ~5
        viewport_half_h = grid_height // 2  # ~4
        _DIR_MAP = {"NORTH": "UP", "SOUTH": "DOWN", "WEST": "LEFT", "EAST": "RIGHT"}
        compass_lines: list = []
        for w in warp_data.get("warps", []):
            if abs(w["dy"]) > viewport_half_h or abs(w["dx"]) > viewport_half_w:
                # Primary direction = axis with larger distance; list it FIRST
                # so game_agent can identify primary vs secondary by keyword order.
                if abs(w["dy"]) >= abs(w["dx"]):
                    pri = "UP" if w["dy"] < 0 else "DOWN"
                else:
                    pri = "LEFT" if w["dx"] < 0 else "RIGHT"
                parts = []
                if pri in ("UP", "DOWN"):
                    if w["dy"] < 0:
                        parts.append(f"~{abs(w['dy'])} blocks UP")
                    elif w["dy"] > 0:
                        parts.append(f"~{w['dy']} blocks DOWN")
                    if w["dx"] < 0:
                        parts.append(f"~{abs(w['dx'])} blocks LEFT")
                    elif w["dx"] > 0:
                        parts.append(f"~{w['dx']} blocks RIGHT")
                else:
                    if w["dx"] < 0:
                        parts.append(f"~{abs(w['dx'])} blocks LEFT")
                    elif w["dx"] > 0:
                        parts.append(f"~{w['dx']} blocks RIGHT")
                    if w["dy"] < 0:
                        parts.append(f"~{abs(w['dy'])} blocks UP")
                    elif w["dy"] > 0:
                        parts.append(f"~{w['dy']} blocks DOWN")
                if parts:
                    note = ""
                    if pri in _warp_adjacent:
                        note = (f" (CAUTION: building entrance 1 step {pri}"
                                f" — move LEFT or RIGHT first to avoid entering it)")
                    elif pri in _immediate_blocked:
                        note = f" ({pri} blocked here — detour around obstacle)"
                    compass_lines.append(f"  {w['dest_name']}: {', '.join(parts)}{note}")
                    _is_pc = w.get("dest_map") in _POKEMON_CENTER_MAPS
                    _compass_targets.append((pri, abs(w["dy"]) + abs(w["dx"]), w['dest_name'], _is_pc))
        for conn in warp_data.get("connections", []):
            d = conn["direction"]
            if d == "NORTH":
                dist = py_abs
            elif d == "SOUTH":
                dist = mh - 1 - py_abs
            elif d == "WEST":
                dist = px_abs
            elif d == "EAST":
                dist = mw - 1 - px_abs
            else:
                continue
            threshold = viewport_half_h if d in ("NORTH", "SOUTH") else viewport_half_w
            if dist > threshold:
                move_dir = _DIR_MAP.get(d, d)
                note = ""
                if move_dir in _warp_adjacent:
                    note = (f" (CAUTION: building entrance 1 step {move_dir}"
                            f" — move LEFT or RIGHT first to avoid entering it)")
                elif move_dir in _immediate_blocked:
                    note = f" ({move_dir} blocked here — detour around obstacle)"
                compass_lines.append(f"  {conn['dest_name']}: ~{dist} blocks {d}{note}")
                _compass_targets.append((move_dir, dist, conn['dest_name'], False))
        if compass_lines:
            lines.append("COMPASS (off-screen exits — crow-flies bearing, NOT a path — follow NAV below):")
            lines.extend(compass_lines)

    # Grid with column index header
    lines.append("   " + "".join(str(x % 10) for x in range(grid_width)))
    for y in range(grid_height):
        lines.append(f"{y:2d} " + "".join(grid[y]))

    # NAV: A* path toward off-screen compass targets through visible obstacles.
    # This gives multi-step maze navigation instead of just 1-tile MOVES.
    # Different from the old broken "path to edge for off-screen warps" approach:
    # labeled as a local step (re-evaluate after), not a complete route.
    _MOVE_TO_EDGE = {"UP": "NORTH", "DOWN": "SOUTH", "LEFT": "WEST", "RIGHT": "EAST"}
    if (_compass_targets and player_screen_pos and grid
            and (has_collision or terrain is not None)):
        from claude_player.utils.pathfinding import find_path_to_edge, path_to_buttons
        # Sort: non-Pokemon-Centers first (caves, gyms, connections are real goals),
        # then furthest-first within each group. This prevents the NAV from
        # directing toward a Pokemon Center when a cave entrance or route exit
        # is the actual navigation target.
        _compass_targets.sort(key=lambda t: (t[3], -t[1]))
        _nav_shown = False
        for _ct_dir, _ct_dist, _ct_name, _ct_pc in _compass_targets:
            edge_name = _MOVE_TO_EDGE.get(_ct_dir)
            if not edge_name:
                continue
            edge_path = find_path_to_edge(grid, player_screen_pos, edge_name)
            if edge_path:
                buttons = path_to_buttons(edge_path)
                if buttons:
                    lines.append(
                        f"NAV: toward {_ct_name} ({_ct_dir}): {buttons}"
                        f" — follow this, then re-evaluate"
                    )
                    _nav_shown = True
                    break
        if not _nav_shown and _compass_targets:
            _ct_dir = _compass_targets[0][0]
            _ct_name = _compass_targets[0][2]
            # Try perpendicular directions to scroll view
            _perp = {"UP": ["LEFT", "RIGHT"], "DOWN": ["LEFT", "RIGHT"],
                     "LEFT": ["UP", "DOWN"], "RIGHT": ["UP", "DOWN"]}
            _fallback_shown = False
            for alt_dir in _perp.get(_ct_dir, []):
                alt_edge = _MOVE_TO_EDGE.get(alt_dir)
                if alt_edge:
                    alt_path = find_path_to_edge(grid, player_screen_pos, alt_edge)
                    if alt_path:
                        alt_buttons = path_to_buttons(alt_path)
                        if alt_buttons:
                            lines.append(
                                f"NAV: {_ct_dir} blocked — detour {alt_dir}: {alt_buttons}"
                                f" — scroll view to find {_ct_dir} passage"
                            )
                            _fallback_shown = True
                            break
            if not _fallback_shown:
                # All local routes blocked — don't suggest going backward (causes ping-pong).
                # Instead, give a neutral hint: shift position sideways a few tiles to
                # scroll the viewport and reveal new paths.
                lines.append(
                    f"NAV: no local path toward {_ct_name} ({_ct_dir}) visible in current view"
                    f" — shift position 2-3 tiles LEFT or RIGHT to scroll view and find a gap"
                )

    # NPC/item text with A* paths
    has_grid = has_collision or terrain is not None
    npc_text = _format_npc_text(npc_data, grid if has_grid else None, player_screen_pos, player_facing)
    if npc_text:
        lines.append(npc_text)

    # Warp/connection text with A* paths
    warp_text = _format_warp_text(warp_data, grid if has_grid else None, player_screen_pos)
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
        if 0 <= gx < grid_w and 0 <= gy < grid_h and (gx, gy) != (px, py):
            grid[gy][gx] = "W"
            # Doormat fix: the tile immediately before a directional warp may
            # be reported as '#' by PyBoy collision but is passable in-game.
            # Force it walkable so A* can path through to the W tile.
            if w["dx"] != 0 or w["dy"] != 0:
                sy = 1 if w["dy"] > 0 else -1 if w["dy"] < 0 else 0
                sx = 1 if w["dx"] > 0 else -1 if w["dx"] < 0 else 0
                back_gx, back_gy = gx - sx, gy - sy
                if (0 <= back_gx < grid_w and 0 <= back_gy < grid_h
                        and (back_gx, back_gy) != (px, py)
                        and abs(back_gx - px) + abs(back_gy - py) > 1
                        and grid[back_gy][back_gx] == '#'):
                    grid[back_gy][back_gx] = '.'


def _overlay_signs_on_grid(
    grid: List[List[str]],
    warp_data: Optional[Dict[str, Any]],
    player_screen: Optional[Tuple[int, int]],
    scale: int = 1,
) -> None:
    """Overlay sign markers ('s' or 'P') on the grid for visible signs."""
    if not warp_data or not player_screen:
        return
    signs = warp_data.get("signs", [])
    if not signs:
        return
    grid_h = len(grid)
    grid_w = len(grid[0]) if grid else 0
    px, py = player_screen
    for sign in signs:
        gx = px + sign["dx"] * scale
        gy = py + sign["dy"] * scale
        if 0 <= gx < grid_w and 0 <= gy < grid_h and (gx, gy) != (px, py):
            existing = grid[gy][gx]
            # Don't overwrite NPC numbers, items, boulders, or ghosts
            if existing not in ('i', 'B', 'g') and not existing.isdigit():
                grid[gy][gx] = sign["label"]


def extract_spatial_context(
    pyboy: PyBoy,
    previous_tilemap: Optional[List[List[int]]] = None,
    previous_player_pos: Optional[Tuple[int, int]] = None,
    visited_maps: Optional[set] = None,
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
        tile_to_char = _build_tile_legend(visible)
        sprites = _extract_sprites(pyboy)
        collision = _extract_collision_data(pyboy)
        terrain = _extract_terrain_data(pyboy)
        cut_tree_pos = _extract_cut_tree_positions(pyboy)
        warp_data = _extract_warp_data(pyboy)
        npc_data = _extract_npc_data(pyboy, map_number=warp_data["map_number"] if warp_data else None)
        # Track current map in visited_maps for visit-check milestones
        if visited_maps is not None and warp_data:
            visited_maps.add(warp_data["map_number"])

        from claude_player.utils.event_flags import check_story_progress
        try:
            story_progress = check_story_progress(
                pyboy.memory.__getitem__, visited_maps=visited_maps
            )
        except Exception as e:
            logger.debug(f"Story progress unavailable: {e}")
            story_progress = None

        # Filter out hidden/event sprites with stale positions far from player.
        # Pokemon Red loads ALL map sprites (including event-hidden ones like
        # Oak waiting on Route 1) — their MapY/MapX values can be stale.
        # Use the visible screen grid (10x9) as the bound, not map dimensions,
        # because small maps (5x6) were incorrectly filtering valid nearby NPCs.
        if npc_data:
            before = len(npc_data)
            npc_data = [
                npc for npc in npc_data
                if abs(npc["dy"]) <= 10 and abs(npc["dx"]) <= 10
            ]
            if len(npc_data) < before:
                logger.debug(
                    f"Filtered {before - len(npc_data)} far-away sprites "
                    f"(kept {len(npc_data)})"
                )
            npc_data = npc_data or None

        player_map_pos = None
        if warp_data:
            player_map_pos = (warp_data["player_x"], warp_data["player_y"])
        game_state_info = _detect_game_state(pyboy)
        player_facing = _extract_player_facing(pyboy)

        tile_terrain = None
        try:
            tile_under = pyboy.memory[_ADDR_TILE_PLAYER_ON]
            grass_tile = pyboy.memory[_ADDR_GRASS_TILE]
            if tile_under == grass_tile:
                tile_terrain = "tall grass"
        except Exception:
            pass

        text = _format_spatial_text(
            collision=collision,
            visible=visible,
            tile_to_char=tile_to_char,
            sprites=sprites,
            warp_data=warp_data,
            npc_data=npc_data,
            game_state_info=game_state_info,
            story_progress=story_progress,
            terrain=terrain,
            cut_tree_positions=cut_tree_pos,
            player_facing=player_facing,
            tile_terrain=tile_terrain,
        )

        # Player screen position (metatile coords) for world map accumulator
        player_screen_pos = None
        if sprites:
            player_screen_pos = (
                sprites[0]["tile_x"] // 2,
                (sprites[0]["tile_y"] + 1) // 2,
            )

        # Base terrain grid (no NPC/player overlays) for world map accumulator
        base_grid = None
        if terrain is not None:
            base_grid = [row[:] for row in terrain]
            # Overlay cut trees
            if cut_tree_pos:
                for y in range(len(base_grid)):
                    for x in range(len(base_grid[0]) if base_grid else 0):
                        if base_grid[y][x] == '#' and (x, y) in cut_tree_pos:
                            base_grid[y][x] = 'T'
            # Overlay item/object sprites (not ghosts — collected items become
            # ghosts, so ghost == already picked up; terrain tile takes over)
            if npc_data and player_screen_pos is not None:
                bg_h = len(base_grid)
                bg_w = len(base_grid[0]) if base_grid else 0
                psx, psy = player_screen_pos
                for npc in npc_data:
                    if npc.get("is_ghost"):
                        continue
                    gx = psx + npc["dx"]
                    gy = psy + npc["dy"]
                    if not (0 <= gx < bg_w and 0 <= gy < bg_h):
                        continue
                    if npc["is_item"]:
                        base_grid[gy][gx] = "i"
                    elif npc.get("is_object"):
                        if npc.get("pic_id") == _BOULDER_SPRITE_ID:
                            base_grid[gy][gx] = "B"
                        else:
                            base_grid[gy][gx] = "o"

        map_number = warp_data["map_number"] if warp_data else None

        # Compute absolute map positions for non-ghost, non-item, non-object NPCs
        # so world-map A* can treat them as temporary obstacles.
        npc_abs_positions: List[Tuple[int, int]] = []
        if npc_data and player_map_pos:
            pmx, pmy = player_map_pos
            for npc in npc_data:
                if npc.get("is_ghost") or npc.get("is_item") or npc.get("is_object"):
                    continue
                npc_abs_positions.append((pmx + npc["dx"], pmy + npc["dy"]))

        return {
            "text": text,
            "visible_tilemap": visible,
            "player_pos": player_map_pos,
            "game_state": game_state_info,
            "story_progress": story_progress,
            "player_screen_pos": player_screen_pos,
            "base_grid": base_grid,
            "map_number": map_number,
            "warp_data_raw": warp_data,
            "npc_abs_positions": npc_abs_positions,
        }
    except Exception as e:
        logger.error(f"Error extracting spatial context: {e}", exc_info=True)
        return {"text": "", "visible_tilemap": previous_tilemap, "player_pos": previous_player_pos, "game_state": {}, "story_progress": None}
