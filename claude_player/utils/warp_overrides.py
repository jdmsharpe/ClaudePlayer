# Per-map warp position overrides.
#
# Warp indices and map layouts derived from the pret/pokered disassembly:
#   https://github.com/pret/pokered
#
# Some warp trigger tiles in RAM are placed at awkward positions (e.g. inside a
# wall, one tile off from the visible door, or on a transition row that the agent
# never naturally walks onto).  This dict lets you relocate any warp to a more
# navigable tile without touching the ROM or RAM.
#
# Key:   (cur_map_number, warp_index)
#          cur_map_number — wCurMap value (0xD35E) while on that map
#          warp_index     — 0-based position in the warp list shown under
#                           "Doors/Warps" in SPATIAL CONTEXT each turn
# Value: (new_y, new_x)  — absolute map coordinates (same space as player_y/player_x)
#
# Convention: shift exit warps ONE TILE OUTSIDE the wall so the agent walks
# through the actual trigger tile rather than stopping just short of it.
#   x=0  wall → x=-1    x=max wall → x=max+1
#   y=0  wall → y=-1    y=max wall → y=max+1
#
# Example (commented out):
#   (0x00, 0): (5, 7),   # Pallet Town warp 0 (Oak's Lab) — move trigger to y=5, x=7

from typing import Dict, Tuple

# Per-warp destination name overrides.
#
# When multiple warps on the same map share the same dest_map (e.g. all four
# Mt. Moon B2F warps go to Mt. Moon B1F), the NAV pipeline can't distinguish
# them.  This dict gives individual warps unique dest_names so that MAP_HINTS
# and goal text can target a specific warp.
#
# Key:   (cur_map_number, warp_index) — same as WARP_POSITION_OVERRIDES
# Value: override dest_name string
WARP_DEST_NAME_OVERRIDES: Dict[Tuple[int, int], str] = {
    # --- Mt. Moon B2F (0x3D) ---
    # pokered: W0→B1F W1, W1→B1F W4, W2→B1F W5, W3→B1F W6
    # All 4 warps share dest_map = 0x3C (Mt. Moon B1F).
    # B2F north zone: W0(9,25), W1(17,21), W3(7,5)
    # B2F south zone: W2(27,15) — entry from B1F W5
    (0x3D, 0): "Mt. Moon B1F (north stairs)",       # W0 north zone → B1F W1
    (0x3D, 1): "Mt. Moon B1F (west section)",         # W1 north zone → B1F W4 (dead-end pocket)
    (0x3D, 2): "Mt. Moon B1F (south entry)",         # W2 south zone → B1F W5 (loops back)
    (0x3D, 3): "Mt. Moon B1F (upper exit to Route 4)", # W3 north zone → B1F W6 near W7/Route 4

    # --- Mt. Moon B1F (0x3C) ---
    # pokered: W1→B2F W0, W4→B2F W1, W5→B2F W2, W6→B2F W3
    # B1F W1(17,11) → B2F north zone (W0 at 9,25)
    # B1F W4(21,17) → B2F north zone (W1 at 17,21)
    # B1F W5(13,27) → B2F SOUTH zone (W2 at 27,15 — loops back!)
    # B1F W6(23,3)  → B2F north zone (W3 at 7,5)
    (0x3C, 1): "Mt. Moon B2F (north zone)",        # W1 → B2F W0 (north zone!)
    (0x3C, 4): "Mt. Moon B2F (north zone)",        # W4 → B2F W1 (north zone)
    (0x3C, 5): "Mt. Moon B2F (south dead-end)",    # W5 → B2F W2 (SOUTH zone loop!)
    (0x3C, 6): "Mt. Moon B2F (north zone)",        # W6 → B2F W3 (north zone)
    (0x3C, 7): "Route 4 (exit)",                   # W7 → Route 4 (the goal)
}

WARP_POSITION_OVERRIDES: Dict[Tuple[int, int], Tuple[int, int]] = {
    # (map_number, warp_index): (override_y, override_x)

    # -------------------------------------------------------------------------
    # PALLET TOWN BUILDINGS
    # -------------------------------------------------------------------------
    # Red's House 1F (0x25) — exit at x=7
    (0x25, 0): (8, 2), (0x25, 1): (8, 3),
    # Red's House 2F (0x26) — staircase only, no outdoor exit
    # Blue's House (0x27) — exit at x=7
    (0x27, 0): (8, 2), (0x27, 1): (8, 3),
    # Oak's Lab (0x28) — exit at x=11
    (0x28, 0): (12, 4), (0x28, 1): (12, 5),

    # -------------------------------------------------------------------------
    # VIRIDIAN CITY BUILDINGS
    # -------------------------------------------------------------------------
    # Pokemon Center Viridian (0x29) — exit at x=7
    (0x29, 0): (8, 3), (0x29, 1): (8, 4),
    # Viridian Mart (0x2A) — exit at x=7
    (0x2A, 0): (8, 4), (0x2A, 1): (8, 4),
    # Viridian School House (0x2B) — exit at x=7
    (0x2B, 0): (8, 2), (0x2B, 1): (8, 3),
    # Viridian Nickname House (0x2C) — exit at x=7
    (0x2C, 0): (8, 2), (0x2C, 1): (8, 3),
    # Viridian Gym (0x2D) — exit at x=17
    (0x2D, 0): (18, 16), (0x2D, 1): (18, 17),

    # -------------------------------------------------------------------------
    # ROUTE 2 / VIRIDIAN FOREST AREA
    # -------------------------------------------------------------------------
    # Diglett's Cave Route 2 entrance (0x2E) — exit at x=7, cave warp interior
    (0x2E, 0): (8, 2), (0x2E, 1): (8, 3),
    # Viridian Forest North Gate (0x2F) — x=0 wall and x=7 wall
    (0x2F, 0): (-1, 5), (0x2F, 1): (-1, 5),   # was (0,4) (0,5) → Route 2 north
    (0x2F, 2): (8, 4), (0x2F, 3): (8, 5),   # was (7,4) (7,5) → Viridian Forest
    # Route 2 Trade House (0x30) — exit at x=7
    (0x30, 0): (8, 2), (0x30, 1): (8, 3),
    # Route 2 (0x31) — x=0 wall and x=7 wall (inward shift kept from manual edit)
    (0x31, 0): (-2, 4), (0x31, 1): (-2, 5),     # was (0,4) (0,5) → Route 2 north
    (0x31, 2): (5, 4), (0x31, 3): (5, 5),     # was (7,4) (7,5) → Route 2 south
    # Viridian Forest South Gate (0x32) — x=0 and x=7 walls
    (0x32, 0): (-1, 5), (0x32, 1): (-1, 5),   # was (0,4) (0,5) → Viridian Forest
    (0x32, 2): (8, 4), (0x32, 3): (8, 5),   # was (7,4) (7,5) → Route 2 south
    # Viridian Forest (0x33) — west edge (x=0) and east edge (x=47)
    (0x33, 0): (-1, 1), (0x33, 1): (-1, 2), # was (0,1)  (0,2)  → North Gate
    (0x33, 2): (15, 48), (0x33, 3): (16, 48), # was (15,47)(16,47)→ South Gate
    (0x33, 4): (17, 48), (0x33, 5): (18, 48), # was (17,47)(18,47)→ South Gate

    # -------------------------------------------------------------------------
    # PEWTER CITY BUILDINGS
    # -------------------------------------------------------------------------
    # Museum 1F (0x34) — two exits at x=7
    (0x34, 0): (8, 10), (0x34, 1): (8, 10),
    (0x34, 2): (8, 16), (0x34, 3): (8, 17),
    # Museum 2F (0x35) — staircase only

    # Pewter Gym (0x36) — exit at x=14
    (0x36, 0): (14, 4), (0x36, 1): (14, 5),
    # Pewter Nidoran House (0x37) — exit at x=7
    (0x37, 0): (8, 2), (0x37, 1): (8, 3),
    # Pewter Mart (0x38) — user-adjusted values preserved
    (0x38, 0): (8, 3), (0x38, 1): (8, 4),
    # Pewter Speech House (0x39) — exit at x=7
    (0x39, 0): (8, 2), (0x39, 1): (8, 3),
    # Pokemon Center Pewter (0x3A) — user-adjusted values preserved
    (0x3A, 0): (8, 3), (0x3A, 1): (8, 4),

    # -------------------------------------------------------------------------
    # MT. MOON
    # -------------------------------------------------------------------------
    # Mt. Moon 1F (0x3B) — east exit at x=35
    (0x3B, 0): (36, 14), (0x3B, 1): (36, 15),  # was (14,35)(15,35) → Route 4
    # Mt. Moon Pokecenter (0x44) — exit at x=7
    (0x44, 0): (8, 3), (0x44, 1): (8, 4),

    # -------------------------------------------------------------------------
    # CERULEAN CITY BUILDINGS
    # -------------------------------------------------------------------------
    # Cerulean Trashed House (0x3E) — exit at x=7, side exit at x=0
    (0x3E, 0): (8, 2), (0x3E, 1): (8, 3),
    (0x3E, 2): (8, 3),                        # was (3,0) → LAST_MAP left wall
    # Cerulean Trade House (0x3F) — exit at x=7
    (0x3F, 0): (8, 2), (0x3F, 1): (8, 3),
    # Pokemon Center Cerulean (0x40) — exit at x=7
    (0x40, 0): (8, 3), (0x40, 1): (8, 4),
    # Cerulean Gym (0x41) — exit at x=13
    (0x41, 0): (14, 4), (0x41, 1): (14, 5),
    # Bike Shop (0x42) — exit at x=7
    (0x42, 0): (8, 2), (0x42, 1): (8, 3),
    # Cerulean Mart (0x43) — exit at x=7
    (0x43, 0): (8, 3), (0x43, 1): (8, 4),
    # Cerulean Badge House (0x E6) — exit at x=7, side at x=0
    (0xE6, 0): (8, 2),                        # was (2,0) → left wall
    (0xE6, 1): (8, 3), (0xE6, 2): (8, 4),

    # -------------------------------------------------------------------------
    # ROUTE 5/6/7/8 GATES & UNDERGROUND PATH ENTRANCES
    # -------------------------------------------------------------------------
    # Route 5 Gate (0x46)
    (0x46, 0): (3, -1), (0x46, 1): (4, -1),   # was (3,0) (4,0) → Route 5 north
    (0x46, 2): (3,  6), (0x46, 3): (4,  6),   # was (3,5) (4,5) → Route 5 south
    # Underground Path Route 5 entrance (0x47)
    (0x47, 0): (3, 8), (0x47, 1): (4, 8),
    # Daycare (0x48) — exit at x=7
    (0x48, 0): (2, 8), (0x48, 1): (3, 8),
    # Route 6 Gate (0x49)
    (0x49, 0): (3, -1), (0x49, 1): (4, -1),
    (0x49, 2): (3,  6), (0x49, 3): (4,  6),
    # Underground Path Route 6 entrance (0x4A)
    (0x4A, 0): (3, 8), (0x4A, 1): (4, 8),
    # Route 7 Gate (0x4C) — horizontal gate (y-wall exits)
    (0x4C, 0): (6, 3), (0x4C, 1): (6, 4),     # was (5,3) (5,4) → Route 7 east
    (0x4C, 2): (-1, 3), (0x4C, 3): (-1, 4),   # was (0,3) (0,4) → Route 7 west
    # Underground Path Route 7 entrance (0x4D)
    (0x4D, 0): (3, 8), (0x4D, 1): (4, 8),
    # Route 8 Gate (0x4F) — horizontal gate
    (0x4F, 0): (-1, 3), (0x4F, 1): (-1, 4),   # was (0,3) (0,4)
    (0x4F, 2): (6, 3),  (0x4F, 3): (6, 4),    # was (5,3) (5,4)
    # Underground Path Route 8 entrance (0x50)
    (0x50, 0): (3, 8), (0x50, 1): (4, 8),

    # -------------------------------------------------------------------------
    # ROCK TUNNEL AREA
    # -------------------------------------------------------------------------
    # Rock Tunnel Pokecenter (0x51) — exit at x=7
    (0x51, 0): (3, 8), (0x51, 1): (4, 8),
    # Rock Tunnel 1F (0x52) — west exit at x=0, east exit at x=33/35
    (0x52, 0): (15, -1),                       # was (15,0) → Route 10 west exit
    (0x52, 2): (15, 36),                       # was (15,35)→ Route 10 east exit
    (0x52, 3): (15, 36),                       # was (15,35)

    # -------------------------------------------------------------------------
    # ROUTE 11 GATES
    # -------------------------------------------------------------------------
    # Route 11 Gate 1F (0x54) — y=0 and y=7 walls (horizontal gate)
    (0x54, 0): (-1, 4), (0x54, 1): (-1, 5),   # was (0,4) (0,5) → Route 11 west
    (0x54, 2): ( 8, 4), (0x54, 3): ( 8, 5),   # was (7,4) (7,5) → Route 11 east
    # Route 11 Gate 2F (0x56) — staircase only
    # Diglett's Cave Route 11 entrance (0x55)
    (0x55, 0): (2, 8), (0x55, 1): (3, 8),

    # -------------------------------------------------------------------------
    # ROUTE 12 GATES
    # -------------------------------------------------------------------------
    # Route 12 Gate 1F (0x57) — x=0 and x=7 walls
    (0x57, 0): (4, -1), (0x57, 1): (5, -1),
    (0x57, 2): (4,  8), (0x57, 3): (5,  8),
    # Route 12 Gate 2F (0xC3) — staircase only
    # Route 12 Super Rod House (0xBD)
    (0xBD, 0): (2, 8), (0xBD, 1): (3, 8),

    # -------------------------------------------------------------------------
    # BILL'S HOUSE / VERMILION CITY
    # -------------------------------------------------------------------------
    # Bill's House (0x58) — exit at x=7
    (0x58, 0): (2, 8), (0x58, 1): (3, 8),
    # Pokemon Center Vermilion (0x59)
    (0x59, 0): (3, 8), (0x59, 1): (4, 8),
    # Pokemon Fan Club (0x5A)
    (0x5A, 0): (2, 8), (0x5A, 1): (3, 8),
    # Vermilion Mart (0x5B)
    (0x5B, 0): (3, 8), (0x5B, 1): (4, 8),
    # Vermilion Gym (0x5C) — exit at x=17
    (0x5C, 0): (4, 18), (0x5C, 1): (5, 18),
    # Vermilion Pidgey House (0x5D)
    (0x5D, 0): (2, 8), (0x5D, 1): (3, 8),
    # Vermilion Old Rod House (0xA3)
    (0xA3, 0): (2, 8), (0xA3, 1): (3, 8),
    # Vermilion Trade House (0xC4)
    (0xC4, 0): (2, 8), (0xC4, 1): (3, 8),

    # -------------------------------------------------------------------------
    # S.S. ANNE
    # -------------------------------------------------------------------------
    # Vermilion Dock (0x5E) — north exit at y=0 of tunnel
    (0x5E, 0): (-1, 14),                       # was (0,14) → Route 6? (top wall)
    # SS Anne 1F (0x5F) — exits at x=0 (gangplank)
    (0x5F, 0): (26, -1), (0x5F, 1): (27, -1), # was (26,0)(27,0) → Dock
    # SS Anne 3F (0x61) — top wall exit to bow
    (0x61, 0): (-1, 3),                        # was (0,3) → SS Anne Bow
    # SS Anne Kitchen (0x64) — left wall exit
    (0x64, 0): (6, -1),                        # was (6,0) → SS Anne 1F
    # SS Anne Captain's Room (0x65) — top wall exit
    (0x65, 0): (-1, 7),                        # was (0,7) → SS Anne 2F
    # SS Anne 1F Rooms (0x66) — exits at y=0 (top of each room)
    (0x66, 0): (-1, 0), (0x66, 1): (-1, 10), (0x66, 2): (-1, 20),
    # SS Anne 2F Rooms (0x67) — exits at x=5 (right wall) and x=15
    (0x67, 0): (2, 6),  (0x67, 1): (3, 6),
    (0x67, 2): (12, 6), (0x67, 3): (13, 6),
    (0x67, 4): (22, 6), (0x67, 5): (23, 6),
    (0x67, 6): (2,  16),(0x67, 7): (3,  16),
    (0x67, 8): (12, 16),(0x67, 9): (13, 16),
    (0x67,10): (22, 16),(0x67,11): (23, 16),
    # SS Anne B1F Rooms (0x68) — exits at x=5 and x=15
    (0x68, 0): (2, 6),  (0x68, 1): (3, 6),
    (0x68, 2): (12, 6), (0x68, 3): (13, 6),
    (0x68, 4): (22, 6), (0x68, 5): (23, 6),
    (0x68, 6): (2,  16),(0x68, 7): (3,  16),
    (0x68, 8): (12, 16),(0x68, 9): (13, 16),

    # -------------------------------------------------------------------------
    # VICTORY ROAD
    # -------------------------------------------------------------------------
    # Victory Road 1F (0x6C) — exit at x=17
    (0x6C, 0): (8, 18), (0x6C, 1): (9, 18),
    # Victory Road 2F (0xC2) — top wall exit, right wall exit
    (0xC2, 0): (-1, 8),                        # was (0,8) → Victory Road 1F
    (0xC2, 1): (29, 8), (0xC2, 2): (29, 9),   # was (29,7)(29,8) → Route 23

    # -------------------------------------------------------------------------
    # POKEMON LEAGUE
    # -------------------------------------------------------------------------
    # Indigo Plateau Lobby (0xAE) — exit at x=11, left wall to Lorelei
    (0xAE, 0): (7, 12), (0xAE, 1): (8, 12),   # was (7,11)(8,11) → LAST_MAP
    (0xAE, 2): (8, -1),                        # was (8,0)  → Lorelei's Room
    # Lorelei's Room (0xF5) — exit at x=11, left wall to Bruno
    (0xF5, 0): (4, 12), (0xF5, 1): (5, 12),
    (0xF5, 2): (4, -1), (0xF5, 3): (5, -1),
    # Bruno's Room (0xF6) — exit at x=11, left wall to Agatha
    (0xF6, 0): (4, 12), (0xF6, 1): (5, 12),
    (0xF6, 2): (4, -1), (0xF6, 3): (5, -1),
    # Agatha's Room (0xF7) — exit at x=11, left wall to Lance
    (0xF7, 0): (4, 12), (0xF7, 1): (5, 12),
    (0xF7, 2): (4, -1), (0xF7, 3): (5, -1),
    # Lance's Room (0x71) — left wall to Champion's Room
    (0x71, 0): (5, -1), (0x71, 1): (6, -1),   # was (5,0)(6,0) → Champion's Room
    # Champion's Room (0x78) — exit at x=7, left wall to Hall of Fame
    (0x78, 0): (3, 8),  (0x78, 1): (4, 8),
    (0x78, 2): (3, -1), (0x78, 3): (4, -1),   # was (3,0)(4,0) → Hall of Fame
    # Hall of Fame (0x76) — exit at x=7
    (0x76, 0): (4, 8), (0x76, 1): (5, 8),

    # -------------------------------------------------------------------------
    # CELADON CITY BUILDINGS
    # -------------------------------------------------------------------------
    # Celadon Mart 1F (0x7A) — two exits at x=7
    (0x7A, 0): (2,  8), (0x7A, 1): (3,  8),
    (0x7A, 2): (16, 8), (0x7A, 3): (17, 8),
    # Celadon Mart Elevator (0x7F) — exit at x=3 (inner)
    # (interior destination, no override needed)
    # Celadon Mansion 1F (0x80) — right exit at x=11, left exit at x=0
    (0x80, 0): (4, 12), (0x80, 1): (5, 12),   # was (4,11)(5,11)→ LAST_MAP right
    (0x80, 2): (4, -1),                        # was (4,0) → LAST_MAP left
    # Celadon Mansion 2F-3F: staircase at x=1, no override needed
    # Celadon Mansion Roof (0x83) — staircase only, no outdoor exit
    # Celadon Mansion Roof House (0x84) — exit at x=7
    (0x84, 0): (2, 8), (0x84, 1): (3, 8),
    # Pokemon Center Celadon (0x85)
    (0x85, 0): (3, 8), (0x85, 1): (4, 8),
    # Celadon Gym (0x86) — exit at x=17
    (0x86, 0): (4, 18), (0x86, 1): (5, 18),
    # Game Corner (0x87) — exit at x=17
    (0x87, 0): (15, 18), (0x87, 1): (16, 18),
    # Game Corner Prize Room (0x89)
    (0x89, 0): (4, 8), (0x89, 1): (5, 8),
    # Celadon Diner (0x8A)
    (0x8A, 0): (3, 8), (0x8A, 1): (4, 8),
    # Celadon Chief House (0x8B)
    (0x8B, 0): (2, 8), (0x8B, 1): (3, 8),
    # Celadon Hotel (0x8C)
    (0x8C, 0): (3, 8), (0x8C, 1): (4, 8),

    # -------------------------------------------------------------------------
    # LAVENDER TOWN BUILDINGS
    # -------------------------------------------------------------------------
    # Pokemon Center Lavender (0x8D)
    (0x8D, 0): (3, 8), (0x8D, 1): (4, 8),
    # Pokemon Tower 1F (0x8E) — exit at x=17
    (0x8E, 0): (10, 18), (0x8E, 1): (11, 18),
    # Mr. Fuji's House (0x95)
    (0x95, 0): (2, 8), (0x95, 1): (3, 8),
    # Lavender Mart (0x96)
    (0x96, 0): (3, 8), (0x96, 1): (4, 8),
    # Lavender Cubone House (0x97)
    (0x97, 0): (2, 8), (0x97, 1): (3, 8),

    # -------------------------------------------------------------------------
    # FUCHSIA CITY BUILDINGS
    # -------------------------------------------------------------------------
    # Fuchsia Mart (0x98)
    (0x98, 0): (3, 8), (0x98, 1): (4, 8),
    # Fuchsia Bill's Grandpa House (0x99)
    (0x99, 0): (2, 8), (0x99, 1): (3, 8),
    # Pokemon Center Fuchsia (0x9A)
    (0x9A, 0): (3, 8), (0x9A, 1): (4, 8),
    # Warden's House (0x9B)
    (0x9B, 0): (4, 8), (0x9B, 1): (5, 8),
    # Safari Zone Gate (0x9C) — right wall exit and left wall to Safari Zone
    (0x9C, 0): (3, 6), (0x9C, 1): (4, 6),     # was (3,5)(4,5) → LAST_MAP
    (0x9C, 2): (3, -1), (0x9C, 3): (4, -1),   # was (3,0)(4,0) → Safari Zone
    # Fuchsia Gym (0x9D) — exit at x=17
    (0x9D, 0): (4, 18), (0x9D, 1): (5, 18),
    # Fuchsia Meeting Room (0x9E)
    (0x9E, 0): (4, 8), (0x9E, 1): (5, 8),
    # Fuchsia Good Rod House (0xA4) — exit at x=7, side at x=0
    (0xA4, 0): (2, -1),                        # was (2,0) → left wall
    (0xA4, 1): (2, 8), (0xA4, 2): (3, 8),

    # -------------------------------------------------------------------------
    # SAFARI ZONE REST HOUSES
    # -------------------------------------------------------------------------
    (0xDD, 0): (2, 8), (0xDD, 1): (3, 8),     # Safari Zone Center Rest House
    (0xDE, 0): (2, 8), (0xDE, 1): (3, 8),     # Safari Zone Secret House
    (0xDF, 0): (2, 8), (0xDF, 1): (3, 8),     # Safari Zone West Rest House
    (0xE0, 0): (2, 8), (0xE0, 1): (3, 8),     # Safari Zone East Rest House
    (0xE1, 0): (2, 8), (0xE1, 1): (3, 8),     # Safari Zone North Rest House

    # -------------------------------------------------------------------------
    # SEAFOAM ISLANDS
    # -------------------------------------------------------------------------
    # Seafoam Islands 1F (0xC0) — two exits at x=17
    (0xC0, 0): (4, 18), (0xC0, 1): (5, 18),
    (0xC0, 2): (26, 18),(0xC0, 3): (27, 18),

    # -------------------------------------------------------------------------
    # POKEMON MANSION (CINNABAR)
    # -------------------------------------------------------------------------
    # Pokemon Mansion 1F (0xA5) — exits at x=27
    (0xA5, 0): (4, 28), (0xA5, 1): (5, 28),
    (0xA5, 2): (6, 28), (0xA5, 3): (7, 28),
    (0xA5, 4): (26, 28),(0xA5, 5): (27, 28),

    # -------------------------------------------------------------------------
    # CINNABAR ISLAND BUILDINGS
    # -------------------------------------------------------------------------
    # Cinnabar Gym (0xA6) — exit at x=17
    (0xA6, 0): (16, 18), (0xA6, 1): (17, 18),
    # Cinnabar Lab (0xA7) — exit at x=7
    (0xA7, 0): (2, 8), (0xA7, 1): (3, 8),
    # Cinnabar Lab Trade Room (0xA8)
    (0xA8, 0): (2, 8), (0xA8, 1): (3, 8),
    # Cinnabar Lab Metronome Room (0xA9)
    (0xA9, 0): (2, 8), (0xA9, 1): (3, 8),
    # Cinnabar Lab Fossil Room (0xAA)
    (0xAA, 0): (2, 8), (0xAA, 1): (3, 8),
    # Pokemon Center Cinnabar (0xAB)
    (0xAB, 0): (3, 8), (0xAB, 1): (4, 8),
    # Cinnabar Mart (0xAC)
    (0xAC, 0): (3, 8), (0xAC, 1): (4, 8),

    # -------------------------------------------------------------------------
    # SAFFRON CITY BUILDINGS
    # -------------------------------------------------------------------------
    # Copycat's House 1F (0xAF)
    (0xAF, 0): (2, 8), (0xAF, 1): (3, 8),
    # Fighting Dojo (0xB1) — exit at x=11
    (0xB1, 0): (4, 12), (0xB1, 1): (5, 12),
    # Saffron Gym (0xB2) — exit at x=17
    (0xB2, 0): (8, 18), (0xB2, 1): (9, 18),
    # Saffron Pidgey House (0xB3)
    (0xB3, 0): (2, 8), (0xB3, 1): (3, 8),
    # Saffron Mart (0xB4)
    (0xB4, 0): (3, 8), (0xB4, 1): (4, 8),
    # Pokemon Center Saffron (0xB6)
    (0xB6, 0): (3, 8), (0xB6, 1): (4, 8),
    # Mr. Psychic's House (0xB7)
    (0xB7, 0): (2, 8), (0xB7, 1): (3, 8),

    # -------------------------------------------------------------------------
    # ROUTE 15/16/18 GATES
    # -------------------------------------------------------------------------
    # Route 15 Gate 1F (0xB8) — horizontal gate, y=0 and y=7 walls
    (0xB8, 0): (-1, 4), (0xB8, 1): (-1, 5),
    (0xB8, 2): ( 8, 4), (0xB8, 3): ( 8, 5),
    # Route 16 Gate 1F (0xBA) — two passage levels
    (0xBA, 0): (-1, 8), (0xBA, 1): (-1, 9),
    (0xBA, 2): ( 8, 8), (0xBA, 3): ( 8, 9),
    (0xBA, 4): (-1, 2), (0xBA, 5): (-1, 3),
    (0xBA, 6): ( 8, 2), (0xBA, 7): ( 8, 3),
    # Route 16 Fly House (0xBC)
    (0xBC, 0): (2, 8), (0xBC, 1): (3, 8),
    # Route 18 Gate 1F (0xBE) — horizontal gate
    (0xBE, 0): (-1, 4), (0xBE, 1): (-1, 5),
    (0xBE, 2): ( 8, 4), (0xBE, 3): ( 8, 5),

    # -------------------------------------------------------------------------
    # ROUTE 22 GATE
    # -------------------------------------------------------------------------
    # Route 22 Gate (0xC1) — x=0 and x=7 walls
    (0xC1, 0): (4, 8), (0xC1, 1): (5, 8),     # was (4,7)(5,7) → LAST_MAP
    (0xC1, 2): (4, -1),(0xC1, 3): (5, -1),    # was (4,0)(5,0) → LAST_MAP

    # -------------------------------------------------------------------------
    # POWER PLANT
    # -------------------------------------------------------------------------
    # Power Plant (0x53) — right exit at x=35, top exit at y=0
    (0x53, 0): (4, 36), (0x53, 1): (5, 36),   # was (4,35)(5,35) → LAST_MAP
    (0x53, 2): (-1, 11),                       # was (0,11) → LAST_MAP top wall

    # -------------------------------------------------------------------------
    # SILPH CO.
    # -------------------------------------------------------------------------
    # Silph Co. 1F (0xB5) — exit at x=17, left-wall stairs at x=0
    (0xB5, 0): (10, 18), (0xB5, 1): (11, 18), # was (10,17)(11,17)→ LAST_MAP
    (0xB5, 2): (26, -1),                       # was (26,0) → Silph 2F stairs
    (0xB5, 3): (20, -1),                       # was (20,0) → Elevator
    # Silph Co. 2F (0xCF) — all stairs at x=0
    (0xCF, 0): (24, -1),(0xCF, 1): (26, -1),(0xCF, 2): (20, -1),
    # Silph Co. 3F (0xD0)
    (0xD0, 0): (26, -1),(0xD0, 1): (24, -1),(0xD0, 2): (20, -1),
    # Silph Co. 4F (0xD1)
    (0xD1, 0): (24, -1),(0xD1, 1): (26, -1),(0xD1, 2): (20, -1),
    # Silph Co. 5F (0xD2)
    (0xD2, 0): (24, -1),(0xD2, 1): (26, -1),(0xD2, 2): (20, -1),
    # Silph Co. 6F (0xD3)
    (0xD3, 0): (16, -1),(0xD3, 1): (14, -1),(0xD3, 2): (18, -1),
    # Silph Co. 7F (0xD4)
    (0xD4, 0): (16, -1),(0xD4, 1): (22, -1),(0xD4, 2): (18, -1),
    # Silph Co. 8F (0xD5)
    (0xD5, 0): (16, -1),(0xD5, 1): (14, -1),(0xD5, 2): (18, -1),
    # Silph Co. 9F (0xE9)
    (0xE9, 0): (14, -1),(0xE9, 1): (16, -1),(0xE9, 2): (18, -1),
    # Silph Co. 10F (0xEA)
    (0xEA, 0): (8, -1), (0xEA, 1): (10, -1),(0xEA, 2): (12, -1),
    # Silph Co. 11F (0xEB) — stairs at x=0 (warps 0,1); LAST_MAP exit at (5,5) is interior
    (0xEB, 0): (9, -1), (0xEB, 1): (13, -1),

    # -------------------------------------------------------------------------
    # VICTORY ROAD 3F
    # -------------------------------------------------------------------------
    # Victory Road 3F (0xC6) — left wall exit back to 2F
    (0xC6, 3): (2, -1),                        # was (2,0) → Victory Road 2F

    # -------------------------------------------------------------------------
    # CERULEAN CAVE
    # -------------------------------------------------------------------------
    # Cerulean Cave 1F (0xE4) — outdoor exit at x=17
    (0xE4, 0): (24, 18), (0xE4, 1): (25, 18), # was (24,17)(25,17) → Cerulean City

    # -------------------------------------------------------------------------
    # CELADON MANSION ROOF
    # -------------------------------------------------------------------------
    # Celadon Mansion Roof (0x83) — roof house entrance at x=7
    (0x83, 2): (2, 8),                         # was (2,7) → Celadon Mansion Roof House

    # -------------------------------------------------------------------------
    # ROUTE GATE 2F BUILDINGS (staircase exit at y=7, x=7)
    # These upper floors have a single warp back down to Gate 1F.
    # -------------------------------------------------------------------------
    (0x56, 0): (8, 8),   # Route 11 Gate 2F — was (7,7) → Route 11 Gate 1F
    (0xC3, 0): (8, 8),   # Route 12 Gate 2F — was (7,7) → Route 12 Gate 1F
    (0xB9, 0): (8, 8),   # Route 15 Gate 2F — was (7,7) → Route 15 Gate 1F
    (0xBB, 0): (8, 8),   # Route 16 Gate 2F — was (7,7) → Route 16 Gate 1F
    (0xBF, 0): (8, 8),   # Route 18 Gate 2F — was (7,7) → Route 18 Gate 1F

    # -------------------------------------------------------------------------
    # UNDERGROUND PATHS (tunnel end-of-corridor exits)
    # -------------------------------------------------------------------------
    # Underground Path N-S (0x77) — south end exits at x=41 (right wall of tunnel)
    (0x77, 1): (2, 42),                        # was (2,41) → Underground Path Route 6
    # Underground Path W-E (0x79) — east end exits at y=47 (bottom wall of tunnel)
    (0x79, 1): (48, 2),                        # was (47,2) → Underground Path Route 8

    # -------------------------------------------------------------------------
    # POKEMON TOWER FLOORS (staircase at x=9 — interior, but included for completeness)
    # Stairs at (3,9) and (18,9) are interior tile positions; no wall-edge override needed.
    # -------------------------------------------------------------------------
    # (intentionally omitted — staircase tiles are navigable interior objects)

    # -------------------------------------------------------------------------
    # STAIRCASE / INTER-FLOOR WARPS
    # These lead to other floors of the same dungeon/building.
    # Wall-edge positions (y=7, x=7, x=0 etc.) are pushed one tile outside.
    # Interior positions are mapped to the same coordinates (no geometric change,
    # but ensures the override table is complete for every staircase trigger).
    # -------------------------------------------------------------------------

    # --- Red's House 2F (0x26) ---
    # Red's House 2F (0x26) — exit at (1,7)

    # --- Museum 2F (0x35) ---
    (0x35, 0): (8, 8),   # was (7,7) → Museum 1F (both at edge)

    # --- Celadon Mansion 1F stairs (0x80, warps 3-4) ---
    (0x80, 3): (8, 1),   # was (7,1) → Celadon Mansion 2F (y=7 bottom edge)
    (0x80, 4): (2, 1),   # was (2,1) → Celadon Mansion 2F (interior, same coords)

    # --- Celadon Mansion 2F stairs (0x81) ---
    (0x81, 0): (6, 1),   # was (6,1) → Celadon Mansion 3F (interior)
    (0x81, 1): (8, 1),   # was (7,1) → Celadon Mansion 1F (y=7 bottom edge)
    (0x81, 2): (2, 1),   # was (2,1) → Celadon Mansion 1F (interior)
    (0x81, 3): (4, 1),   # was (4,1) → Celadon Mansion 3F (interior)

    # --- Celadon Mansion 3F stairs (0x82) ---
    (0x82, 0): (6, 1),   # was (6,1) → Celadon Mansion 2F (interior)
    (0x82, 1): (8, 1),   # was (7,1) → Celadon Mansion Roof (y=7 bottom edge)
    (0x82, 2): (2, 1),   # was (2,1) → Celadon Mansion Roof (interior)
    (0x82, 3): (4, 1),   # was (4,1) → Celadon Mansion 2F (interior)

    # --- Celadon Mansion Roof stairs (0x83, warps 0-1) ---
    (0x83, 0): (6, 1),   # was (6,1) → Celadon Mansion 3F (interior)
    (0x83, 1): (2, 1),   # was (2,1) → Celadon Mansion 3F (interior)

    # --- Celadon Mart 1F stairs (0x7A, warps 4-5) ---
    (0x7A, 4): (12, 1),  # was (12,1) → Celadon Mart 2F (interior)
    (0x7A, 5): ( 1, 1),  # was (1,1)  → Celadon Mart Elevator (interior)

    # --- Celadon Mart 2F stairs (0x7B) ---
    (0x7B, 0): (12, 1),  (0x7B, 1): (16, 1),  (0x7B, 2): (1, 1),

    # --- Celadon Mart 3F stairs (0x7C) ---
    (0x7C, 0): (12, 1),  (0x7C, 1): (16, 1),  (0x7C, 2): (1, 1),

    # --- Celadon Mart 4F stairs (0x7D) ---
    (0x7D, 0): (12, 1),  (0x7D, 1): (16, 1),  (0x7D, 2): (1, 1),

    # --- Celadon Mart 5F stairs (0x88) ---
    (0x88, 0): (12, 1),  (0x88, 1): (16, 1),  (0x88, 2): (1, 1),

    # --- Celadon Mart Roof (0x7E) ---
    (0x7E, 0): (15, 2),  # was (15,2) → Celadon Mart 5F (interior)

    # --- Celadon Mart Elevator (0x7F) ---
    (0x7F, 0): (1, 3),   (0x7F, 1): (2, 3),   # was (1,3)(2,3) → Celadon Mart 1F

    # --- Copycat's House 2F (0xB0) ---
    (0xB0, 0): (8, 1),   # was (7,1) → Copycat's House 1F (y=7 bottom edge)

    # --- Pokemon Tower 2F (0x8F) ---
    (0x8F, 0): (3, 9),   (0x8F, 1): (18, 9),  # stairs at interior x=9

    # --- Pokemon Tower 3F (0x90) ---
    (0x90, 0): (3, 9),   (0x90, 1): (18, 9),

    # --- Pokemon Tower 4F (0x91) ---
    (0x91, 0): (3, 9),   (0x91, 1): (18, 9),

    # --- Pokemon Tower 5F (0x92) ---
    (0x92, 0): (3, 9),   (0x92, 1): (18, 9),

    # --- Pokemon Tower 6F (0x93) ---
    (0x93, 0): (18, 9),  (0x93, 1): (9, 16),  # second stair position

    # --- Pokemon Tower 7F (0x94) ---
    (0x94, 0): (9, 16),

    # --- Silph Co. warp pads (interior teleporters — same coords, no geometric change) ---
    # SilphCo3F (0xD0): teleport pads at (23,11) and (27,15)
    (0xD0, 3): (23, 11), (0xD0, 4): (3, 3),   (0xD0, 5): (3, 15),
    (0xD0, 6): (27, 3),  (0xD0, 7): (3, 11),  (0xD0, 8): (11, 11),
    (0xD0, 9): (27, 15),
    # SilphCo4F (0xD1): warp pads
    (0xD1, 3): (11, 7),  (0xD1, 4): (17, 3),  (0xD1, 5): (3, 15),
    (0xD1, 6): (17, 11),
    # SilphCo5F (0xD2): warp pads
    (0xD2, 3): (27, 3),  (0xD2, 4): (9, 15),  (0xD2, 5): (11, 5),
    (0xD2, 6): (3, 15),
    # SilphCo6F (0xD3): warp pads
    (0xD3, 3): (3, 3),   (0xD3, 4): (23, 3),
    # SilphCo7F (0xD4): warp pads
    (0xD4, 3): (5, 7),   (0xD4, 4): (5, 3),   (0xD4, 5): (21, 15),
    # SilphCo8F (0xD5): teleport pads
    (0xD5, 3): (3, 11),  (0xD5, 4): (3, 15),  (0xD5, 5): (11, 5),
    (0xD5, 6): (11, 9),
    # SilphCo9F (0xE9): warp pads
    (0xE9, 3): (9, 3),   (0xE9, 4): (17, 15),
    # SilphCo10F (0xEA): warp pads
    (0xEA, 3): (9, 11),  (0xEA, 4): (13, 15), (0xEA, 5): (13, 7),
    # SilphCo11F (0xEB): warp pad + LAST_MAP exit (interior at (5,5))
    (0xEB, 2): (5, 5),   (0xEB, 3): (3, 2),

    # --- Mt. Moon B1F (0x3C) — cave stairs, all interior positions ---
    (0x3C, 0): (5, 5),   (0x3C, 1): (11, 17), (0x3C, 2): (9, 25),
    (0x3C, 3): (15, 25), (0x3C, 4): (17, 21), (0x3C, 5): (27, 13),
    (0x3C, 6): (3, 23),  (0x3C, 7): (3, 27),  # warp 7 is Route 4 exit

    # --- Mt. Moon B2F (0x3D) ---
    (0x3D, 0): (9, 25),  (0x3D, 1): (17, 21), (0x3D, 2): (27, 15),
    (0x3D, 3): (7, 5),

    # --- Rock Tunnel B1F (0xE8) — cave stairs ---
    (0xE8, 0): (33, 25), (0xE8, 1): (27, 3),  (0xE8, 2): (23, 11),
    (0xE8, 3): (3, 3),

    # --- Cerulean Cave 2F (0xE2) ---
    (0xE2, 0): (29, 1),  (0xE2, 1): (22, 6),  (0xE2, 2): (19, 7),
    (0xE2, 3): (9, 1),   (0xE2, 4): (1, 3),   (0xE2, 5): (3, 11),

    # --- Cerulean Cave B1F (0xE3) ---
    (0xE3, 0): (3, 6),

    # --- Seafoam Islands B1F (0x9F) ---
    (0x9F, 0): (4, 2),   (0x9F, 1): (7, 5),   (0x9F, 2): (13, 7),
    (0x9F, 3): (19, 15), (0x9F, 4): (23, 15), (0x9F, 5): (25, 11),
    (0x9F, 6): (25, 3),

    # --- Seafoam Islands B2F (0xA0) ---
    (0xA0, 0): (5, 3),   (0xA0, 1): (5, 13),  (0xA0, 2): (13, 7),
    (0xA0, 3): (19, 15), (0xA0, 4): (25, 3),  (0xA0, 5): (25, 11),
    (0xA0, 6): (25, 14),

    # --- Seafoam Islands B3F (0xA1) ---
    (0xA1, 0): (5, 12),  (0xA1, 1): (8, 6),   (0xA1, 2): (25, 4),
    (0xA1, 3): (25, 3),  (0xA1, 4): (25, 14), (0xA1, 5): (20, 17),
    (0xA1, 6): (21, 17),

    # --- Seafoam Islands B4F (0xA2) ---
    (0xA2, 0): (20, 17), (0xA2, 1): (21, 17), (0xA2, 2): (11, 7),
    (0xA2, 3): (25, 4),

    # --- Rocket Hideout B1F (0xC7) ---
    (0xC7, 0): (23, 2),  (0xC7, 1): (21, 2),  (0xC7, 2): (24, 19),
    (0xC7, 3): (21, 24), (0xC7, 4): (25, 19),

    # --- Rocket Hideout B2F (0xC8) ---
    (0xC8, 0): (27, 8),  (0xC8, 1): (21, 8),  (0xC8, 2): (24, 19),
    (0xC8, 3): (21, 22), (0xC8, 4): (25, 19),

    # --- Rocket Hideout B3F (0xC9) ---
    (0xC9, 0): (25, 6),  (0xC9, 1): (19, 18),

    # --- Rocket Hideout B4F (0xCA) ---
    (0xCA, 0): (19, 10), (0xCA, 1): (24, 15), (0xCA, 2): (25, 15),

    # --- Rocket Hideout Elevator (0xCB) ---
    (0xCB, 0): (2, 1),   (0xCB, 1): (3, 1),

    # --- Pokemon Mansion 2F (0xD6) ---
    (0xD6, 0): (5, 10),  (0xD6, 1): (7, 10),  (0xD6, 2): (25, 14),
    (0xD6, 3): (6, 1),

    # --- Pokemon Mansion 3F (0xD7) ---
    (0xD7, 0): (7, 10),  (0xD7, 1): (6, 1),   (0xD7, 2): (25, 14),

    # --- Pokemon Mansion B1F (0xD8) ---
    (0xD8, 0): (23, 22),

    # --- Victory Road 2F inter-floor warps (0xC2, warps 2-5) ---
    (0xC2, 2): (23, 7),  (0xC2, 3): (25, 14), (0xC2, 4): (27, 7),
    (0xC2, 5): (1, 1),

    # --- Victory Road 3F inter-floor warps (0xC6, warps 0-2) ---
    (0xC6, 0): (23, 7),  (0xC6, 1): (26, 8),  (0xC6, 2): (27, 15),

    # --- SS Anne 2F inter-floor warps (0x60) ---
    (0x60, 0): (9, 11),  (0x60, 1): (13, 11), (0x60, 2): (17, 11),
    (0x60, 3): (21, 11), (0x60, 4): (25, 11), (0x60, 5): (29, 11),
    (0x60, 6): (2, 4),   (0x60, 7): (2, 12),  (0x60, 8): (36, 4),

    # --- SS Anne B1F inter-floor warps (0x62) ---
    (0x62, 0): (23, 3),  (0x62, 1): (19, 3),  (0x62, 2): (15, 3),
    (0x62, 3): (11, 3),  (0x62, 4): (7, 3),   (0x62, 5): (27, 5),

    # --- Cinnabar Lab room warps (0xA7, warps 2-4) ---
    (0xA7, 2): (8, 4),   (0xA7, 3): (12, 4),  (0xA7, 4): (16, 4),

    # --- Diglett's Cave interior warps (0xC5) ---
    (0xC5, 0): (5, 5),   (0xC5, 1): (37, 31),

    # --- Underground Path N-S interior warp (0x77, warp 0) ---
    (0x77, 0): (5, 4),

    # --- Underground Path W-E interior warp (0x79, warp 0) ---
    (0x79, 0): (2, 5),

    # --- Silph Co. 1F interior warp (0xB5, warp 4) ---
    (0xB5, 4): (16, 10),
}
