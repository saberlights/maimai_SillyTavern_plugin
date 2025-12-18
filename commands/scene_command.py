"""
场景模式命令
"""
import json
import re
from datetime import datetime
from typing import Tuple, Optional, Dict, Any
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.component_types import CommandInfo, ComponentType
from src.chat.message_receive.message import MessageRecv
from src.config.config import global_config
from src.common.logger import get_logger
from ..core.scene_db import SceneDB
from ..core.preset_manager import PresetManager
from ..core.llm_client import LLMClientFactory

logger = get_logger("scene_command")


class SceneCommand(BaseCommand):
    """场景模式命令 - /scene on/off/init"""

    command_name = "scene"
    command_description = "场景模式控制命令，支持on/off/init"
    # 支持 /sc 和 /scene，避免和其他子命令冲突
    command_pattern = (
        r"^/s(?:cene|c)"
        r"(?:\s+(?!preset\b|help\b|admin\b|status\b|nai\b|nsfw\b|style\b|文风\b|pov\b|视角\b|schedule\b|日程\b|init\s+.+).*)?$"
    )

    def __init__(self, message: MessageRecv, plugin_config: Optional[dict] = None):
        super().__init__(message, plugin_config)
        self.db = SceneDB()
        self.preset_manager = PresetManager(self.db)
        # 使用 reply 模型生成场景（根据配置选择自定义 API 或 MaiBot 原生）
        self.llm = LLMClientFactory.create_reply_client(self.get_config)

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        """执行命令"""
        stream_id = self.message.chat_stream.stream_id
        user_id = str(self.message.message_info.user_info.user_id)
        session_id = self._build_session_id(stream_id, user_id)
        content = self.message.processed_plain_text.strip()

        logger.info(f"[SceneCommand] 执行命令: {content}, chat_id={stream_id}, user_id={user_id}")

        # 权限检查
        from .admin_command import SceneAdminCommand
        message_info = self.message.message_info
        platform = getattr(message_info, "platform", "")
        group_info = getattr(message_info, "group_info", None)
        chat_id = group_info.group_id if group_info and getattr(group_info, "group_id", None) else user_id

        if not SceneAdminCommand.check_user_permission(platform, chat_id, user_id, self.get_config):
            await self.send_text("❌ 当前会话已开启管理员模式，仅管理员可使用")
            return False, "没有权限", 2

        # 解析子命令 - 使用更严格的匹配
        parts = content.split()
        # 获取子命令（如果有的话）
        subcommand = parts[1].lower() if len(parts) > 1 else ""

        if subcommand == "on":
            result = await self._handle_scene_on(stream_id, session_id, user_id)
            logger.info(f"[SceneCommand] scene on 返回: success={result[0]}, reply_len={len(result[1]) if result[1] else 0}")
            return result
        elif subcommand == "off":
            result = await self._handle_scene_off(stream_id, session_id, user_id)
            logger.info(f"[SceneCommand] scene off 返回: {result}")
            return result
        elif subcommand == "init":
            result = await self._handle_scene_init(stream_id, session_id, user_id)
            logger.info(f"[SceneCommand] scene init 返回: success={result[0]}, reply_len={len(result[1]) if result[1] else 0}")
            return result
        elif subcommand == "":
            # /sc 不带参数，提示使用帮助
            reply = "请指定子命令：\n• /sc on - 启动场景模式\n• /sc off - 关闭场景模式\n• /sc init - 重新初始化场景\n• /sc status - 查看角色状态\n• /sc style - 文风管理\n• /sc nai - NAI 生图控制\n• /sc help - 查看详细帮助"
            await self.send_text(reply)
            return True, reply, 2
        else:
            # 无效的子命令
            reply = f"❌ 未知命令: {subcommand}\n使用 /sc help 查看帮助"
            await self.send_text(reply)
            return False, reply, 2

    def _build_session_id(self, chat_id: str, user_id: Optional[str]) -> str:
        """构建会话ID，使每位用户的状态互相隔离"""
        user_part = user_id or "unknown_user"
        return f"{chat_id}:{user_part}"

    def _reset_session_storage(self, resolved_id: Optional[str], session_id: str):
        """清空旧的场景存档，同时保留 NAI 开关"""
        target_ids = {cid for cid in (resolved_id, session_id) if cid}
        preserve_nai = False
        for cid in target_ids:
            if self.db.get_nai_enabled(cid):
                preserve_nai = True
                break

        for cid in target_ids:
            self.db.clear_scene_state(cid)
            self.db.clear_scene_history(cid)
            self.db.clear_character_status(cid)

        if preserve_nai:
            self.db.set_nai_enabled(session_id, True)

    def _get_existing_state(self, chat_id: str, user_id: str, session_id: Optional[str] = None) -> Tuple[str, Optional[dict]]:
        """查找当前用户已存在的场景状态（兼容旧版本）"""
        target_id = session_id or self._build_session_id(chat_id, user_id)
        state = self.db.get_scene_state(target_id)
        if state:
            return target_id, state

        # 尝试通过 user_id 查找历史会话
        user_state = self.db.get_state_by_user(chat_id, user_id)
        if user_state:
            return user_state["chat_id"], user_state

        # 仅当无法识别用户时才使用旧的 chat_id 状态
        if not user_id:
            legacy_state = self.db.get_scene_state(chat_id)
            if legacy_state:
                return chat_id, legacy_state

        return target_id, None

    async def _send_command_reply(self, text: str, success: bool = True, intercept_level: int = 2) -> Tuple[bool, str, int]:
        """统一发送命令回复"""
        await self.send_text(text)
        return success, text, intercept_level

    async def _handle_scene_on(self, chat_id: str, session_id: str, user_id: str) -> Tuple[bool, str, int]:
        """启动场景模式"""
        resolved_id, state = self._get_existing_state(chat_id, user_id, session_id)

        if state and state.get("enabled") == 1:
            return await self._send_command_reply("⚠️ 场景模式已经启用")

        if state and state.get("location"):
            # 有历史，续接模式
            return await self._resume_scene(resolved_id, user_id, state)
        else:
            # 无历史，初始化模式
            return await self._initialize_scene(session_id, user_id)

    async def _handle_scene_off(self, chat_id: str, session_id: str, user_id: str) -> Tuple[bool, str, int]:
        """关闭场景模式"""
        resolved_id, state = self._get_existing_state(chat_id, user_id, session_id)

        if not state or state.get("enabled") != 1:
            return await self._send_command_reply("⚠️ 场景模式未启用")

        self.db.disable_scene(resolved_id)
        return await self._send_command_reply("❌ 场景模式已关闭（状态已保留，下次 /scene on 可续接）")

    async def _handle_scene_init(self, chat_id: str, session_id: str, user_id: str) -> Tuple[bool, str, int]:
        """重新初始化场景（不启用）"""
        resolved_id, _ = self._get_existing_state(chat_id, user_id, session_id)

        # 发送处理中反馈
        await self.send_text("🎬 正在初始化场景...")

        # 清空旧存档并保留必要标识
        self._reset_session_storage(resolved_id, session_id)

        # 重新初始化（但不启用）
        return await self._initialize_scene_without_enable(session_id, user_id)

    async def _handle_scene_status(self, chat_id: str, session_id: str, user_id: str) -> Tuple[bool, str, int]:
        """查看场景状态"""
        resolved_id, state = self._get_existing_state(chat_id, user_id, session_id)

        if not state or not state.get("location"):
            return await self._send_command_reply("场景模式未启用，使用 /scene on 启动")

        enabled_text = "启用" if state.get("enabled") == 1 else "禁用"

        # 处理场景中的换行符
        scene_text = state['scene_description'].replace('\\n\\n', '\n\n').replace('\\n', '\n')

        reply = f"""📍 当前场景状态：{enabled_text}

📍 地点：{state['location']}
👗 着装：{state['clothing']}

🎬 场景：
{scene_text}

最后活动：{state['last_activity']}
更新时间：{state['last_update_time']}
初始化时间：{state['init_time']}"""

        return await self._send_command_reply(reply)

    def _build_init_prompt(self, activity: Dict[str, Any], current_time: datetime) -> str:
        """构建场景初始化的 prompt（公共方法，避免重复）"""
        bot_name = global_config.bot.nickname
        bot_personality = getattr(global_config.personality, "personality", "")
        bot_reply_style = getattr(global_config.personality, "reply_style", "")

        return f"""【你的身份】
你是 {bot_name}

【性格特质与身份】
{bot_personality}

【回复风格】
{bot_reply_style}

【当前时间和活动】
现在是 {current_time.strftime("%H:%M")}（{self._get_time_period(current_time)}）
根据日程，你现在应该在：{activity['activity']}
活动描述：{activity.get('description', '无')}

【建议状态】
建议地点：{activity.get('location', '未知')}
建议着装：{activity.get('clothing', '普通装扮')}

【初始化任务】
请生成你此时此刻的状态，用小说化的方式呈现。

1. 地点：基于建议地点，可微调（3-8字，简洁）
2. 着装：基于建议着装，可微调（10-20字，简短描述）
3. 场景：用第一人称（"我"）创作一段小说化的场景描写（150-300字）

【场景描写要求】
必须包含以下元素，像写小说一样：

✦ 环境描写：描绘周围的场景、氛围、光线、声音等细节
✦ 动作描写：细腻刻画你当前的动作、表情、姿态
✦ 心理描写：适当融入内心想法、感受（可选）
✦ 合理分段：使用换行符分段，让叙述节奏自然流畅

【写作风格】
- 使用生动的细节描写，避免空洞抽象
- 句式富有变化，长短结合
- 营造画面感和沉浸感
- 避免陈词滥调，力求新鲜自然

【分段建议】
- 第一段：环境/氛围描写
- 第二段：我的动作和状态描写
- （可选第三段）：心理活动或补充描写

【输出格式】
严格按照JSON格式输出：

```json
{{
  "地点": "...",
  "着装": "...",
  "场景": "第一段场景描写\\n\\n第二段场景描写\\n\\n第三段场景描写（如有）"
}}
```

注意：场景内容中使用 \\n\\n 表示段落换行（两个换行符）"""

    async def _do_initialize_scene(self, session_id: str, user_id: str, enable: bool = True) -> Tuple[bool, str, int]:
        """
        执行场景初始化的核心逻辑（公共方法）

        Args:
            session_id: 会话ID
            user_id: 用户ID
            enable: 是否启用场景模式

        Returns:
            (success, reply, intercept_level)
        """
        try:
            # 获取当前活动
            current_time = datetime.now()
            activity = self.db.get_current_activity("global_schedule", current_time)

            if not activity:
                logger.warning(f"[SceneCommand] 未找到全局日程")
                return await self._send_command_reply("❌ 未找到当前日程，请先使用 /sc 日程 生成日程表", success=False)

            # 构建并增强 prompt
            prompt = self._build_init_prompt(activity, current_time)
            enhanced_prompt = self.preset_manager.build_full_preset_prompt(
                base_prompt=prompt,
                include_main=True,
                include_guidelines=True,
                include_style=True
            )
            logger.info(f"初始化Prompt (with full preset):\n{enhanced_prompt}")

            # 调用 LLM
            llm_response, _ = await self.llm.generate_response_async(enhanced_prompt)
            logger.info(f"LLM返回:\n{llm_response}")

            # 解析 JSON
            scene_data = self._parse_json_response(llm_response)
            if not scene_data:
                return await self._send_command_reply("❌ 场景初始化失败，请稍后重试", success=False)

            # 保存到数据库
            self.db.create_scene_state(
                chat_id=session_id,
                location=scene_data["地点"],
                clothing=scene_data["着装"],
                scene_description=scene_data["场景"],
                activity=activity["activity"],
                user_id=user_id
            )

            # 根据参数决定是否启用
            if not enable:
                self.db.disable_scene(session_id)

            # 初始化角色状态
            self.db.init_character_status(session_id)

            # 格式化场景文本
            scene_text = scene_data['场景'].replace('\\n\\n', '\n\n').replace('\\n', '\n')

            # 构建回复
            if enable:
                reply = f"""✅ 场景模式已启用

📍 地点：{scene_data['地点']}
👗 着装：{scene_data['着装']}

🎬 场景：
{scene_text}

现在你可以开始场景对话了~"""
            else:
                reply = f"""✅ 场景已初始化（未启用）

📍 地点：{scene_data['地点']}
👗 着装：{scene_data['着装']}

🎬 场景：
{scene_text}

使用 /scene on 启动场景模式"""

            # 保存初始化场景到历史记录（让后续对话能读取到初始场景）
            self.db.add_scene_history(
                chat_id=session_id,
                location=scene_data["地点"],
                clothing=scene_data["着装"],
                scene_description=scene_data["场景"],
                user_message="[场景初始化]",
                bot_reply=reply
            )
            logger.info(f"[SceneCommand] 初始化场景已保存到历史记录: {session_id}")

            return await self._send_command_reply(reply)

        except Exception as e:
            logger.error(f"初始化场景时出错: {e}", exc_info=True)
            return await self._send_command_reply(f"❌ 场景初始化失败: {str(e)}", success=False)

    async def _initialize_scene_without_enable(self, session_id: str, user_id: str) -> Tuple[bool, str, int]:
        """初始化场景但不启用"""
        return await self._do_initialize_scene(session_id, user_id, enable=False)

    async def _initialize_scene(self, session_id: str, user_id: str) -> Tuple[bool, str, int]:
        """初始化场景并启用"""
        await self.send_text("🎬 正在初始化场景...")
        return await self._do_initialize_scene(session_id, user_id, enable=True)

    async def _resume_scene(self, session_id: str, user_id: str, last_state: dict) -> Tuple[bool, str, int]:
        """续接场景（有历史状态）"""
        try:
            # 使用全局日程
            global_user_id = "global_schedule"

            # 获取当前活动
            current_time = datetime.now()
            activity = self.db.get_current_activity(global_user_id, current_time)

            if not activity:
                logger.warning(f"[SceneCommand] 未找到全局日程（续接模式）")
                return await self._send_command_reply("❌ 未找到当前日程，请先使用 /sc 日程 生成日程表", success=False)

            # 计算时间差（使用多格式解析）
            from ..core.utils import parse_datetime
            last_time = parse_datetime(last_state['last_update_time'])
            if not last_time:
                # 解析失败，使用当前时间作为回退
                last_time = current_time
            time_diff_hours = (current_time - last_time).total_seconds() / 3600

            # 如果时间差很小（<30分钟），直接续接
            if time_diff_hours < 0.5:
                self.db.enable_scene(session_id)

                # 初始化角色状态（如果不存在）
                self.db.init_character_status(session_id)

                # 处理场景中的换行符
                scene_text = last_state['scene_description'].replace('\\n\\n', '\n\n').replace('\\n', '\n')

                reply = f"""✅ 场景模式已启用（续接上次对话）

📍 地点：{last_state['location']}
👗 着装：{last_state['clothing']}

🎬 场景：
{scene_text}

继续对话吧~"""
                return await self._send_command_reply(reply)

            # 时间差较大，需要生成过渡
            await self.send_text("🎬 正在生成场景过渡...")
            bot_name = global_config.bot.nickname
            bot_personality = getattr(global_config.personality, "personality", "")
            bot_reply_style = getattr(global_config.personality, "reply_style", "")

            prompt = f"""【你的身份】
你是 {bot_name}

【性格特质与身份】
{bot_personality}

【回复风格】
{bot_reply_style}

【上次场景状态】（{last_state['last_update_time']}）
地点：{last_state['location']}
着装：{last_state['clothing']}
场景：{last_state['scene_description']}
当时活动：{last_state['last_activity']}

【当前时间和活动】（{current_time.strftime("%Y-%m-%d %H:%M:%S")}）
现在应该在：{activity['activity']}
活动描述：{activity.get('description', '无')}
建议地点：{activity.get('location', '未知')}
建议着装：{activity.get('clothing', '普通装扮')}

【时间跨度】
距离上次对话已经过去了 {time_diff_hours:.1f} 小时

【任务】
请生成一个"续接性"的场景，包含过渡说明和当前状态。

1. 过渡说明：简短回顾中间发生的事（{self._get_transition_length(time_diff_hours)}）
   - 概括时间流逝过程中的主要活动
   - 自然过渡到当前状态

2. 当前场景：用第一人称（"我"）创作小说化的场景描写（150-300字）
   - 地点：基于当前活动判断（可能变化，也可能不变）
   - 着装：基于当前活动判断（可能变化，也可能不变）
   - 场景：包含环境描写、动作描写、心理描写

【场景描写要求】
必须包含以下元素，像写小说一样：

✦ 环境描写：描绘周围的场景、氛围、光线、声音等细节
✦ 动作描写：细腻刻画你当前的动作、表情、姿态
✦ 心理描写：适当融入内心想法、感受（可选）
✦ 合理分段：使用换行符分段，让叙述节奏自然流畅

【写作风格】
- 使用生动的细节描写，避免空洞抽象
- 句式富有变化，长短结合
- 营造画面感和沉浸感
- 避免陈词滥调，力求新鲜自然

【分段建议】
- 第一段：环境/氛围描写
- 第二段：我的动作和状态描写
- （可选第三段）：心理活动或补充描写

【输出格式】
```json
{{
  "过渡说明": "简短概括中间发生的事...",
  "地点": "...",
  "着装": "...",
  "场景": "第一段场景描写\\n\\n第二段场景描写\\n\\n第三段场景描写（如有）"
}}
```

注意：场景内容中使用 \\n\\n 表示段落换行（两个换行符）"""

            # 应用完整预设（包括主提示、指南、禁词表、文风）
            enhanced_prompt = self.preset_manager.build_full_preset_prompt(
                base_prompt=prompt,
                include_main=True,
                include_guidelines=True,
                include_style=True
            )
            logger.info(f"续接Prompt (with full preset):\n{enhanced_prompt}")

            # 调用LLM
            llm_response, _ = await self.llm.generate_response_async(enhanced_prompt)

            logger.info(f"LLM返回:\n{llm_response}")

            # 解析JSON
            scene_data = self._parse_json_response(llm_response)

            if not scene_data:
                # 解析失败，直接续接
                self.db.enable_scene(session_id)

                # 处理场景中的换行符
                scene_text = last_state['scene_description'].replace('\\n\\n', '\n\n').replace('\\n', '\n')

                reply = f"""✅ 场景模式已启用（续接上次对话）

📍 地点：{last_state['location']}
👗 着装：{last_state['clothing']}

🎬 场景：
{scene_text}"""
                return await self._send_command_reply(reply)

            # 更新状态
            self.db.update_scene_state(
                chat_id=session_id,
                location=scene_data["地点"],
                clothing=scene_data["着装"],
                scene_description=scene_data["场景"],
                activity=activity["activity"]
            )
            self.db.enable_scene(session_id)

            # 初始化角色状态（如果不存在）
            self.db.init_character_status(session_id)

            # 处理场景中的换行符
            scene_text = scene_data['场景'].replace('\\n\\n', '\n\n').replace('\\n', '\n')

            # 返回续接结果
            reply = f"""✅ 场景模式已启用（续接上次对话）

【时间已过去 {time_diff_hours:.1f} 小时】
{scene_data['过渡说明']}

📍 地点：{scene_data['地点']}
👗 着装：{scene_data['着装']}

🎬 场景：
{scene_text}"""

            # 保存过渡场景到历史记录（让后续对话能读取到过渡场景）
            self.db.add_scene_history(
                chat_id=session_id,
                location=scene_data["地点"],
                clothing=scene_data["着装"],
                scene_description=scene_data["场景"],
                user_message=f"[场景过渡 - 时间流逝 {time_diff_hours:.1f} 小时]",
                bot_reply=reply
            )
            logger.info(f"[SceneCommand] 过渡场景已保存到历史记录: {session_id}")

            return await self._send_command_reply(reply)

        except Exception as e:
            logger.error(f"续接场景时出错: {e}", exc_info=True)
            return await self._send_command_reply(f"❌ 场景续接失败: {str(e)}", success=False)

    def _get_time_period(self, dt: datetime) -> str:
        """获取时间段描述"""
        hour = dt.hour
        if 5 <= hour < 8:
            return "清晨"
        elif 8 <= hour < 12:
            return "上午"
        elif 12 <= hour < 14:
            return "中午"
        elif 14 <= hour < 18:
            return "下午"
        elif 18 <= hour < 20:
            return "傍晚"
        elif 20 <= hour < 24:
            return "晚上"
        else:
            return "深夜"

    def _get_transition_length(self, hours: float) -> str:
        """根据时间差决定过渡说明的长度"""
        if hours < 1:
            return "1句话"
        elif hours < 4:
            return "1-2句话"
        elif hours < 12:
            return "2-3句话"
        else:
            return "3-5句话，可能跨天"

    def _parse_json_response(self, response: str) -> Optional[dict]:
        """解析LLM返回的JSON"""
        try:
            # 提取```json包裹的内容
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # 尝试直接解析
                json_str = response

            # 解析JSON
            data = json.loads(json_str)
            return data

        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}")
            return None
        except Exception as e:
            logger.error(f"解析响应时出错: {e}")
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
