"""
NAI 生图控制命令 - /sc nai on/off
仅在场景模式下可用
"""
from typing import Tuple, Optional
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.component_types import CommandInfo, ComponentType
from src.chat.message_receive.message import MessageRecv
from src.common.logger import get_logger
from ..core.scene_db import SceneDB

logger = get_logger("nai_command")


class NaiControlCommand(BaseCommand):
    """NAI 生图控制命令 - /sc nai on/off"""

    command_name = "scene_nai"
    command_description = "控制场景模式下的 NAI 生图开关"
    command_pattern = r"^/s(?:cene|c)\s+nai\s*.*$"

    def __init__(self, message: MessageRecv, plugin_config: Optional[dict] = None):
        super().__init__(message, plugin_config)
        self.db = SceneDB()

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        """执行命令"""
        content = self.message.processed_plain_text.strip().lower()
        stream_id = self.message.chat_stream.stream_id
        user_id = str(self.message.message_info.user_info.user_id)
        session_id = self._build_session_id(stream_id, user_id)

        logger.info(f"[NaiCommand] 执行命令: {content}, session_id={session_id}")

        # 解析子命令
        if "on" in content:
            return await self._handle_nai_on(session_id)
        elif "off" in content:
            return await self._handle_nai_off(session_id)
        else:
            return await self._handle_nai_status(session_id)

    async def _handle_nai_on(self, session_id: str) -> Tuple[bool, Optional[str], int]:
        """开启 NAI 生图"""
        # 检查 API Key 是否配置
        api_key = self.get_config("nai.api_key", "")
        if not api_key:
            reply = "❌ NAI API Token 未配置，请在 config.toml 中设置 nai.api_key"
            await self.send_text(reply)
            return False, reply, 2

        self.db.set_nai_enabled(session_id, True)
        reply = "✅ NAI 生图已开启\n场景回复时将自动生成配图"
        await self.send_text(reply)
        return True, reply, 2

    async def _handle_nai_off(self, session_id: str) -> Tuple[bool, Optional[str], int]:
        """关闭 NAI 生图"""
        self.db.set_nai_enabled(session_id, False)
        reply = "❌ NAI 生图已关闭"
        await self.send_text(reply)
        return True, reply, 2

    async def _handle_nai_status(self, session_id: str) -> Tuple[bool, Optional[str], int]:
        """查看 NAI 生图状态"""
        enabled = self.db.get_nai_enabled(session_id)
        status = "开启" if enabled else "关闭"

        reply = f"""🎨 NAI 生图状态: {status}

命令:
• /sc nai on  - 开启生图
• /sc nai off - 关闭生图"""

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
