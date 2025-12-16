"""
自定义场景初始化命令 - 根据用户描述生成场景
"""
import json
import re
from typing import Tuple, Optional
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.component_types import CommandInfo, ComponentType
from src.chat.message_receive.message import MessageRecv
from src.config.config import global_config
from src.common.logger import get_logger
from ..core.scene_db import SceneDB
from ..core.preset_manager import PresetManager
from ..core.llm_client import LLMClientFactory

logger = get_logger("custom_init_command")


class CustomInitCommand(BaseCommand):
    """自定义场景初始化命令 - 根据用户描述生成初始场景"""

    command_name = "scene_custom_init"
    command_description = "根据用户描述生成自定义场景"
    command_pattern = r"^/sc\s+init\s+(?P<description>.+)$"

    def __init__(self, message: MessageRecv, plugin_config: Optional[dict] = None):
        super().__init__(message, plugin_config)
        self.db = SceneDB()
        self.preset_manager = PresetManager(self.db)
        # 使用 reply 模型生成场景（根据配置选择自定义 API 或 MaiBot 原生）
        self.llm = LLMClientFactory.create_reply_client(self.get_config)

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        """执行自定义初始化命令"""
        stream_id = self.message.chat_stream.stream_id
        user_id = str(self.message.message_info.user_info.user_id)
        session_id = self._build_session_id(stream_id, user_id)

        # 权限检查
        from .admin_command import SceneAdminCommand
        message_info = self.message.message_info
        platform = getattr(message_info, "platform", "")
        group_info = getattr(message_info, "group_info", None)
        chat_id = group_info.group_id if group_info and getattr(group_info, "group_id", None) else user_id

        if not SceneAdminCommand.check_user_permission(platform, chat_id, user_id, self.get_config):
            await self.send_text("❌ 当前会话已开启管理员模式，仅管理员可使用")
            return False, "没有权限", 2

        description = self.matched_groups.get("description", "").strip()

        if not description:
            reply = "❌ 请提供场景描述"
            await self.send_text(reply)
            return False, reply, 2

        logger.info(f"[CustomInit] 自定义初始化场景: {description}")

        # 发送处理中反馈
        await self.send_text("🎬 正在根据您的描述生成场景...")

        try:
            # 构建prompt
            bot_name = global_config.bot.nickname
            bot_personality = getattr(global_config.personality, "personality", "")

            prompt = f"""【你的身份】
你是 {bot_name}

【性格特质与身份】
{bot_personality}

【用户要求】
用户希望你处于以下场景：
{description}

【初始化任务】
请根据用户的描述，生成一个符合要求的场景状态。

1. 地点：从描述中提取或推测地点（3-8字）
2. 着装：根据场景推测合适的着装（10-20字）
3. 场景：用第一人称（"我"）创作小说化的场景描写（150-300字）

【场景描写要求】
- 环境描写：描绘周围的场景、氛围、光线、声音等
- 动作描写：细腻刻画你当前的动作、表情、姿态
- 心理描写：适当融入内心想法、感受（可选）
- 合理分段：使用换行符分段，让叙述自然流畅

【输出格式】
严格按照JSON格式输出：

```json
{{
  "地点": "...",
  "着装": "...",
  "场景": "第一段场景描写\\n\\n第二段场景描写\\n\\n第三段场景描写（如有）"
}}
```

注意：场景内容中使用 \\n\\n 表示段落换行"""

            # 应用完整预设
            enhanced_prompt = self.preset_manager.build_full_preset_prompt(
                base_prompt=prompt,
                include_main=True,
                include_guidelines=True,
                include_style=True
            )

            # 调用LLM
            llm_response, _ = await self.llm.generate_response_async(enhanced_prompt)
            logger.info(f"[CustomInit] LLM返回: {llm_response}")

            # 解析JSON
            scene_data = self._parse_json_response(llm_response)

            if not scene_data:
                reply = "❌ 场景生成失败，请稍后重试"
                await self.send_text(reply)
                return False, reply, 2

            # 保存到数据库（不启用）
            nai_enabled = self.db.get_nai_enabled(session_id)
            self.db.clear_scene_state(session_id)
            self.db.clear_scene_history(session_id)
            self.db.clear_character_status(session_id)
            if nai_enabled:
                self.db.set_nai_enabled(session_id, True)
            self.db.create_scene_state(
                chat_id=session_id,
                location=scene_data["地点"],
                clothing=scene_data["着装"],
                scene_description=scene_data["场景"],
                activity="自定义场景",
                user_id=user_id
            )
            self.db.disable_scene(session_id)

            # 处理换行符
            scene_text = scene_data['场景'].replace('\\n\\n', '\n\n').replace('\\n', '\n')

            reply = f"""✅ 场景已生成（未启用）

📍 地点：{scene_data['地点']}
👗 着装：{scene_data['着装']}

🎬 场景：
{scene_text}

使用 /sc on 启动场景模式"""

            await self.send_text(reply)
            return True, reply, 2

        except Exception as e:
            logger.error(f"[CustomInit] 生成场景时出错: {e}", exc_info=True)
            reply = f"❌ 场景生成失败: {str(e)}"
            await self.send_text(reply)
            return False, reply, 2

    def _build_session_id(self, chat_id: str, user_id: Optional[str]) -> str:
        """构建会话ID"""
        user_part = user_id or "unknown_user"
        return f"{chat_id}:{user_part}"

    def _parse_json_response(self, response: str) -> Optional[dict]:
        """解析LLM返回的JSON"""
        try:
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = response

            data = json.loads(json_str)
            return data

        except json.JSONDecodeError as e:
            logger.error(f"[CustomInit] JSON解析失败: {e}")
            return None
        except Exception as e:
            logger.error(f"[CustomInit] 解析响应时出错: {e}")
            return None

    @classmethod
    def get_command_info(cls) -> CommandInfo:
        """返回命令信息"""
        return CommandInfo(
            name=cls.command_name,
            component_type=ComponentType.COMMAND,
            description=cls.command_description,
            command_pattern=cls.command_pattern
        )
