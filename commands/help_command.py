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

        # 权限检查
        from .admin_command import SceneAdminCommand
        message_info = self.message.message_info
        user_id = str(message_info.user_info.user_id)
        platform = getattr(message_info, "platform", "")
        group_info = getattr(message_info, "group_info", None)
        chat_id = group_info.group_id if group_info and getattr(group_info, "group_id", None) else user_id

        if not SceneAdminCommand.check_user_permission(platform, chat_id, user_id, self.get_config):
            await self.send_text("❌ 当前会话已开启管理员模式，仅管理员可使用")
            return False, "没有权限", 2

        reply = """📖 场景插件帮助 (可用 /sc 或 /scene)

【场景控制】
/sc on           - 启动场景模式（有历史则续接）
/sc off          - 关闭场景模式（保留状态）
/sc init         - 重新初始化（根据日程）
/sc init <描述>  - 自定义初始化场景

【状态管理】
/sc status         - 查看角色状态栏
/sc status history - 查看状态变化历史
/sc status reset   - 重置角色状态到初始值

【NAI 生图】
/sc nai on/off   - 开启/关闭场景配图
/sc nai          - 查看开关状态
/sc nsfw on/off  - 开启/关闭 NSFW 加强

【日程管理】
/sc 日程/schedule  - 生成今日日程
/sc schedule view  - 查看当前日程
（日程每天自动生成，默认凌晨5点）

【文风管理】
/sc style list      - 列出所有文风
/sc style use <n>   - 选择文风（序号或名称）
/sc style clear     - 清除文风
/sc pov 1/3         - 切换第一/三人称视角

【管理员】
/sc admin on/off  - 开启/关闭管理员模式

【快速开始】
1. /sc 日程         # 生成日程
2. /sc style use 1  # 选择文风（可选）
3. /sc on          # 启动场景
4. /sc nai on      # 开启配图（可选）

提示: 启动场景后，@bot 或私聊即可进行场景对话"""

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
