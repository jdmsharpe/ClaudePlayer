# GBC color palette presets for DMG (original Game Boy) games running in CGB mode.
# These match the 12 manual-select palettes from the Game Boy Color bootstrap ROM,
# using RGB888 hex values sourced from gambatte-libretro/gbcpalettes.h.
#
# Each preset is a tuple of 3 sub-palettes (BG, OBJ0, OBJ1),
# where each sub-palette is a tuple of 4 RGB888 integers (lightest to darkest).
#
# Reference: https://github.com/libretro/gambatte-libretro/blob/master/libgambatte/libretro/gbcpalettes.h

GBC_PALETTE_PRESETS = {
    "blue": (
        (0xFFFFFF, 0x63A5FF, 0x0000FF, 0x000000),  # BG
        (0xFFFFFF, 0xFF8484, 0x943A3A, 0x000000),  # OBJ0
        (0xFFFFFF, 0x7BFF31, 0x008400, 0x000000),  # OBJ1
    ),
    "brown": (
        (0xFFFFFF, 0xFFAD63, 0x843100, 0x000000),
        (0xFFFFFF, 0xFFAD63, 0x843100, 0x000000),
        (0xFFFFFF, 0xFFAD63, 0x843100, 0x000000),
    ),
    "dark_blue": (
        (0xFFFFFF, 0x8C8CDE, 0x52528C, 0x000000),
        (0xFFFFFF, 0xFF8484, 0x943A3A, 0x000000),
        (0xFFFFFF, 0xFF8484, 0x943A3A, 0x000000),
    ),
    "dark_brown": (
        (0xFFE6C5, 0xCE9C84, 0x846B29, 0x5A3108),
        (0xFFFFFF, 0xFFAD63, 0x843100, 0x000000),
        (0xFFFFFF, 0xFFAD63, 0x843100, 0x000000),
    ),
    "dark_green": (
        (0xFFFFFF, 0x7BFF31, 0x0063C5, 0x000000),
        (0xFFFFFF, 0xFF8484, 0x943A3A, 0x000000),
        (0xFFFFFF, 0xFF8484, 0x943A3A, 0x000000),
    ),
    "grayscale": (
        (0xFFFFFF, 0xA5A5A5, 0x525252, 0x000000),
        (0xFFFFFF, 0xA5A5A5, 0x525252, 0x000000),
        (0xFFFFFF, 0xA5A5A5, 0x525252, 0x000000),
    ),
    "green": (
        (0xFFFFFF, 0x52FF00, 0xFF4200, 0x000000),
        (0xFFFFFF, 0x52FF00, 0xFF4200, 0x000000),
        (0xFFFFFF, 0x52FF00, 0xFF4200, 0x000000),
    ),
    "inverted": (
        (0x000000, 0x008484, 0xFFDE00, 0xFFFFFF),
        (0x000000, 0x008484, 0xFFDE00, 0xFFFFFF),
        (0x000000, 0x008484, 0xFFDE00, 0xFFFFFF),
    ),
    "orange": (
        (0xFFFFFF, 0xFFFF00, 0xFF0000, 0x000000),
        (0xFFFFFF, 0xFFFF00, 0xFF0000, 0x000000),
        (0xFFFFFF, 0xFFFF00, 0xFF0000, 0x000000),
    ),
    "pastel_mix": (
        (0xFFFFA5, 0xFF9494, 0x9494FF, 0x000000),
        (0xFFFFA5, 0xFF9494, 0x9494FF, 0x000000),
        (0xFFFFA5, 0xFF9494, 0x9494FF, 0x000000),
    ),
    "red": (
        (0xFFFFFF, 0xFF8484, 0x943A3A, 0x000000),
        (0xFFFFFF, 0x7BFF31, 0x008400, 0x000000),
        (0xFFFFFF, 0x63A5FF, 0x0000FF, 0x000000),
    ),
    "yellow": (
        (0xFFFFFF, 0xFFFF00, 0x7B4A00, 0x000000),
        (0xFFFFFF, 0x63A5FF, 0x0000FF, 0x000000),
        (0xFFFFFF, 0x7BFF31, 0x008400, 0x000000),
    ),
}


def resolve_palette(value):
    """Resolve a palette config value to a cgb_color_palette tuple for PyBoy.

    Args:
        value: One of:
            - None: no palette override (use PyBoy default)
            - str: preset name (e.g. "blue", "grayscale")
            - list/tuple: custom ((BG...), (OBJ0...), (OBJ1...)) triple

    Returns:
        A tuple of 3 sub-palettes suitable for PyBoy's cgb_color_palette kwarg,
        or None if no palette should be applied.

    Raises:
        ValueError: if the preset name is unknown or format is invalid.
    """
    if value is None:
        return None

    if isinstance(value, str):
        key = value.lower().replace("-", "_").replace(" ", "_")
        if key not in GBC_PALETTE_PRESETS:
            names = ", ".join(sorted(GBC_PALETTE_PRESETS))
            raise ValueError(f"Unknown GBC palette preset '{value}'. Available: {names}")
        return GBC_PALETTE_PRESETS[key]

    # Assume it's a custom palette: list/tuple of 3 sub-palettes of 4 ints each
    try:
        pal = tuple(tuple(int(c) for c in sub) for sub in value)
        if len(pal) != 3 or any(len(sub) != 4 for sub in pal):
            raise ValueError
        return pal
    except (TypeError, ValueError):
        raise ValueError(
            "GBC_COLOR_PALETTE must be a preset name (e.g. 'blue'), "
            "or a list of 3 sub-palettes each with 4 RGB integer colors."
        )
