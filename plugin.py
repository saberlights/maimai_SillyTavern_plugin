"""
场景格式插件主类
"""
from typing import List, Tuple, Type

from src.plugin_system.base.base_plugin import BasePlugin
from src.plugin_system.base.component_types import ComponentInfo
from src.plugin_system import register_plugin
from src.plugin_system.base.config_types import ConfigField

from .commands.schedule_command import ScheduleGenerateCommand
from .commands.scene_command import SceneCommand
from .commands.preset_command import PresetCommand
from .commands.help_command import HelpCommand
from .commands.admin_command import SceneAdminCommand
from .commands.custom_init_command import CustomInitCommand
from .commands.status_command import StatusCommand
from .commands.nai_command import NaiControlCommand
from .handlers.scene_handler import SceneFormatHandler


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
                default="1.0.0",
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
            "planner_context_messages": ConfigField(
                type=int,
                default=1,
                description="Planner模型使用的历史上下文条数（状态判断用）"
            ),
            "reply_context_messages": ConfigField(
                type=int,
                default=10,
                description="Reply模型使用的历史上下文条数（场景生成用）"
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
        components.append((SceneCommand.get_command_info(), SceneCommand))
        components.append((PresetCommand.get_command_info(), PresetCommand))
        components.append((HelpCommand.get_command_info(), HelpCommand))
        components.append((SceneAdminCommand.get_command_info(), SceneAdminCommand))
        components.append((CustomInitCommand.get_command_info(), CustomInitCommand))
        components.append((StatusCommand.get_command_info(), StatusCommand))
        components.append((NaiControlCommand.get_command_info(), NaiControlCommand))

        # 事件处理器
        components.append((SceneFormatHandler.get_handler_info(), SceneFormatHandler))

        return components
