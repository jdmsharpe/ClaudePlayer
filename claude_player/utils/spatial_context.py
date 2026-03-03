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

# Sprite facing direction (offset 0x09 within SPRITESTATEDATA1, 16-byte stride)
# Values from pret/pokered: $00=DOWN, $04=UP, $08=LEFT, $0C=RIGHT
_FACING_OFFSET = 0x09
_FACING_MAP: Dict[int, str] = {
    0x00: "DOWN",
    0x04: "UP",
    0x08: "LEFT",
    0x0C: "RIGHT",
}

# Game state detection addresses (from pret/pokered disassembly)
_ADDR_TEXT_BOX_ID    = 0xD125   # wTextBoxID – non-zero = text box active
_ADDR_IS_IN_BATTLE   = 0xD057   # wIsInBattle – 0=overworld, 1=wild, 2=trainer
_ADDR_WALK_COUNTER   = 0xCFC5   # wWalkCounter – non-zero = mid-step animation
_ADDR_JOY_IGNORE     = 0xCC6B   # wJoyIgnore – button ignore bitmask (retained for reference; stale like wTextBoxID)
_ADDR_STATUS_FLAGS5  = 0xD730   # bit5=joypad disabled, bit7=scripted movement
_ADDR_WINDOW_Y       = 0xFF4A   # WY register – Window layer Y position (144 = off-screen)
_ADDR_SIM_JOYPAD_IDX = 0xCD38   # wSimulatedJoypadStatesIndex – non-zero = game running scripted input

# Terrain classification RAM addresses
_ADDR_GRASS_TILE       = 0xD535   # wGrassTile — grass tile value for current map
_ADDR_TILESET_TYPE     = 0xFFD7   # Tileset type: 0=indoor, >0=outdoor (has grass)
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

# Map ID → name (from pret/pokered constants/map_constants.asm)
# Complete mapping verified against https://github.com/pret/pokered/blob/master/constants/map_constants.asm
_MAP_NAMES = {
    # Towns & Cities
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
    # Routes
    0x0C: "Route 1",
    0x0D: "Route 2",
    0x0E: "Route 3",
    0x0F: "Route 4",
    0x10: "Route 5",
    0x11: "Route 6",
    0x12: "Route 7",
    0x13: "Route 8",
    0x14: "Route 9",
    0x15: "Route 10",
    0x16: "Route 11",
    0x17: "Route 12",
    0x18: "Route 13",
    0x19: "Route 14",
    0x1A: "Route 15",
    0x1B: "Route 16",
    0x1C: "Route 17",
    0x1D: "Route 18",
    0x1E: "Route 19",
    0x1F: "Route 20",
    0x20: "Route 21",
    0x21: "Route 22",
    0x22: "Route 23",
    0x23: "Route 24",
    0x24: "Route 25",
    # Pallet Town buildings
    0x25: "Red's House 1F",
    0x26: "Red's House 2F",
    0x27: "Blue's House",
    0x28: "Oak's Lab",
    # Viridian City buildings
    0x29: "Pokemon Center (Viridian)",
    0x2A: "Viridian Mart",
    0x2B: "Viridian School House",
    0x2C: "Viridian Nickname House",
    0x2D: "Viridian Gym",
    # Route 2 / Viridian Forest gates
    0x2E: "Diglett's Cave (Route 2)",
    0x2F: "Viridian Forest North Gate",
    0x30: "Route 2 Trade House",
    0x31: "Route 2 Gate",
    0x32: "Viridian Forest South Gate",
    # Viridian Forest
    0x33: "Viridian Forest",
    # Pewter City buildings
    0x34: "Museum 1F",
    0x35: "Museum 2F",
    0x36: "Pewter Gym",
    0x37: "Pewter Nidoran House",
    0x38: "Pewter Mart",
    0x39: "Pewter Speech House",
    0x3A: "Pokemon Center (Pewter)",
    # Mt. Moon
    0x3B: "Mt. Moon 1F",
    0x3C: "Mt. Moon B1F",
    0x3D: "Mt. Moon B2F",
    # Cerulean City buildings
    0x3E: "Cerulean Trashed House",
    0x3F: "Cerulean Trade House",
    0x40: "Pokemon Center (Cerulean)",
    0x41: "Cerulean Gym",
    0x42: "Bike Shop",
    0x43: "Cerulean Mart",
    # Route 4
    0x44: "Pokemon Center (Mt. Moon)",
    0x45: "Cerulean Trashed House (Copy)",
    # Route 5
    0x46: "Route 5 Gate",
    0x47: "Underground Path (Route 5)",
    0x48: "Daycare",
    # Route 6
    0x49: "Route 6 Gate",
    0x4A: "Underground Path (Route 6)",
    # Route 7
    0x4C: "Route 7 Gate",
    0x4D: "Underground Path (Route 7)",
    # Route 8
    0x4F: "Route 8 Gate",
    0x50: "Underground Path (Route 8)",
    # Rock Tunnel / Power Plant
    0x51: "Pokemon Center (Rock Tunnel)",
    0x52: "Rock Tunnel 1F",
    0x53: "Power Plant",
    # Route 11
    0x54: "Route 11 Gate 1F",
    0x55: "Diglett's Cave (Route 11)",
    0x56: "Route 11 Gate 2F",
    # Route 12
    0x57: "Route 12 Gate 1F",
    # Bill's House
    0x58: "Bill's House",
    # Vermilion City buildings
    0x59: "Pokemon Center (Vermilion)",
    0x5A: "Pokemon Fan Club",
    0x5B: "Vermilion Mart",
    0x5C: "Vermilion Gym",
    0x5D: "Vermilion Pidgey House",
    0x5E: "Vermilion Dock",
    # S.S. Anne
    0x5F: "S.S. Anne 1F",
    0x60: "S.S. Anne 2F",
    0x61: "S.S. Anne 3F",
    0x62: "S.S. Anne B1F",
    0x63: "S.S. Anne Bow",
    0x64: "S.S. Anne Kitchen",
    0x65: "S.S. Anne Captain's Room",
    0x66: "S.S. Anne 1F Rooms",
    0x67: "S.S. Anne 2F Rooms",
    0x68: "S.S. Anne B1F Rooms",
    # Victory Road 1F
    0x6C: "Victory Road 1F",
    # Pokemon League
    0x71: "Lance's Room",
    0x76: "Hall of Fame",
    # Underground Paths
    0x77: "Underground Path (N-S)",
    0x78: "Champion's Room",
    0x79: "Underground Path (W-E)",
    # Celadon City buildings
    0x7A: "Celadon Mart 1F",
    0x7B: "Celadon Mart 2F",
    0x7C: "Celadon Mart 3F",
    0x7D: "Celadon Mart 4F",
    0x7E: "Celadon Mart Roof",
    0x7F: "Celadon Mart Elevator",
    0x80: "Celadon Mansion 1F",
    0x81: "Celadon Mansion 2F",
    0x82: "Celadon Mansion 3F",
    0x83: "Celadon Mansion Roof",
    0x84: "Celadon Mansion Roof House",
    0x85: "Pokemon Center (Celadon)",
    0x86: "Celadon Gym",
    0x87: "Game Corner",
    0x88: "Celadon Mart 5F",
    0x89: "Game Corner Prize Room",
    0x8A: "Celadon Diner",
    0x8B: "Celadon Chief House",
    0x8C: "Celadon Hotel",
    # Lavender Town buildings
    0x8D: "Pokemon Center (Lavender)",
    # Pokemon Tower
    0x8E: "Pokemon Tower 1F",
    0x8F: "Pokemon Tower 2F",
    0x90: "Pokemon Tower 3F",
    0x91: "Pokemon Tower 4F",
    0x92: "Pokemon Tower 5F",
    0x93: "Pokemon Tower 6F",
    0x94: "Pokemon Tower 7F",
    0x95: "Mr. Fuji's House",
    0x96: "Lavender Mart",
    0x97: "Lavender Cubone House",
    # Fuchsia City buildings
    0x98: "Fuchsia Mart",
    0x99: "Fuchsia Bill's Grandpa House",
    0x9A: "Pokemon Center (Fuchsia)",
    0x9B: "Warden's House",
    0x9C: "Safari Zone Gate",
    0x9D: "Fuchsia Gym",
    0x9E: "Fuchsia Meeting Room",
    # Seafoam Islands
    0x9F: "Seafoam Islands B1F",
    0xA0: "Seafoam Islands B2F",
    0xA1: "Seafoam Islands B3F",
    0xA2: "Seafoam Islands B4F",
    # Vermilion / Fuchsia extras
    0xA3: "Vermilion Old Rod House",
    0xA4: "Fuchsia Good Rod House",
    # Pokemon Mansion
    0xA5: "Pokemon Mansion 1F",
    # Cinnabar Island buildings
    0xA6: "Cinnabar Gym",
    0xA7: "Cinnabar Lab",
    0xA8: "Cinnabar Lab Trade Room",
    0xA9: "Cinnabar Lab Metronome Room",
    0xAA: "Cinnabar Lab Fossil Room",
    0xAB: "Pokemon Center (Cinnabar)",
    0xAC: "Cinnabar Mart",
    # Indigo Plateau
    0xAE: "Indigo Plateau Lobby",
    # Saffron City buildings
    0xAF: "Copycat's House 1F",
    0xB0: "Copycat's House 2F",
    0xB1: "Fighting Dojo",
    0xB2: "Saffron Gym",
    0xB3: "Saffron Pidgey House",
    0xB4: "Saffron Mart",
    0xB5: "Silph Co. 1F",
    0xB6: "Pokemon Center (Saffron)",
    0xB7: "Mr. Psychic's House",
    # Route gates
    0xB8: "Route 15 Gate 1F",
    0xB9: "Route 15 Gate 2F",
    0xBA: "Route 16 Gate 1F",
    0xBB: "Route 16 Gate 2F",
    0xBC: "Route 16 Fly House",
    0xBD: "Route 12 Super Rod House",
    0xBE: "Route 18 Gate 1F",
    0xBF: "Route 18 Gate 2F",
    # Seafoam Islands 1F
    0xC0: "Seafoam Islands 1F",
    # Route 22
    0xC1: "Route 22 Gate",
    # Victory Road
    0xC2: "Victory Road 2F",
    0xC3: "Route 12 Gate 2F",
    0xC4: "Vermilion Trade House",
    # Diglett's Cave
    0xC5: "Diglett's Cave",
    # Victory Road 3F
    0xC6: "Victory Road 3F",
    # Rocket Hideout
    0xC7: "Rocket Hideout B1F",
    0xC8: "Rocket Hideout B2F",
    0xC9: "Rocket Hideout B3F",
    0xCA: "Rocket Hideout B4F",
    0xCB: "Rocket Hideout Elevator",
    # Silph Co.
    0xCF: "Silph Co. 2F",
    0xD0: "Silph Co. 3F",
    0xD1: "Silph Co. 4F",
    0xD2: "Silph Co. 5F",
    0xD3: "Silph Co. 6F",
    0xD4: "Silph Co. 7F",
    0xD5: "Silph Co. 8F",
    # Pokemon Mansion upper floors
    0xD6: "Pokemon Mansion 2F",
    0xD7: "Pokemon Mansion 3F",
    0xD8: "Pokemon Mansion B1F",
    # Safari Zone
    0xD9: "Safari Zone East",
    0xDA: "Safari Zone North",
    0xDB: "Safari Zone West",
    0xDC: "Safari Zone Center",
    0xDD: "Safari Zone Center Rest House",
    0xDE: "Safari Zone Secret House",
    0xDF: "Safari Zone West Rest House",
    0xE0: "Safari Zone East Rest House",
    0xE1: "Safari Zone North Rest House",
    # Cerulean Cave
    0xE2: "Cerulean Cave 2F",
    0xE3: "Cerulean Cave B1F",
    0xE4: "Cerulean Cave 1F",
    # Misc
    0xE5: "Name Rater's House",
    0xE6: "Cerulean Badge House",
    # Rock Tunnel B1F
    0xE8: "Rock Tunnel B1F",
    # Silph Co. upper floors
    0xE9: "Silph Co. 9F",
    0xEA: "Silph Co. 10F",
    0xEB: "Silph Co. 11F",
    0xEC: "Silph Co. Elevator",
    # Trade / Battle
    0xEF: "Trade Center",
    0xF0: "Colosseum",
    # Pokemon League rooms
    0xF5: "Lorelei's Room",
    0xF6: "Bruno's Room",
    0xF7: "Agatha's Room",
    # Special
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

    Uses the same bottom-left-of-metatile sampling as PyBoy's collision
    system for exact alignment with the walkability grid.
    """
    try:
        # Read the VRAM background tilemap
        (scx_px, scy_px), _ = pyboy.screen.get_tilemap_position()
        scx = scx_px // 8
        scy = scy_px // 8
        bg = pyboy.tilemap_background[:, :]

        # Sample bottom-left tile of each 2x2 metatile (same as PyBoy collision)
        # Grid is 10 wide x 9 tall (metatile resolution)
        grid_w = SCREEN_TILES_X // 2   # 10
        grid_h = SCREEN_TILES_Y // 2   # 9

        metatile_ids = []
        for my in range(grid_h):
            row = []
            for mx in range(grid_w):
                # Bottom-left = odd row (y*2+1), even col (x*2)
                ty = (my * 2 + 1 + scy) % TILEMAP_SIZE
                tx = (mx * 2 + scx) % TILEMAP_SIZE
                row.append(bg[ty][tx])
            metatile_ids.append(row)

        # Read terrain-classification RAM values
        tileset_type = pyboy.memory[_ADDR_TILESET_TYPE]
        grass_tile_vram = None
        if tileset_type > 0:  # Outdoor tileset has grass
            grass_raw = pyboy.memory[_ADDR_GRASS_TILE]
            if grass_raw != 0xFF:
                grass_tile_vram = grass_raw + 0x100

        # Build walkable tile set from collision pointer table
        coll_ptr_lo = pyboy.memory[_ADDR_TILESET_COLL_PTR]
        coll_ptr_hi = pyboy.memory[_ADDR_TILESET_COLL_PTR + 1]
        coll_ptr = coll_ptr_lo | (coll_ptr_hi << 8)
        walkable_set: set = set()
        if grass_tile_vram is not None:
            walkable_set.add(grass_tile_vram)
        # Read collision pairs (each pair = 2 bytes, terminated by 0xFF)
        for i in range(0, 0x180, 2):
            tile_val = pyboy.memory[coll_ptr + i]
            if tile_val == 0xFF:
                break
            walkable_set.add(tile_val + 0x100)
            # Second byte of pair is also walkable
            tile_val2 = pyboy.memory[coll_ptr + i + 1]
            if tile_val2 != 0xFF:
                walkable_set.add(tile_val2 + 0x100)

        # Classify each metatile
        terrain: List[List[str]] = []
        for my in range(grid_h):
            row: List[str] = []
            for mx in range(grid_w):
                tid = metatile_ids[my][mx]
                if grass_tile_vram is not None and tid == grass_tile_vram:
                    row.append(',')
                elif tileset_type > 0 and tid in _LEDGE_TILES:
                    row.append(_LEDGE_TILES[tid])
                elif tileset_type > 0 and tid == _WATER_TILE_VRAM:
                    row.append('=')
                elif tid in walkable_set:
                    row.append('.')
                else:
                    row.append('#')
            terrain.append(row)

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
                    # Each block covers a 2x2 metatile area in the grid
                    base_gx = bx * 2 - x_off
                    base_gy = by * 2 - y_off
                    for dy in range(2):
                        for dx in range(2):
                            positions.add((base_gx + dx, base_gy + dy))

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
            name = _SPRITE_NAMES.get(pic_id, "NPC")

            logger.info(
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
                            # ON THIS TILE — need to trigger
                            hint = "  [on this tile — walk D16 to trigger exit]"
                    else:
                        hint = "  [no path found]"
                else:
                    # Warp is off the visible grid — use A* to the nearest edge
                    # in the warp's direction, avoiding walls.
                    edge_dir = None
                    if abs(dy) >= abs(dx):
                        edge_dir = "NORTH" if dy < 0 else "SOUTH"
                    else:
                        edge_dir = "WEST" if dx < 0 else "EAST"
                    edge_path = find_path_to_edge(grid, player_pos, edge_dir) if edge_dir else None
                    if edge_path:
                        buttons = path_to_buttons(edge_path)
                        hint = f"  [path: {buttons} (warp is off screen, continue {edge_dir})]" if buttons else f"  [off screen — head {edge_dir}]"
                    else:
                        hint = _edge_opening_hint(edge_dir) if edge_dir else ""
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
                    hint = "  [on this tile — walk D16 to trigger exit]"

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
    player_movement_text: Optional[str],
    npc_data: Optional[List[Dict[str, Any]]] = None,
    game_state_info: Optional[Dict[str, str]] = None,
    story_progress: Optional[Dict[str, Any]] = None,
    terrain: Optional[List[List[str]]] = None,
    cut_tree_positions: Optional[set] = None,
    player_facing: Optional[str] = None,
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
    if terrain is not None and has_collision:
        # Use collision as walkability ground truth, overlay terrain types.
        # The terrain VRAM sampling can misclassify walkability (e.g. fence
        # tiles whose IDs appear in the collision pointer's walkable list),
        # but terrain accurately identifies grass/water/ledge tile types.
        grid_height = len(terrain)
        grid_width = len(terrain[0]) if terrain else 0
        for y in range(grid_height):
            row = []
            for x in range(grid_width):
                terrain_type = terrain[y][x]
                cy, cx = y * 2, x * 2
                if cy + 1 < src_height and cx + 1 < src_width:
                    coll_walkable = all(
                        collision[cy + dy][cx + dx] != 0
                        for dy in range(2) for dx in range(2)
                    )
                else:
                    coll_walkable = terrain_type not in ('#',)
                # Preserve special terrain markers (grass, water, ledge);
                # for plain walkable/blocked, defer to collision data.
                if terrain_type in (',', '=', 'v', '>', '<'):
                    row.append(terrain_type)
                elif coll_walkable:
                    row.append('.')
                else:
                    row.append('#')
            grid.append(row)
    elif terrain is not None:
        # No collision data — use terrain classification as-is
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

    # Context-aware hint for current milestone + map
    if story_progress and story_progress.get("next") and warp_data:
        from claude_player.utils.event_flags import get_map_hint
        hint = get_map_hint(story_progress["next"][0], warp_data["map_number"])
        if hint:
            lines.append(f"HINT: {hint}")

    # Player grid position (viewport-relative, matches the grid below)
    if player_screen_pos:
        facing_str = f", facing {player_facing}" if player_facing else ""
        lines.append(f"Player @ is at grid column {player_screen_pos[0]}, row {player_screen_pos[1]}{facing_str}")

    # Absolute map position + compass for off-screen exits
    if warp_data:
        px_abs = warp_data["player_x"]
        py_abs = warp_data["player_y"]
        mw = warp_data["map_width"]
        mh = warp_data["map_height"]
        lines.append(f"Map position: ({px_abs}, {py_abs}) of {mw}x{mh}")

        # Compass bearings to warps/connections that are far off-screen
        viewport_half_w = grid_width // 2  # ~5
        viewport_half_h = grid_height // 2  # ~4
        compass_lines: list = []
        for w in warp_data.get("warps", []):
            if abs(w["dy"]) > viewport_half_h or abs(w["dx"]) > viewport_half_w:
                parts = []
                if w["dy"] < 0:
                    parts.append(f"~{abs(w['dy'])} blocks UP")
                elif w["dy"] > 0:
                    parts.append(f"~{w['dy']} blocks DOWN")
                if w["dx"] < 0:
                    parts.append(f"~{abs(w['dx'])} blocks LEFT")
                elif w["dx"] > 0:
                    parts.append(f"~{w['dx']} blocks RIGHT")
                if parts:
                    compass_lines.append(f"  {w['dest_name']}: {', '.join(parts)}")
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
                compass_lines.append(f"  {conn['dest_name']}: ~{dist} blocks {d}")
        if compass_lines:
            lines.append("COMPASS (off-screen exits):")
            lines.extend(compass_lines)

    # Player movement status (directional, no raw map coords)
    if player_movement_text:
        lines.append(player_movement_text)

    # Grid with column index header
    lines.append("   " + "".join(str(x % 10) for x in range(grid_width)))
    for y in range(grid_height):
        lines.append(f"{y:2d} " + "".join(grid[y]))

    # Brief legend
    if has_collision or terrain is not None:
        lines.append(". = walkable  # = blocked  , = grass  = = water  v/>/< = ledge  T = cut tree  B = boulder  W = exit  @ = player  1-9 = NPC  i = item  o = object  (1 cell = 16 frames)")

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
        if 0 <= gx < grid_w and 0 <= gy < grid_h:
            # Only overlay W on walkable tiles — some warps sit on wall tiles
            # (e.g. gate buildings with wider warp zones than walkable exits)
            if grid[gy][gx] not in ('#', 'T', 'B', '='):
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
        terrain = _extract_terrain_data(pyboy)
        cut_tree_pos = _extract_cut_tree_positions(pyboy)
        warp_data = _extract_warp_data(pyboy)
        npc_data = _extract_npc_data(pyboy)
        from claude_player.utils.event_flags import check_story_progress
        try:
            story_progress = check_story_progress(pyboy.memory.__getitem__)
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
        player_movement_text = _detect_player_movement(player_map_pos, previous_player_pos, game_state_info)
        player_facing = _extract_player_facing(pyboy)

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
            terrain=terrain,
            cut_tree_positions=cut_tree_pos,
            player_facing=player_facing,
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
