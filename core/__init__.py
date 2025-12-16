"""
场景格式插件核心模块
"""
from .scene_db import SceneDB
from .llm_client import LLMClient, LLMClientFactory
from .nai_client import NaiClient
from .preset_manager import PresetManager
from .utils import (
    safe_json_loads, parse_json_response, parse_structured_text,
    collapse_text, truncate_text, parse_datetime,
    parse_status_json_fields, build_session_id,
    normalize_planner_decision, get_default_decision
)
from .state_manager import (
    StateManager,
    SCENE_TYPE_NORMAL, SCENE_TYPE_ROMANTIC,
    SCENE_TYPE_INTIMATE, SCENE_TYPE_EXPLICIT, SCENE_TYPE_REST
)
from .status_formatter import StatusFormatter
from .context_builder import ContextBuilder
from .scene_generator import SceneGenerator

__all__ = [
    # 数据库
    "SceneDB",
    # LLM
    "LLMClient", "LLMClientFactory",
    # NAI
    "NaiClient",
    # 预设
    "PresetManager",
    # 工具函数
    "safe_json_loads", "parse_json_response", "parse_structured_text",
    "collapse_text", "truncate_text", "parse_datetime",
    "parse_status_json_fields", "build_session_id",
    "normalize_planner_decision", "get_default_decision",
    # 状态管理
    "StateManager",
    "SCENE_TYPE_NORMAL", "SCENE_TYPE_ROMANTIC",
    "SCENE_TYPE_INTIMATE", "SCENE_TYPE_EXPLICIT", "SCENE_TYPE_REST",
    # 格式化
    "StatusFormatter",
    # 上下文
    "ContextBuilder",
    # 场景生成
    "SceneGenerator",
]
