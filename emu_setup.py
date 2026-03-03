"""Utility to manually run the emulator for creating/updating save states."""
import sys
from pyboy import PyBoy

ROM_PATH = sys.argv[1] if len(sys.argv) > 1 else "gold.gbc"
STATE_PATH = sys.argv[2] if len(sys.argv) > 2 else None

pyboy = PyBoy(ROM_PATH)
print(pyboy.cartridge_title())

if STATE_PATH:
    with open(STATE_PATH, "rb") as f:
        pyboy.load_state(f)

while not pyboy.tick():
    pass

