"""
状态栏显示命令
"""
import json
from typing import Tuple, Optional
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.component_types import CommandInfo, ComponentType
from src.chat.message_receive.message import MessageRecv
from src.common.logger import get_logger
from ..core.scene_db import SceneDB

logger = get_logger("status_command")


class StatusCommand(BaseCommand):
    """状态栏显示命令 - /sc status"""

    command_name = "scene_status"
    command_description = "显示角色状态栏"
    command_pattern = r"^/s(?:cene|c)\s+status.*$"

    def __init__(self, message: MessageRecv, plugin_config: Optional[dict] = None):
        super().__init__(message, plugin_config)
        self.db = SceneDB()

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        """执行命令"""
        stream_id = self.message.chat_stream.stream_id
        user_id = str(self.message.message_info.user_info.user_id)
        session_id = f"{stream_id}:{user_id}"

        logger.info(f"[StatusCommand] 查看状态: {session_id}")

        # 权限检查
        from .admin_command import SceneAdminCommand
        message_info = self.message.message_info
        platform = getattr(message_info, "platform", "")
        group_info = getattr(message_info, "group_info", None)
        chat_id = group_info.group_id if group_info and getattr(group_info, "group_id", None) else user_id

        if not SceneAdminCommand.check_user_permission(platform, chat_id, user_id, self.get_config):
            await self.send_text("❌ 当前会话已开启管理员模式，仅管理员可使用")
            return False, "没有权限", 2

        # 获取角色状态
        status = self.db.get_character_status(session_id)

        if not status:
            reply = "未找到角色状态\n请先使用 /sc on 启动场景模式"
            await self.send_text(reply)
            return True, reply, 2

        # 格式化状态栏（精简易读）
        reply = self._format_status(status)
        await self.send_text(reply)
        return True, reply, 2

    def _format_status(self, status: dict) -> str:
        """格式化状态栏输出（精简版）"""
        # 解析 JSON 字段
        fetishes = self._safe_load_json(status.get('fetishes', '{}'))
        inventory = self._safe_load_json(status.get('inventory', '[]'))
        semen_sources = self._safe_load_json(status.get('semen_sources', '[]'))
        vaginal_foreign = self._safe_load_json(status.get('vaginal_foreign', '[]'))
        permanent_mods = self._safe_load_json(status.get('permanent_mods', '{}'))
        body_condition = self._safe_load_json(status.get('body_condition', '{}'))

        lines = ["━━━ 角色状态 ━━━\n"]

        # 身体与生理
        lines.append("【身体状态】")
        lines.append(f"  生理: {status.get('physiological_state', '呼吸平稳')}")
        lines.append(f"  阴道: {status.get('vaginal_state', '放松')} | 湿润度: {status.get('vaginal_wetness', '正常')}")

        # 阴道容量（如果不是默认值）
        vaginal_capacity = status.get('vaginal_capacity', 100)
        if vaginal_capacity != 100:
            lines.append(f"  阴道容量: {vaginal_capacity}")

        # 后穴开发度（如果大于0）
        anal_dev = status.get('anal_development', 0)
        if anal_dev > 0:
            lines.append(f"  后穴开发度: {anal_dev}/100")

        pregnancy = status.get('pregnancy_status', '未受孕')
        if pregnancy == '受孕中':
            source = status.get('pregnancy_source', '未知')
            counter = status.get('pregnancy_counter', 0)
            lines.append(f"  子宫: {pregnancy} ({source}, {counter}天)")
        else:
            lines.append(f"  子宫: {pregnancy}")

        semen_vol = status.get('semen_volume', 0)
        if semen_vol > 0:
            sources_str = ", ".join(semen_sources) if semen_sources else "未知"
            lines.append(f"  体内精液: {semen_vol}ml (来源: {sources_str})")

        # 阴道内异物（如果有）
        if vaginal_foreign:
            foreign_str = ", ".join(vaginal_foreign)
            lines.append(f"  阴道内异物: {foreign_str}")

        # 身体部位状况（如果有）
        if body_condition:
            lines.append("\n【部位状况】")
            for part, condition in body_condition.items():
                lines.append(f"  {part}: {condition}")

        # 快感与成长
        lines.append("\n【快感状态】")
        pleasure = status.get('pleasure_value', 0)
        threshold = status.get('pleasure_threshold', 100)
        corruption = status.get('corruption_level', 0)
        lines.append(f"  快感值: {pleasure}/{threshold}")
        lines.append(f"  污染度: {corruption}")

        # 永久性改造（如果有）
        if permanent_mods:
            lines.append("\n【永久改造】")
            for mod_type, description in permanent_mods.items():
                lines.append(f"  {mod_type}: {description}")

        # 性癖（如果有）
        if fetishes:
            lines.append("\n【性癖】")
            for name, data in fetishes.items():
                if isinstance(data, dict):
                    exp = data.get('经验', 0)
                    level = data.get('等级', 0)
                    lines.append(f"  {name}: Lv{level} ({exp}exp)")

        # 道具栏（如果有）
        if inventory:
            lines.append("\n【道具】")
            for item in inventory[:5]:  # 最多显示5个
                lines.append(f"  • {item}")
            if len(inventory) > 5:
                lines.append(f"  ... 还有 {len(inventory) - 5} 个道具")

        lines.append("\n━━━━━━━━━━━━━━")
        return "\n".join(lines)

    def _safe_load_json(self, text: str) -> any:
        """安全加载JSON字符串"""
        try:
            return json.loads(text) if text else {}
        except json.JSONDecodeError:
            logger.warning(f"JSON解析失败: {text}")
            return {} if text.startswith('{') else []

    @classmethod
    def get_command_info(cls) -> CommandInfo:
        """返回命令信息"""
        return CommandInfo(
            name=cls.command_name,
            component_type=ComponentType.COMMAND,
            description=cls.command_description,
            command_pattern=cls.command_pattern
        )
