"""Pokemon Red map data: ID -> name mapping.

Complete mapping verified against:
https://github.com/pret/pokered/blob/master/constants/map_constants.asm
"""

from typing import Dict

# ---------------------------------------------------------------------------
# Map ID -> human-readable name
# ---------------------------------------------------------------------------

MAP_NAMES: Dict[int, str] = {
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
