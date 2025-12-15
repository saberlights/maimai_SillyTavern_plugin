"""
帮助命令 - 显示所有可用命令及说明
"""
from typing import Tuple, Optional
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.component_types import CommandInfo, ComponentType
from src.chat.message_receive.message import MessageRecv
from src.common.logger import get_logger

logger = get_logger("help_command")


class HelpCommand(BaseCommand):
    """帮助命令 - 显示场景格式插件的所有可用命令"""

    command_name = "scene_help"
    command_description = "显示场景格式插件的所有可用命令及使用说明"
    command_pattern = r"^/s(?:cene|c)\s+help.*$"

    def __init__(self, message: MessageRecv, plugin_config: Optional[dict] = None):
        super().__init__(message, plugin_config)

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        """执行命令"""
        logger.info("[HelpCommand] 显示帮助信息")

        reply = """📖 场景插件帮助 (可用 /sc 或 /scene)

【场景控制】
/sc on           - 启动场景模式（有历史则续接）
/sc off          - 关闭场景模式（保留状态）
/sc init         - 重新初始化（根据日程）
/sc init <描述>  - 自定义初始化场景
/sc              - 查看当前场景状态

【状态管理】
/sc status       - 查看角色状态栏

【NAI 生图】
/sc nai on       - 开启场景配图（概率触发）
/sc nai off      - 关闭场景配图
/sc nai          - 查看开关状态

【日程管理】
/sc 日程         - 生成今日日程
/sc schedule     - 同上

【预设管理】
/sc preset import <文件>  - 导入预设
/sc preset list           - 列出所有预设和文风
/sc preset use <文风ID>   - 激活文风
/sc preset status         - 查看当前文风
/sc preset clear          - 清除文风

【管理员】
/sc admin on/off  - 开启/关闭管理员模式

【快速开始】
1. /sc 日程              # 生成日程
2. /sc on               # 启动场景
3. /sc nai on           # 开启配图（可选）
4. /sc nai off          # 关闭配图

详细文档: README.md"""

        await self.send_text(reply)
        return True, reply, 2

    @classmethod
    def get_command_info(cls) -> CommandInfo:
        """返回命令信息"""
        return CommandInfo(
            name=cls.command_name,
            component_type=ComponentType.COMMAND,
            description=cls.command_description,
            command_pattern=cls.command_pattern
        )
