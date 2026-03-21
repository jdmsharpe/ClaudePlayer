from typing import Optional, Tuple, TypedDict


class ModelConfig(TypedDict, total=False):
    MODEL: str
    THINKING: bool
    DYNAMIC_THINKING: bool
    EFFICIENT_TOOLS: bool
    MAX_TOKENS: int
    EFFORT: str           # "low" | "medium" | "high" | "max" — controls thinking depth + tool calls
    THINKING_BUDGET: int  # Optional: if set, use budget_tokens; if absent, use adaptive thinking


class MemoryConfig(TypedDict, total=False):
    MEMORY_INTERVAL: int  # Update memory every N turns via background subagent
    MODEL: str            # Model for memory subagent
    MAX_TOKENS: int       # Max tokens for memory subagent response
    THINKING: bool        # Enable extended thinking for memory subagent
    EFFORT: str           # Effort level for memory subagent
    THINKING_BUDGET: int  # Optional: if set, use budget_tokens instead of adaptive


class ActionConfig(ModelConfig):
    pass


class ConfigClass:
    """Configuration settings for the game agent."""
    ROM_PATH: str
    GBC_COLOR_PALETTE: Optional[object]  # preset name (str), custom triple, or None
    STATE_PATH: Optional[str]
    LOG_FILE: str
    EMULATION_SPEED: int
    CONTINUOUS_ANALYSIS_INTERVAL: float
    MAX_ADAPTIVE_INTERVAL: float
    ENABLE_SPATIAL_CONTEXT: bool
    GRID_IN_PROMPT: bool  # Include ASCII grid in API prompt (False = compact direction summary)
    ENABLE_SOUND: bool
    SOUND_VOLUME: int  # 0-100
    MAX_HISTORY_MESSAGES: int
    MAX_SCREENSHOTS: int
    BOOT_FRAMES: int
    CUSTOM_INSTRUCTIONS: Optional[str]
    WEB_PORT: int
    MODEL_DEFAULTS: ModelConfig
    ACTION: ActionConfig
    MEMORY: MemoryConfig
    STUCK: dict
