from typing import Optional, TypedDict


class ModelConfig(TypedDict):
    MODEL: str
    THINKING: bool
    DYNAMIC_THINKING: bool
    EFFICIENT_TOOLS: bool
    MAX_TOKENS: int
    THINKING_BUDGET: int


class MemoryConfig(TypedDict):
    MEMORY_INTERVAL: int  # Update memory every N turns via background subagent
    MODEL: str            # Model for memory subagent (default: claude-haiku-4-5)
    MAX_TOKENS: int       # Max tokens for memory subagent response


class ActionConfig(ModelConfig):
    pass


class ConfigClass:
    """Configuration settings for the game agent."""
    ROM_PATH: str
    STATE_PATH: Optional[str]
    LOG_FILE: str
    EMULATION_SPEED: int
    CONTINUOUS_ANALYSIS_INTERVAL: float
    MAX_ADAPTIVE_INTERVAL: float
    ENABLE_SPATIAL_CONTEXT: bool
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
