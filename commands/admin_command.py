"""
场景格式插件管理员权限控制命令
"""
from typing import Tuple, Optional
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.component_types import CommandInfo, ComponentType
from src.chat.message_receive.message import MessageRecv
from src.common.logger import get_logger

logger = get_logger("scene_admin_command")


class SceneAdminCommand(BaseCommand):
    """场景管理员模式控制命令"""

    # 类级别的管理员模式状态
    _admin_mode_enabled = {}

    command_name = "scene_admin"
    command_description = "场景管理员模式控制：/sc admin <on|off>"
    command_pattern = r"^/sc\s+admin\s+(?P<action>on|off)$"

    def __init__(self, message: MessageRecv, plugin_config: Optional[dict] = None):
        super().__init__(message, plugin_config)

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        """执行管理员模式控制命令"""
        action = self.matched_groups.get("action", "").strip()

        # 获取会话信息
        if not self.message or not getattr(self.message, "message_info", None):
            await self.send_text("❌ 无法获取会话信息")
            return False, "无法获取会话信息", 2

        message_info = self.message.message_info
        platform = getattr(message_info, "platform", "")
        group_info = getattr(message_info, "group_info", None)
        user_info = getattr(message_info, "user_info", None)

        if not user_info:
            await self.send_text("❌ 无法获取用户信息")
            return False, "无法获取用户信息", 2

        # 确定会话类型
        if group_info and getattr(group_info, "group_id", None):
            chat_id = group_info.group_id
            chat_type = "群聊"
        else:
            chat_id = user_info.user_id
            chat_type = "私聊"

        current_chat_key = f"{platform}:{chat_id}"
        user_id = str(user_info.user_id)

        # 权限检查
        if not self._check_admin_permission(user_id):
            await self.send_text("❌ 只有管理员可以开启/关闭管理员模式")
            return False, "没有管理员权限", 2

        # 执行操作
        if action == "on":
            self._admin_mode_enabled[current_chat_key] = True
            await self.send_text(
                f"✅ 已在{chat_type}中开启场景管理员模式\n"
                f"🔒 现在所有场景命令仅管理员可使用\n"
                f"💡 使用 /sc admin off 可关闭"
            )
            logger.info(f"[SceneAdmin] {chat_type} {current_chat_key} 已开启管理员模式")
            return True, "管理员模式已开启", 2

        elif action == "off":
            self._admin_mode_enabled[current_chat_key] = False
            await self.send_text(
                f"✅ 已在{chat_type}中关闭场景管理员模式\n"
                f"🔓 现在所有人都可使用场景命令"
            )
            logger.info(f"[SceneAdmin] {chat_type} {current_chat_key} 已关闭管理员模式")
            return True, "管理员模式已关闭", 2

        return False, "未知操作", 2

    def _check_admin_permission(self, user_id: str) -> bool:
        """检查当前用户是否是管理员"""
        try:
            admin_users = self.get_config("admin.admin_users", [])
            if not admin_users:
                logger.warning("[SceneAdmin] 未配置管理员列表，允许所有人使用管理命令")
                return True

            # 确保 user_id 转换为字符串进行比较
            is_admin = str(user_id) in [str(uid) for uid in admin_users]
            logger.debug(f"[SceneAdmin] 用户 {user_id} 管理员检查结果: {is_admin}")
            return is_admin
        except Exception as e:
            logger.error(f"[SceneAdmin] 检查管理员权限时出错: {e}", exc_info=True)
            return False

    @classmethod
    def is_admin_mode_enabled(cls, platform: str, chat_id: str, get_config_func) -> bool:
        """
        静态方法：检查指定会话是否启用了管理员模式

        Args:
            platform: 平台标识
            chat_id: 会话ID
            get_config_func: 获取配置的函数

        Returns:
            bool: 是否启用管理员模式
        """
        current_chat_key = f"{platform}:{chat_id}"

        # 检查运行时覆盖
        if current_chat_key in cls._admin_mode_enabled:
            return cls._admin_mode_enabled[current_chat_key]

        # 检查默认配置
        return get_config_func("admin.default_admin_mode", False)

    @classmethod
    def check_user_permission(cls, platform: str, chat_id: str, user_id: str, get_config_func) -> bool:
        """
        静态方法：检查用户是否有权限使用场景命令

        Args:
            platform: 平台标识
            chat_id: 会话ID
            user_id: 用户ID
            get_config_func: 获取配置的函数

        Returns:
            bool: 是否有权限
        """
        # 如果管理员模式未开启，所有人都有权限
        if not cls.is_admin_mode_enabled(platform, chat_id, get_config_func):
            return True

        # 管理员模式已开启，检查是否是管理员
        admin_users = get_config_func("admin.admin_users", [])
        return str(user_id) in [str(uid) for uid in admin_users]

    @classmethod
    def get_command_info(cls) -> CommandInfo:
        """返回命令信息"""
        return CommandInfo(
            name=cls.command_name,
            component_type=ComponentType.COMMAND,
            description=cls.command_description,
            command_pattern=cls.command_pattern
        )
