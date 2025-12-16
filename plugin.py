"""
场景格式插件主类
"""
from typing import List, Tuple, Type

from src.plugin_system.base.base_plugin import BasePlugin
from src.plugin_system.base.component_types import ComponentInfo
from src.plugin_system import register_plugin
from src.plugin_system.base.config_types import ConfigField

from .commands.schedule_command import ScheduleGenerateCommand, ScheduleViewCommand
from .commands.scene_command import SceneCommand
from .commands.preset_command import PresetCommand
from .commands.help_command import HelpCommand
from .commands.admin_command import SceneAdminCommand
from .commands.custom_init_command import CustomInitCommand
from .commands.status_command import StatusCommand
from .commands.nai_command import NaiControlCommand
from .handlers.scene_handler import SceneFormatHandler
from .handlers.schedule_handler import DailyScheduleEventHandler


@register_plugin
class SceneFormatPlugin(BasePlugin):
    """场景格式插件 - 提供场景模式对话功能"""

    # 插件基本信息
    plugin_name = "scene_format_plugin"
    plugin_version = "1.0.0"
    plugin_author = "MaiBot"
    enable_plugin = True
    dependencies: List[str] = []
    python_dependencies: List[str] = []
    config_file_name = "config.toml"

    # 配置节描述
    config_section_descriptions = {
        "plugin": "插件基本配置",
        "scene": "场景相关配置",
        "llm": "LLM 模型配置",
        "nai": "NAI 生图配置",
        "admin": "管理员权限配置",
        "logging": "日志配置"
    }

    # 配置Schema
    config_schema = {
        "plugin": {
            "name": ConfigField(
                type=str,
                default="scene_format_plugin",
                description="场景格式插件",
                required=True
            ),
            "config_version": ConfigField(
                type=str,
                default="1.1.0",
                description="配置版本"
            ),
            "enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用插件"
            )
        },
        "scene": {
            "max_scene_length": ConfigField(
                type=int,
                default=500,
                description="场景描述的最大长度（字符数）"
            ),
            "model_mode": ConfigField(
                type=str,
                default="dual",
                description="模型调用模式：dual（双模型）或 single（单模型）",
                choices=["dual", "single"]
            ),
            "planner_context_messages": ConfigField(
                type=int,
                default=1,
                description="Planner模型使用的历史上下文条数（状态判断用）"
            ),
            "reply_context_messages": ConfigField(
                type=int,
                default=10,
                description="Reply模型使用的历史上下文条数（场景生成用）"
            ),
            "appearance_description": ConfigField(
                type=str,
                default="",
                description="NAI生图外貌提示词（直接使用，不翻译）"
            ),
            "schedule.enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用每日自动生成日程"
            ),
            "schedule.time": ConfigField(
                type=str,
                default="05:00",
                description="每天自动生成日程的时间（HH:MM格式）"
            ),
            "status_bar.enabled": ConfigField(
                type=bool,
                default=True,
                description="是否显示状态栏"
            ),
            "status_bar.display_mode": ConfigField(
                type=str,
                default="compact",
                description="状态栏显示模式：compact/full/changes_only",
                choices=["compact", "full", "changes_only"]
            ),
            "status_bar.position": ConfigField(
                type=str,
                default="bottom",
                description="状态栏位置：top/bottom",
                choices=["top", "bottom"]
            ),
            "status_bar.use_progress_bar": ConfigField(
                type=bool,
                default=True,
                description="是否使用进度条显示快感值"
            ),
            "status_changes.enabled": ConfigField(
                type=bool,
                default=True,
                description="是否显示状态变化提示"
            ),
            "status_changes.format": ConfigField(
                type=str,
                default="detailed",
                description="状态变化格式：detailed/simple",
                choices=["detailed", "simple"]
            )
        },
        "llm": {
            "planner.use_custom_api": ConfigField(
                type=bool,
                default=False,
                description="Planner模型是否使用自定义API"
            ),
            "planner.base_url": ConfigField(
                type=str,
                default="",
                description="Planner模型API地址"
            ),
            "planner.api_key": ConfigField(
                type=str,
                default="",
                description="Planner模型API密钥"
            ),
            "planner.model": ConfigField(
                type=str,
                default="",
                description="Planner模型名称"
            ),
            "reply.use_custom_api": ConfigField(
                type=bool,
                default=False,
                description="Reply模型是否使用自定义API"
            ),
            "reply.base_url": ConfigField(
                type=str,
                default="",
                description="Reply模型API地址"
            ),
            "reply.api_key": ConfigField(
                type=str,
                default="",
                description="Reply模型API密钥"
            ),
            "reply.model": ConfigField(
                type=str,
                default="",
                description="Reply模型名称"
            )
        },
        "nai": {
            "api_key": ConfigField(
                type=str,
                default="",
                description="NAI API Token"
            ),
            "base_url": ConfigField(
                type=str,
                default="https://std.loliyc.com",
                description="NAI API 地址"
            ),
            "trigger_probability": ConfigField(
                type=float,
                default=0.3,
                description="NAI生图兜底概率（0-1），仅当模型未输出配图建议时使用"
            ),
            "ssl_verify": ConfigField(
                type=bool,
                default=True,
                description="是否验证SSL证书"
            ),
            "model": ConfigField(
                type=str,
                default="nai-diffusion-4-5-full",
                description="NAI模型名称"
            ),
            "prompt_suffix": ConfigField(
                type=str,
                default="masterpiece, best quality",
                description="正面提示词后缀"
            ),
            "negative_prompt": ConfigField(
                type=str,
                default="",
                description="负面提示词"
            )
        },
        "admin": {
            "admin_users": ConfigField(
                type=list,
                default=[],
                description="管理员用户ID列表"
            ),
            "default_admin_mode": ConfigField(
                type=bool,
                default=False,
                description="默认是否启用管理员模式"
            )
        },
        "logging": {
            "level": ConfigField(
                type=str,
                default="INFO",
                description="日志级别",
                choices=["DEBUG", "INFO", "WARNING", "ERROR"]
            )
        }
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的组件列表"""
        components = []

        # 命令
        components.append((ScheduleGenerateCommand.get_command_info(), ScheduleGenerateCommand))
        components.append((ScheduleViewCommand.get_command_info(), ScheduleViewCommand))
        components.append((SceneCommand.get_command_info(), SceneCommand))
        components.append((PresetCommand.get_command_info(), PresetCommand))
        components.append((HelpCommand.get_command_info(), HelpCommand))
        components.append((SceneAdminCommand.get_command_info(), SceneAdminCommand))
        components.append((CustomInitCommand.get_command_info(), CustomInitCommand))
        components.append((StatusCommand.get_command_info(), StatusCommand))
        components.append((NaiControlCommand.get_command_info(), NaiControlCommand))

        # 事件处理器
        components.append((SceneFormatHandler.get_handler_info(), SceneFormatHandler))
        components.append((DailyScheduleEventHandler.get_handler_info(), DailyScheduleEventHandler))

        return components
