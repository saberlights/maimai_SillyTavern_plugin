"""
文风管理命令 - /sc style
"""
from typing import Tuple, Optional
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.component_types import CommandInfo, ComponentType
from src.chat.message_receive.message import MessageRecv
from src.common.logger import get_logger
from ..core.preset_manager import PresetManager
from ..core.scene_db import SceneDB

logger = get_logger("style_command")


class PresetCommand(BaseCommand):
    """文风管理命令 - /sc style"""

    command_name = "scene_style"
    command_description = "文风管理命令，支持列表、选择、清除文风"
    command_pattern = r"^/s(?:cene|c)\s+(?:style|文风|preset|pov|视角).*$"

    def __init__(self, message: MessageRecv, plugin_config: Optional[dict] = None):
        super().__init__(message, plugin_config)
        self.db = SceneDB()
        self.preset_manager = PresetManager(self.db)

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        """执行命令"""
        content = self.message.processed_plain_text.strip()

        logger.info(f"[StyleCommand] 执行命令: {content}")

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

        # 解析命令类型
        parts = content.split()
        # parts[0] = "/sc", parts[1] = "style"/"pov", parts[2] = 子命令
        command_type = parts[1].lower() if len(parts) > 1 else ""

        # 视角命令
        if command_type in ("pov", "视角"):
            return await self._handle_pov(parts[2:] if len(parts) > 2 else [])

        # 文风命令
        subcommand = parts[2].lower() if len(parts) > 2 else ""

        if subcommand in ("list", "ls", "列表"):
            return await self._handle_list()
        elif subcommand in ("use", "set", "选择"):
            return await self._handle_use(parts[3:] if len(parts) > 3 else [])
        elif subcommand in ("clear", "off", "关闭"):
            return await self._handle_clear()
        elif subcommand in ("status", "当前"):
            return await self._handle_status()
        else:
            # 默认显示当前状态和帮助
            return await self._handle_help()

    async def _handle_list(self) -> Tuple[bool, Optional[str], int]:
        """列出所有文风"""
        styles = self.preset_manager.get_styles()

        if not styles:
            reply = "❌ 未找到可用文风"
            await self.send_text(reply)
            return False, reply, 2

        current = self.preset_manager.get_current_style()
        current_id = current["id"] if current else None

        reply = f"📚 可用文风 ({len(styles)}个)\n\n"

        for i, style in enumerate(styles, 1):
            is_active = "✅ " if style["id"] == current_id else ""
            reply += f"{i}. {is_active}{style['name']}\n"

        reply += f"\n使用 /sc style use <序号或名称> 选择文风"

        await self.send_text(reply)
        return True, reply, 2

    async def _handle_use(self, args: list) -> Tuple[bool, Optional[str], int]:
        """选择文风"""
        if not args:
            reply = "❌ 请指定文风\n用法: /sc style use <序号或名称>\n示例: /sc style use 1\n示例: /sc style use 鲁迅"
            await self.send_text(reply)
            return False, reply, 2

        query = " ".join(args)
        styles = self.preset_manager.get_styles()

        # 尝试按序号查找
        try:
            index = int(query) - 1
            if 0 <= index < len(styles):
                style = styles[index]
                self.preset_manager.activate_style(style["id"])
                reply = f"✅ 已选择文风: {style['name']}"
                await self.send_text(reply)
                return True, reply, 2
        except ValueError:
            pass

        # 按名称查找
        style = self.preset_manager.get_style_by_name(query)
        if style:
            self.preset_manager.activate_style(style["id"])
            reply = f"✅ 已选择文风: {style['name']}"
            await self.send_text(reply)
            return True, reply, 2

        reply = f"❌ 未找到文风: {query}\n使用 /sc style list 查看可用文风"
        await self.send_text(reply)
        return False, reply, 2

    async def _handle_clear(self) -> Tuple[bool, Optional[str], int]:
        """清除文风"""
        self.preset_manager.deactivate_style()
        reply = "✅ 已清除文风，将使用默认风格"
        await self.send_text(reply)
        return True, reply, 2

    async def _handle_status(self) -> Tuple[bool, Optional[str], int]:
        """查看当前文风"""
        current = self.preset_manager.get_current_style()

        if not current:
            reply = "当前未选择文风\n使用 /sc style list 查看可用文风"
            await self.send_text(reply)
            return True, reply, 2

        reply = f"📝 当前文风: {current['name']}\n激活时间: {current.get('activated_at', '未知')}"
        await self.send_text(reply)
        return True, reply, 2

    async def _handle_pov(self, args: list) -> Tuple[bool, Optional[str], int]:
        """处理视角切换命令"""
        current_pov = self.db.get_perspective()

        if not args:
            # 显示当前视角
            reply = f"📷 当前视角: {current_pov}\n\n切换命令:\n• /sc pov 1  - 第一人称\n• /sc pov 3  - 第三人称"
            await self.send_text(reply)
            return True, reply, 2

        arg = args[0].lower()

        if arg in ("1", "first", "第一人称", "第一"):
            self.db.set_perspective("第一人称")
            reply = "✅ 已切换为第一人称视角"
            await self.send_text(reply)
            return True, reply, 2
        elif arg in ("3", "third", "第三人称", "第三"):
            self.db.set_perspective("第三人称")
            reply = "✅ 已切换为第三人称视角"
            await self.send_text(reply)
            return True, reply, 2
        else:
            reply = f"❌ 无效的视角: {arg}\n\n可用选项:\n• 1 / first / 第一人称\n• 3 / third / 第三人称"
            await self.send_text(reply)
            return False, reply, 2

    async def _handle_help(self) -> Tuple[bool, Optional[str], int]:
        """显示帮助"""
        current = self.preset_manager.get_current_style()
        current_pov = self.db.get_perspective()
        status = f"当前文风: {current['name']}" if current else "当前未选择文风"

        reply = f"""📖 文风与视角管理

{status}
当前视角: {current_pov}

【文风命令】
• /sc style list    - 列出所有文风
• /sc style use <n> - 选择文风（序号或名称）
• /sc style clear   - 清除文风
• /sc style status  - 查看当前文风

【视角命令】
• /sc pov      - 查看当前视角
• /sc pov 1    - 切换为第一人称
• /sc pov 3    - 切换为第三人称

示例:
  /sc style use 鲁迅
  /sc pov 1"""

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
