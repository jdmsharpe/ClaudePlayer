import json
import os.path
import logging
from logging.handlers import RotatingFileHandler
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
        "GBC_COLOR_PALETTE": "red",
        "STATE_PATH": None,
        "LOG_FILE": "game_agent.log",
        "EMULATION_SPEED": 1,
        "CONTINUOUS_ANALYSIS_INTERVAL": 1.0,
        "MAX_ADAPTIVE_INTERVAL": 15.0,
        "ENABLE_SPATIAL_CONTEXT": True,
        "ENABLE_SOUND": True,
        "SOUND_VOLUME": 50,
        "MAX_HISTORY_MESSAGES": 15,
        "MAX_SCREENSHOTS": 1,
        "BOOT_FRAMES": 400,
        "CUSTOM_INSTRUCTIONS": "",
        "WEB_PORT": 0,

        "MODEL_DEFAULTS": {
            "MODEL": "claude-sonnet-4-6",
            "THINKING": True,
            "DYNAMIC_THINKING": True,
            "EFFICIENT_TOOLS": True,
            "MAX_TOKENS": 2048,
            "THINKING_BUDGET": 1024,
        },

        "ACTION": {},

        "MEMORY": {
            "MEMORY_INTERVAL": 30,
            "MODEL": "claude-sonnet-4-6",
            "THINKING": True,
            "DYNAMIC_THINKING": True,
            "EFFICIENT_TOOLS": True,
            "MAX_TOKENS": 8192,
            "THINKING_BUDGET": 4096
        },

        "STUCK": {
            # Minimum visit count to a single tile before it's "cycling"
            "CYCLING_MIN_VISITS": 4,
            # x/y range thresholds for "confined to small area" detection
            "SMALL_AREA_X": 6,
            "SMALL_AREA_Y": 6,
            # x/y range thresholds for lateral "thrashing" detection
            "THRASH_X": 8,
            "THRASH_Y": 3,
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

    # Apply MODEL_DEFAULTS to ACTION (mode settings override defaults)
    action_config = default_config.get("ACTION", {}).copy()
    for k, v in default_config["MODEL_DEFAULTS"].items():
        if k not in action_config:
            action_config[k] = v
    config.ACTION = action_config

    # MEMORY config (no model defaults needed — agent writes directly)
    config.MEMORY = default_config.get("MEMORY", {"MEMORY_INTERVAL": 20})

    # Backward compat: if old config.json has SUMMARY.SUMMARY_INTERVAL, use it
    legacy = default_config.get("SUMMARY", {})
    if legacy.get("SUMMARY_INTERVAL") and config.MEMORY.get("MEMORY_INTERVAL", 20) == 20:
        config.MEMORY["MEMORY_INTERVAL"] = legacy["SUMMARY_INTERVAL"]

    _validate_config(config)

    return config


def setup_logging(config: ConfigClass):
    """Configure logging for the application.

    All INFO+ messages go to the log file. The terminal only receives
    WARNING+ so it stays clean for the live status display.
    """
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')

    file_handler = RotatingFileHandler(config.LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=2)
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
