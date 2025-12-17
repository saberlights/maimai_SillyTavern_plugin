"""
NSFW 开关控制命令 - /sc nsfw on/off
控制提示词中是否包含 NSFW 加强规则
"""
from typing import Tuple, Optional
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.component_types import CommandInfo, ComponentType
from src.chat.message_receive.message import MessageRecv
from src.common.logger import get_logger
from ..core.scene_db import SceneDB

logger = get_logger("nsfw_command")


class NsfwControlCommand(BaseCommand):
    """NSFW 开关控制命令 - /sc nsfw on/off"""

    command_name = "scene_nsfw"
    command_description = "控制场景模式下的 NSFW 加强开关"
    command_pattern = r"^/s(?:cene|c)\s+nsfw\s*.*$"

    def __init__(self, message: MessageRecv, plugin_config: Optional[dict] = None):
        super().__init__(message, plugin_config)
        self.db = SceneDB()

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        """执行命令"""
        content = self.message.processed_plain_text.strip().lower()
        stream_id = self.message.chat_stream.stream_id
        user_id = str(self.message.message_info.user_info.user_id)
        session_id = self._build_session_id(stream_id, user_id)

        logger.info(f"[NsfwCommand] 执行命令: {content}, session_id={session_id}")

        # 权限检查
        from .admin_command import SceneAdminCommand
        message_info = self.message.message_info
        platform = getattr(message_info, "platform", "")
        group_info = getattr(message_info, "group_info", None)
        chat_id = group_info.group_id if group_info and getattr(group_info, "group_id", None) else user_id

        if not SceneAdminCommand.check_user_permission(platform, chat_id, user_id, self.get_config):
            await self.send_text("❌ 当前会话已开启管理员模式，仅管理员可使用")
            return False, "没有权限", 2

        # 解析子命令
        if "on" in content:
            return await self._handle_nsfw_on(session_id)
        elif "off" in content:
            return await self._handle_nsfw_off(session_id)
        else:
            return await self._handle_nsfw_status(session_id)

    async def _handle_nsfw_on(self, session_id: str) -> Tuple[bool, Optional[str], int]:
        """开启 NSFW 加强"""
        self.db.set_nsfw_enabled(session_id, True)
        reply = "🔞 NSFW 加强已开启\n提示词将包含 NSFW 相关规则"
        await self.send_text(reply)
        return True, reply, 2

    async def _handle_nsfw_off(self, session_id: str) -> Tuple[bool, Optional[str], int]:
        """关闭 NSFW 加强"""
        self.db.set_nsfw_enabled(session_id, False)
        reply = "✅ NSFW 加强已关闭"
        await self.send_text(reply)
        return True, reply, 2

    async def _handle_nsfw_status(self, session_id: str) -> Tuple[bool, Optional[str], int]:
        """查看 NSFW 开关状态"""
        enabled = self.db.get_nsfw_enabled(session_id)
        status = "开启" if enabled else "关闭"

        reply = f"""🔞 NSFW 加强状态: {status}

命令:
• /sc nsfw on  - 开启 NSFW 加强
• /sc nsfw off - 关闭 NSFW 加强"""

        await self.send_text(reply)
        return True, reply, 2

    def _build_session_id(self, chat_id: str, user_id: Optional[str]) -> str:
        """构建会话ID"""
        user_part = user_id or "unknown_user"
        return f"{chat_id}:{user_part}"

    @classmethod
    def get_command_info(cls) -> CommandInfo:
        """返回命令信息"""
        return CommandInfo(
            name=cls.command_name,
            component_type=ComponentType.COMMAND,
            description=cls.command_description,
            command_pattern=cls.command_pattern
        )
