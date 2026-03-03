from typing import Optional, TypedDict


class ModelConfig(TypedDict):
    MODEL: str
    THINKING: bool
    DYNAMIC_THINKING: bool
    EFFICIENT_TOOLS: bool
    MAX_TOKENS: int
    THINKING_BUDGET: int


class SummaryConfig(ModelConfig):
    INITIAL_SUMMARY: bool
    SUMMARY_INTERVAL: int


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
    MAX_HISTORY_MESSAGES: int
    MAX_SCREENSHOTS: int
    BOOT_FRAMES: int
    CUSTOM_INSTRUCTIONS: Optional[str]
    MODEL_DEFAULTS: ModelConfig
    ACTION: ActionConfig
    SUMMARY: SummaryConfig
