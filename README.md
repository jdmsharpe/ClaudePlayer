# Claude Player

An AI-powered game playing agent using Claude and PyBoy

![Game Screenshot](image.png)

[![Python 3.10](https://img.shields.io/badge/python-3.10-blue.svg)](https://www.python.org/downloads/release/python-31012/)
[![PyBoy](https://img.shields.io/badge/emulator-PyBoy-green.svg)](https://github.com/Baekalfen/PyBoy)
[![Claude](https://img.shields.io/badge/AI-Claude%203.7-purple.svg)](https://anthropic.com/claude)

## Overview

Claude Player is an AI agent that lets Claude play Game Boy games through the PyBoy emulator. The agent observes game frames, makes strategic decisions, and controls the emulator through button inputs.

I have been working on this project for a while, and have been meaning to clean it up and release it, and with the release of Claude 3.7 (especially given their semi official <https://www.twitch.tv/claudeplayspokemon> stream of a similar project), I thought it was a good time to do so.

I've taken some imspiration from their official implementation by adding additional memory tools and summarisation, however mine differs in that I don't have any coordinate based movement helpers: it is purely button based. Additionally, the emulator only ticks when the AI sends inputs, so it is not running at real time speed.

## Features

- **AI-Powered Gameplay**: Uses Claude 3.7 to analyze game frames and determine optimal actions
- **Dual Emulation Modes**:
  - **Turn-based**: Emulator only advances when AI sends inputs (default)
  - **Continuous**: Real-time gameplay with periodic AI analysis
- **Memory System**: Short-term and long-term memory to maintain game context
- **Automatic Summarization**: Periodically generates game progress summaries
- **Tool-Based Control**: Structured tools for game interaction and state management
- **Screenshot Capture**: Automatically saves frames for analysis and debugging

## Requirements

- Python 3.10+
- PyBoy emulator
- Anthropic API key
- Game Boy ROM files
- Optional: Saved state files

## Installation

1. Clone the repository:

   ```
   git clone https://github.com/jmurth1234/claude-player.git
   cd claude-player
   ```

2. Install dependencies using Pipenv (recommended):

   ```
   pipenv install
   ```

3. Create a `.env` file with your Anthropic API key:

   ```
   ANTHROPIC_API_KEY=your_api_key_here
   ```

4. Place your Game Boy ROM file in the project directory

## Configuration

Configuration is loaded from `config.json` (created automatically on first run if not found). The settings are structured to avoid duplication between different modes. You can customize Claude's behavior by adding custom instructions that will be injected into the system prompt.

```json
{
  "ROM_PATH": "red.gb",                   // Path to the Game Boy ROM file
  "STATE_PATH": null,                     // Optional path to a saved state (null = auto-load from saves/)
  "LOG_FILE": "game_agent.log",           // Path to the log file
  "EMULATION_SPEED": 1,                   // Emulation speed multiplier
  "CONTINUOUS_ANALYSIS_INTERVAL": 1.0,    // Base analysis interval in seconds
  "MAX_ADAPTIVE_INTERVAL": 15.0,          // Max interval when agent is idle
  "ENABLE_SPATIAL_CONTEXT": true,         // Whether to include map/grid context in prompts
  "GRID_IN_PROMPT": false,                 // false = replace ASCII grid with compact direction summary in API calls
  "ENABLE_SOUND": true,                   // Whether to enable emulator sound
  "MAX_HISTORY_MESSAGES": 15,             // Max messages kept in context window
  "MAX_SCREENSHOTS": 1,                   // Max recent screenshots kept in chat history
  "BOOT_FRAMES": 400,                     // Frames to tick before first analysis
  "WEB_PORT": 0,                          // HTTP dashboard port (0 = disabled)
  "CUSTOM_INSTRUCTIONS": "",              // Extra instructions injected into Claude's system prompt

  // Default model settings — inherited by ACTION if not overridden
  "MODEL_DEFAULTS": {
    "MODEL": "claude-opus-4-6",          // Claude model to use
    "THINKING": true,                    // Enable extended thinking (adaptive on Opus 4.6)
    "DYNAMIC_THINKING": true,            // Allow Claude to toggle thinking on/off
    "EFFICIENT_TOOLS": true,             // Use token-efficient-tools beta
    "MAX_TOKENS": 4096,                  // Maximum tokens per response
    "EFFORT": "medium"                   // low/medium/high/max — controls thinking depth
    // "THINKING_BUDGET": 1024           // Optional: set for budget_tokens mode (Sonnet)
  },

  // Action mode overrides (inherits MODEL_DEFAULTS; add keys here to override)
  "ACTION": {},

  // Memory agent settings (inherits MODEL_DEFAULTS; add keys here to override)
  "MEMORY": {
    "MEMORY_INTERVAL": 20,               // Run memory agent every N turns
    "MODEL": "claude-opus-4-6",          // Claude model to use
    "THINKING": true,
    "DYNAMIC_THINKING": true,
    "EFFICIENT_TOOLS": true,
    "MAX_TOKENS": 16000,
    "EFFORT": "medium"
  }
}
```

You can customize these settings by:

1. Editing the generated `config.json` file directly
2. Creating your own configuration file and specifying it with:

   ```
   python play.py --config my_config.json
   ```

## Usage

1. Activate the Pipenv environment:

   ```
   pipenv shell
   ```

2. Run the agent:

   ```
   python play.py
   ```

   Or specify a custom configuration file:

   ```
   python play.py --config my_config.json
   ```

3. For setting up a saved state, you can use the included utility script:

   ```
   python emu_setup.py
   ```

   This script runs the emulator to help you create a saved state that you can reference in your configuration.

## Game Controls

The agent uses a structured notation for game inputs:

- **Single Press**: `A` (press A once)
- **Hold**: `A2` (hold A for 2 ticks)
- **Simultaneous**: `AB` (press A and B together)
- **Wait**: `W` or `W2` (wait for 1 or 2 ticks)
- **Sequence**: `R2 A U3` (right for 2 ticks, A once, up for 3 ticks)

Available buttons: `U` (Up), `D` (Down), `L` (Left), `R` (Right), `A`, `B`, `S` (Start), `X` (Select), `W` (Wait)

## Tool System

The AI uses several tools to interact with the game:

- `send_inputs`: Send button sequences to the emulator
- `set_strategic_goal`: Override the auto-detected strategic milestone goal
- `set_tactical_goal`: Set an immediate map-specific action (auto-clears on map change)
- `read_from_memory`: Read the persistent memory file (routes, puzzle progress, past mistakes)
- `delete_memory`: Permanently delete the memory file if it becomes corrupted
- `toggle_thinking`: Dynamically enable or disable extended thinking (requires `DYNAMIC_THINKING: true` in config)

## Debugging

- Game frames are saved to `./frames/{timestamp}/`
- Detailed logs are written to `game_agent.log`

## Contributing

Contributions welcome! Please feel free to submit a Pull Request.

## License

[MIT License](LICENSE)

## Acknowledgments

- Anthropic for the Claude AI model
- PyBoy developers for the Game Boy emulator
