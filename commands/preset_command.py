"""
预设管理命令
"""
from typing import Tuple, Optional
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.component_types import CommandInfo, ComponentType
from src.chat.message_receive.message import MessageRecv
from src.common.logger import get_logger
from ..core.preset_manager import PresetManager

logger = get_logger("preset_command")


class PresetCommand(BaseCommand):
    """预设管理命令 - /sc preset"""

    command_name = "scene_preset"
    command_description = "预设管理命令，支持导入、列表、应用文风等操作"
    command_pattern = r"^/s(?:cene|c)\s+preset.*$"

    def __init__(self, message: MessageRecv, plugin_config: Optional[dict] = None):
        super().__init__(message, plugin_config)
        self.preset_manager = PresetManager()

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        """执行命令"""
        content = self.message.processed_plain_text.strip()

        logger.info(f"[PresetCommand] 执行命令: {content}")

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

        # 解析子命令
        if "import" in content:
            return await self._handle_import(content)
        elif "list" in content or "ls" in content:
            return await self._handle_list()
        elif "use" in content or "apply" in content:
            return await self._handle_use(content)
        elif "clear" in content or "disable" in content:
            return await self._handle_clear()
        elif "delete" in content or "remove" in content:
            return await self._handle_delete(content)
        elif "status" in content or "current" in content:
            return await self._handle_status()
        else:
            return await self._handle_help()

    async def _handle_import(self, content: str) -> Tuple[bool, Optional[str], int]:
        """处理导入命令"""
        # 提取文件路径
        # 格式: /scene preset import <file>
        parts = content.split()
        if len(parts) < 4:
            reply = "❌ 请指定预设文件路径\n用法: /scene preset import <文件路径>"
            await self.send_text(reply)
            return False, reply, 2

        file_path = " ".join(parts[3:])  # 支持带空格的路径

        logger.info(f"[PresetCommand] 导入预设: {file_path}")

        # 执行导入
        success = self.preset_manager.import_preset_from_file(file_path)

        if success:
            # 获取预设名称
            from pathlib import Path
            preset_name = Path(file_path).stem

            # 列出可用文风
            styles = self.preset_manager.get_available_styles(preset_name)

            reply = f"✅ 预设导入成功: {preset_name}\n\n"
            if styles:
                reply += f"找到 {len(styles)} 个文风:\n"
                for i, style in enumerate(styles, 1):
                    reply += f"{i}. {style['name']} (ID: {style['identifier']})\n"
                reply += "\n使用 /scene preset use <ID> 应用文风"
            else:
                reply += "注意: 未找到可用的文风"

            await self.send_text(reply)
            return True, reply, 2
        else:
            reply = f"❌ 预设导入失败: {file_path}\n请检查文件路径和格式"
            await self.send_text(reply)
            return False, reply, 2

    async def _handle_list(self) -> Tuple[bool, Optional[str], int]:
        """处理列表命令 - 显示完整列表"""
        presets = self.preset_manager.list_presets()

        if not presets:
            reply = "未找到已导入的预设\n使用 /sc preset import <文件> 导入预设"
            await self.send_text(reply)
            return True, reply, 2

        reply = "📚 已导入的预设:\n\n"
        for i, preset in enumerate(presets, 1):
            preset_name = preset["preset_name"]
            imported_at = preset["imported_at"]

            # 获取文风列表
            styles = self.preset_manager.get_available_styles(preset_name)

            reply += f"{i}. {preset_name}\n"
            reply += f"   导入: {imported_at}\n"
            reply += f"   文风数: {len(styles)}\n"

            if styles:
                # 显示所有文风（不限制数量）
                for j, style in enumerate(styles, 1):
                    style_id_short = style['identifier'][:8] + "..."
                    reply += f"     {j}. {style['name']} ({style_id_short})\n"

            reply += "\n"

        reply += f"💡 使用 /sc preset use <文风ID> 激活文风"

        await self.send_text(reply)
        return True, reply, 2

    async def _handle_use(self, content: str) -> Tuple[bool, Optional[str], int]:
        """处理应用文风命令"""
        # 格式: /scene preset use <style_identifier>
        # 或: /scene preset use <preset_name> <style_identifier>

        parts = content.split()
        if len(parts) < 4:
            reply = "❌ 请指定文风标识\n用法: /scene preset use <文风ID>"
            await self.send_text(reply)
            return False, reply, 2

        # 获取所有预设
        presets = self.preset_manager.list_presets()
        if not presets:
            reply = "❌ 未找到已导入的预设\n请先使用 /scene preset import 导入预设"
            await self.send_text(reply)
            return False, reply, 2

        # 如果只提供了 style_identifier，在所有预设中查找
        if len(parts) == 4:
            style_identifier = parts[3]

            # 在所有预设中查找该文风
            found_preset = None
            found_style = None

            for preset in presets:
                preset_name = preset["preset_name"]
                styles = self.preset_manager.get_available_styles(preset_name)

                for style in styles:
                    if style["identifier"] == style_identifier:
                        found_preset = preset_name
                        found_style = style
                        break

                if found_preset:
                    break

            if not found_preset:
                reply = f"❌ 未找到文风: {style_identifier}\n使用 /scene preset list 查看可用文风"
                await self.send_text(reply)
                return False, reply, 2

            # 激活文风
            success = self.preset_manager.activate_style(found_preset, style_identifier)

            if success:
                reply = f"✅ 已激活文风: {found_style['name']}\n来自预设: {found_preset}"
                await self.send_text(reply)
                return True, reply, 2
            else:
                reply = "❌ 激活文风失败"
                await self.send_text(reply)
                return False, reply, 2

        # 如果提供了 preset_name 和 style_identifier
        elif len(parts) >= 5:
            preset_name = parts[3]
            style_identifier = parts[4]

            success = self.preset_manager.activate_style(preset_name, style_identifier)

            if success:
                reply = f"✅ 已激活文风: {style_identifier}\n来自预设: {preset_name}"
                await self.send_text(reply)
                return True, reply, 2
            else:
                reply = "❌ 激活文风失败\n请检查预设名称和文风ID"
                await self.send_text(reply)
                return False, reply, 2

    async def _handle_clear(self) -> Tuple[bool, Optional[str], int]:
        """处理清除文风命令"""
        success = self.preset_manager.deactivate_style()

        if success:
            reply = "✅ 已清除激活的文风\n场景对话将使用默认风格"
            await self.send_text(reply)
            return True, reply, 2
        else:
            reply = "❌ 清除文风失败"
            await self.send_text(reply)
            return False, reply, 2

    async def _handle_delete(self, content: str) -> Tuple[bool, Optional[str], int]:
        """处理删除预设命令"""
        # 格式: /scene preset delete <preset_name>
        parts = content.split()
        if len(parts) < 4:
            reply = "❌ 请指定预设名称\n用法: /scene preset delete <预设名称>"
            await self.send_text(reply)
            return False, reply, 2

        preset_name = " ".join(parts[3:])

        success = self.preset_manager.delete_preset(preset_name)

        if success:
            reply = f"✅ 已删除预设: {preset_name}"
            await self.send_text(reply)
            return True, reply, 2
        else:
            reply = f"❌ 删除预设失败: {preset_name}"
            await self.send_text(reply)
            return False, reply, 2

    async def _handle_status(self) -> Tuple[bool, Optional[str], int]:
        """处理查看状态命令"""
        current_style = self.preset_manager.get_current_style()

        if not current_style:
            reply = "未激活任何文风\n使用 /scene preset use <文风ID> 激活文风"
            await self.send_text(reply)
            return True, reply, 2

        reply = f"""📝 当前激活的文风:

文风名称: {current_style['style_name']}
文风ID: {current_style['style_identifier']}
来自预设: {current_style['preset_name']}
激活时间: {current_style['updated_at']}

使用 /scene preset clear 清除文风"""

        await self.send_text(reply)
        return True, reply, 2

    async def _handle_help(self) -> Tuple[bool, Optional[str], int]:
        """显示帮助信息"""
        reply = """📖 预设管理命令帮助

可用命令:
• /scene preset import <文件>   - 导入预设文件
• /scene preset list            - 列出已导入的预设
• /scene preset use <文风ID>    - 应用文风
• /scene preset status          - 查看当前文风
• /scene preset clear           - 清除文风
• /scene preset delete <预设>   - 删除预设

示例:
  /scene preset import Izumi预设V0.2.json
  /scene preset use 2dbec179-06c2-4d22-80d8-1d0d9649ef72
  /scene preset clear"""

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
