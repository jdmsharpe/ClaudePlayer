import json
import os.path
import logging
from claude_player.config.config_class import ConfigClass


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate_config(config: ConfigClass) -> None:
    """Validate critical configuration values, raising ValueError on problems."""
    if config.MODEL_DEFAULTS["THINKING_BUDGET"] < 1024:
        raise ValueError(
            f"THINKING_BUDGET ({config.MODEL_DEFAULTS['THINKING_BUDGET']}) must be >= 1024 (API minimum)"
        )

    if config.MODEL_DEFAULTS["THINKING_BUDGET"] >= config.MODEL_DEFAULTS["MAX_TOKENS"]:
        raise ValueError(
            f"THINKING_BUDGET ({config.MODEL_DEFAULTS['THINKING_BUDGET']}) must be less than "
            f"MAX_TOKENS ({config.MODEL_DEFAULTS['MAX_TOKENS']})"
        )

    if config.MAX_HISTORY_MESSAGES < 2:
        raise ValueError(f"MAX_HISTORY_MESSAGES must be >= 2, got {config.MAX_HISTORY_MESSAGES}")

    if config.MAX_SCREENSHOTS < 1:
        raise ValueError(f"MAX_SCREENSHOTS must be >= 1, got {config.MAX_SCREENSHOTS}")


def load_config(config_file='config.json') -> ConfigClass:
    """
    Load configuration from a JSON file with fallback to default values.
    If the configuration file doesn't exist, it will be created with default values.

    Args:
        config_file: Path to the configuration file (default: 'config.json')

    Returns:
        Configuration object with loaded values or defaults
    """
    default_config = {
        "ROM_PATH": "red.gb",
        "STATE_PATH": None,
        "LOG_FILE": "game_agent.log",
        "EMULATION_SPEED": 1,
        "CONTINUOUS_ANALYSIS_INTERVAL": 3.0,
        "MAX_ADAPTIVE_INTERVAL": 15.0,
        "ENABLE_SPATIAL_CONTEXT": True,
        "ENABLE_SOUND": True,
        "MAX_HISTORY_MESSAGES": 15,
        "MAX_SCREENSHOTS": 2,
        "BOOT_FRAMES": 400,
        "CUSTOM_INSTRUCTIONS": "",

        "MODEL_DEFAULTS": {
            "MODEL": "claude-haiku-4-5",
            "THINKING": True,
            "DYNAMIC_THINKING": True,
            "EFFICIENT_TOOLS": True,
            "MAX_TOKENS": 16384,
            "THINKING_BUDGET": 10000,
        },

        "ACTION": {},

        "SUMMARY": {
            "INITIAL_SUMMARY": True,
            "SUMMARY_INTERVAL": 30,
            "MODEL": "claude-haiku-4-5",
            "THINKING": False,
        },
    }

    config = ConfigClass()

    # Load configuration from file if it exists
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                file_config = json.load(f)

            print(f"Loading configuration from {config_file}")

            # Deep merge so partial nested dicts don't clobber defaults
            default_config = _deep_merge(default_config, file_config)
        except Exception as e:
            print(f"Error loading configuration file: {str(e)}")
            print("Using default configuration values")
    else:
        print(f"Configuration file '{config_file}' not found, creating with default values")
        try:
            with open(config_file, 'w') as f:
                json.dump(default_config, f, indent=2)
            print(f"Created default configuration file: {config_file}")
        except Exception as e:
            print(f"Error creating configuration file: {str(e)}")

    # Set configuration attributes
    for key, value in default_config.items():
        setattr(config, key, value)

    # Apply MODEL_DEFAULTS to ACTION and SUMMARY (mode settings override defaults)
    for mode in ("ACTION", "SUMMARY"):
        mode_config = default_config.get(mode, {}).copy()
        for k, v in default_config["MODEL_DEFAULTS"].items():
            if k not in mode_config:
                mode_config[k] = v
        setattr(config, mode, mode_config)

    _validate_config(config)

    return config


def setup_logging(config: ConfigClass):
    """Configure logging for the application.

    All INFO+ messages go to the log file. The terminal only receives
    WARNING+ so it stays clean for the live status display.
    """
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')

    file_handler = logging.FileHandler(config.LOG_FILE)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.WARNING)
    stream_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
