"""
场景格式事件处理器 - 使用双模型架构
- planner模型：判断状态变化
- reply模型：生成场景描述和回复内容

重构版本：拆分为多个模块，提高可维护性
"""
import random
from typing import Tuple, Optional, Dict, Any

from maim_message import Seg
from src.plugin_system.base.base_events_handler import BaseEventHandler
from src.plugin_system.base.component_types import (
    EventType, EventHandlerInfo, MaiMessages,
    CustomEventHandlerResult, ComponentType
)
from src.config.config import global_config
from src.common.logger import get_logger

# 导入拆分后的模块
from ..core.scene_db import SceneDB
from ..core.preset_manager import PresetManager
from ..core.llm_client import LLMClientFactory
from ..core.nai_client import NaiClient
from ..core.utils import build_session_id, collapse_text, truncate_text
from ..core.state_manager import (
    StateManager, SCENE_TYPE_NORMAL, SCENE_TYPE_ROMANTIC,
    SCENE_TYPE_INTIMATE, SCENE_TYPE_EXPLICIT, SCENE_TYPE_REST
)
from ..core.status_formatter import StatusFormatter
from ..core.context_builder import ContextBuilder
from ..core.scene_generator import SceneGenerator

logger = get_logger("scene_format_handler")

# 场景类型关键词
SCENE_TYPE_KEYWORDS = {
    SCENE_TYPE_ROMANTIC: [
        "拥抱", "亲吻", "牵手", "依偎", "抱住", "亲了一下", "贴近",
        "搂住", "靠在怀里", "抱着", "吻了", "亲嘴", "轻吻", "深吻"
    ],
    SCENE_TYPE_INTIMATE: [
        "抚摸身体", "爱抚", "触碰敏感", "揉捏", "舔舐", "吮吸",
        "摸着胸", "胸部", "乳房", "臀部", "大腿内侧", "敏感部位", "私处",
        "脱衣", "解开", "褪下"
    ],
    SCENE_TYPE_EXPLICIT: [
        "插入", "抽送", "进入体内", "深入", "顶弄", "撞击",
        "射精", "高潮", "抽插", "交合", "做爱", "性交",
        "进入了", "顶到", "射在"
    ],
    SCENE_TYPE_REST: [
        "休息一下", "睡觉", "躺下休息", "放松一下", "恢复体力",
        "睡眠", "小憩", "歇息", "闭眼休息"
    ]
}


class SceneFormatHandler(BaseEventHandler):
    """
    场景格式事件处理器
    在 ON_MESSAGE 事件中拦截，直接生成场景格式回复
    """

    event_type = EventType.ON_MESSAGE
    weight = 50
    intercept_message = True

    def __init__(self):
        super().__init__()
        # 延迟初始化的组件
        self.db: Optional[SceneDB] = None
        self.planner_llm = None
        self.reply_llm = None
        self.preset_manager: Optional[PresetManager] = None
        self.nai_client: Optional[NaiClient] = None

        # 拆分后的模块
        self.state_manager: Optional[StateManager] = None
        self.status_formatter: Optional[StatusFormatter] = None
        self.context_builder: Optional[ContextBuilder] = None
        self.scene_generator: Optional[SceneGenerator] = None


    def _ensure_initialized(self):
        """确保已初始化所有组件"""
        if self.db is None:
            self.db = SceneDB()
        if self.planner_llm is None:
            self.planner_llm = LLMClientFactory.create_planner_client(self.get_config)
        if self.reply_llm is None:
            self.reply_llm = LLMClientFactory.create_reply_client(self.get_config)
        if self.preset_manager is None:
            self.preset_manager = PresetManager(self.db)
        if self.nai_client is None:
            self.nai_client = NaiClient(self.get_config)

        # 初始化拆分后的模块
        if self.state_manager is None:
            self.state_manager = StateManager(self.db)
        if self.status_formatter is None:
            self.status_formatter = StatusFormatter(self.get_config)
        if self.context_builder is None:
            self.context_builder = ContextBuilder(self.db, self.get_config)
        if self.scene_generator is None:
            self.scene_generator = SceneGenerator(
                self.planner_llm, self.reply_llm,
                self.preset_manager, self.status_formatter
            )

    async def cleanup(self):
        """清理资源（关闭 HTTP 客户端等）"""
        try:
            if self.planner_llm and hasattr(self.planner_llm, 'close'):
                await self.planner_llm.close()
            if self.reply_llm and hasattr(self.reply_llm, 'close'):
                await self.reply_llm.close()
            if self.nai_client and hasattr(self.nai_client, 'close'):
                await self.nai_client.close()
            logger.debug("[SceneFormat] 资源清理完成")
        except Exception as e:
            logger.warning(f"[SceneFormat] 资源清理时出错: {e}")

    def __del__(self):
        """析构时尝试清理资源"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.cleanup())
        except Exception:
            pass

    async def execute(
        self, message: Optional[MaiMessages]
    ) -> Tuple[bool, bool, Optional[str], Optional[CustomEventHandlerResult], Optional[MaiMessages]]:
        """
        执行事件处理

        返回值：
        - success: 是否执行成功
        - continue_chain: 是否继续处理链（False会阻止LLM调用）
        - reply: 直接回复内容
        - custom_result: 自定义结果
        - modified_message: 修改后的消息
        """
        if not message:
            return True, True, None, None, None

        # 延迟初始化
        self._ensure_initialized()

        chat_id = message.stream_id
        user_id = str(message.message_base_info.get("user_id") or "")
        session_id, current_state = self._resolve_active_state(chat_id, user_id)

        # 检查是否启用场景模式
        if not current_state or current_state.get("enabled") != 1:
            return True, True, None, None, None

        # 仅在私聊或被明确提及时才截获
        if not self._should_handle_message(message):
            return True, True, None, None, None

        logger.info(f"[SceneFormat] 拦截到场景模式消息: {session_id}")

        try:
            # 获取用户消息
            user_message = message.plain_text or ""

            # 获取角色状态
            self.db.init_character_status(session_id)
            character_status = self.db.get_character_status(session_id) or {}

            # 识别场景类型
            scene_type = self._detect_scene_type(user_message, current_state.get("scene_description", ""))
            logger.info(f"[SceneFormat] 识别场景类型: {scene_type}")

            # 应用时间衰减
            time_decay_applied = self.state_manager.apply_time_decay(session_id, current_state, character_status)
            if time_decay_applied:
                character_status = self.db.get_character_status(session_id) or {}

            # 保存原始状态用于对比
            original_status = dict(character_status)

            # 检查模型调用模式
            model_mode = self.get_config("scene.model_mode", "dual")

            if model_mode == "single":
                # 单模型模式
                reply_context = self.context_builder.build_context_block(session_id, context_type="reply")

                state_decision, scene_reply = await self.scene_generator.single_model_generate(
                    user_message=user_message,
                    current_location=current_state["location"],
                    current_clothing=current_state["clothing"],
                    last_scene=current_state["scene_description"],
                    character_status=character_status,
                    conversation_context=reply_context,
                    scene_type=scene_type
                )

                if not scene_reply:
                    logger.error(f"[SceneFormat] 单模型场景生成失败")
                    await self.send_text(stream_id=chat_id, text="⚠️ 场景生成遇到问题，请稍后重试")
                    return True, False, None, None, None

                # 验证状态决策
                state_decision = self.state_manager.validate_state_decision(state_decision, character_status)
                state_decision = self.state_manager.apply_scene_decay(state_decision, scene_type, character_status)
                state_decision = self.state_manager.ensure_status_consistency(state_decision, character_status, scene_type)

            else:
                # 双模型模式（默认）
                planner_context = self.context_builder.build_context_block(session_id, context_type="planner")
                reply_context = self.context_builder.build_context_block(session_id, context_type="reply")

                # 步骤1：使用planner模型判断状态变化
                state_decision = await self.scene_generator.plan_state_changes(
                    user_message=user_message,
                    current_location=current_state["location"],
                    current_clothing=current_state["clothing"],
                    last_scene=current_state["scene_description"],
                    character_status=character_status,
                    conversation_context=planner_context,
                    scene_type=scene_type
                )

                # 验证和应用衰减
                state_decision = self.state_manager.validate_state_decision(state_decision, character_status)
                state_decision = self.state_manager.apply_scene_decay(state_decision, scene_type, character_status)
                state_decision = self.state_manager.ensure_status_consistency(state_decision, character_status, scene_type)

                # 应用状态更新到临时状态
                updated_character_status = self.state_manager.apply_state_updates_preview(character_status, state_decision)

                # 步骤2：生成场景回复
                scene_reply = await self.scene_generator.generate_scene_reply(
                    user_message=user_message,
                    current_location=current_state["location"],
                    current_clothing=current_state["clothing"],
                    last_scene=current_state["scene_description"],
                    character_status=updated_character_status,
                    state_decision=state_decision,
                    conversation_context=reply_context
                )

                if not scene_reply:
                    logger.error(f"[SceneFormat] 场景回复生成失败")
                    await self.send_text(stream_id=chat_id, text="⚠️ 场景生成遇到问题，请稍后重试")
                    return True, False, None, None, None

            # 步骤3：更新数据库状态
            self._update_scene_state(
                session_id=session_id,
                scene_reply=scene_reply,
                state_decision=state_decision,
                current_state=current_state,
                activity_summary=self._derive_last_activity(user_message, state_decision, scene_reply)
            )

            # 获取更新后的状态
            final_status = self.db.get_character_status(session_id) or {}

            # 步骤4：记录历史
            self.db.add_scene_history(
                chat_id=session_id,
                location=scene_reply["地点"],
                clothing=scene_reply["着装"],
                scene_description=scene_reply["场景"],
                user_message=user_message,
                bot_reply=scene_reply["场景"]
            )

            # 步骤5：格式化输出
            scene_text = scene_reply['场景'].replace('\\n\\n', '\n\n').replace('\\n', '\n')

            # 使用更美观的格式
            formatted_reply = (
                f"┌─────────────────────┐\n"
                f"│ 📍 {scene_reply['地点']}\n"
                f"│ 👗 {scene_reply['着装']}\n"
                f"└─────────────────────┘\n\n"
                f"{scene_text}"
            )

            # 获取状态变化提示和状态栏
            status_changes_text = self.status_formatter.format_status_changes(original_status, final_status, state_decision)
            status_bar_text = self.status_formatter.format_status_bar(final_status)
            status_bar_position = self.get_config("scene.status_bar.position", "bottom")

            # 组合状态信息
            status_block_parts = []
            if status_changes_text:
                status_block_parts.append(status_changes_text)
            if status_bar_text:
                status_block_parts.append(status_bar_text)

            if status_block_parts:
                status_block = "\n".join(status_block_parts)
                if status_bar_position == "top":
                    formatted_reply = f"{status_block}\n\n{formatted_reply}"
                else:
                    formatted_reply += f"\n\n{status_block}"

            logger.info(f"[SceneFormat] 场景回复生成成功")

            # 步骤6：尝试生成 NAI 配图
            image_path = await self._try_generate_nai_image(session_id, scene_reply)

            if image_path:
                segments = [
                    Seg(type="text", data=formatted_reply),
                    Seg(type="imageurl", data=f"file://{image_path}")
                ]
                await self.send_custom(
                    stream_id=chat_id,
                    message_type="seglist",
                    content=segments
                )
            else:
                await self.send_text(stream_id=chat_id, text=formatted_reply)

            return True, False, formatted_reply, None, None

        except Exception as e:
            logger.error(f"[SceneFormat] 处理场景回复时出错: {e}", exc_info=True)
            try:
                await self.send_text(stream_id=chat_id, text="⚠️ 场景处理出错，请稍后重试")
            except Exception:
                pass
            return True, False, None, None, None

    def _update_scene_state(
        self,
        session_id: str,
        scene_reply: Dict[str, str],
        state_decision: Dict[str, Any],
        current_state: Dict[str, str],
        activity_summary: Optional[str] = None
    ):
        """更新数据库状态"""
        try:
            # 更新场景状态
            self.db.update_scene_state(
                chat_id=session_id,
                location=scene_reply["地点"],
                clothing=scene_reply["着装"],
                scene_description=scene_reply["场景"],
                activity=activity_summary
            )

            # 更新角色状态
            character_updates = state_decision.get("角色状态更新", {})
            if character_updates:
                processed_updates = {}
                current_status = self.db.get_character_status(session_id) or {}

                numeric_fields = ['pleasure_value', 'corruption_level', 'semen_volume', 'anal_development', 'vaginal_capacity']

                for key, value in character_updates.items():
                    if isinstance(value, (int, float)) and key in numeric_fields:
                        current_value = current_status.get(key, 0) or 0
                        processed_updates[key] = current_value + value
                    else:
                        processed_updates[key] = value

                self.db.update_character_status(session_id, processed_updates)
                logger.debug(f"[SceneFormat] 角色状态已更新: {list(processed_updates.keys())}")

        except Exception as e:
            logger.error(f"[SceneFormat] 更新状态失败: {e}")

    def _derive_last_activity(self, user_message: str, state_decision: Dict[str, Any], scene_reply: Dict[str, str]) -> str:
        """根据状态变化和用户消息生成简短的最后活动描述"""
        if state_decision.get("地点变化"):
            location = scene_reply.get("地点", "")
            return f"移动到{location}" if location else "地点变更"
        if state_decision.get("着装变化"):
            clothing = scene_reply.get("着装", "")
            return f"换装为{clothing}" if clothing else "更换着装"

        condensed_user = collapse_text(user_message)
        if condensed_user:
            return truncate_text(condensed_user, 40)

        scene_excerpt = collapse_text(scene_reply.get("场景"))
        if scene_excerpt:
            return truncate_text(scene_excerpt, 40)

        return "场景更新"

    async def _try_generate_nai_image(self, session_id: str, scene_reply: Dict[str, str]) -> Optional[str]:
        """尝试生成 NAI 配图（智能配图 + 概率兜底）"""
        try:
            # 检查是否启用 NAI
            if not self.db.get_nai_enabled(session_id):
                return None

            api_key = self.get_config("nai.api_key", "")
            if not api_key:
                logger.warning("[NAI] API Token 未配置，跳过生图")
                return None

            # 智能配图：检查模型是否建议配图
            should_generate = scene_reply.get("建议配图", None)
            nai_prompt = scene_reply.get("nai_prompt", "")

            # 如果模型输出了建议配图字段，使用智能配图
            if should_generate is not None:
                if not should_generate:
                    logger.debug("[NAI] 模型判断无需配图，跳过")
                    return None

                if not nai_prompt:
                    logger.warning("[NAI] 建议配图但未提供 nai_prompt，跳过")
                    return None

                logger.info(f"[NAI] 智能配图触发，prompt: {nai_prompt[:100]}...")

            else:
                # 兜底：模型未输出配图字段，使用概率触发
                trigger_probability = self.get_config("nai.trigger_probability", 0.3)
                try:
                    trigger_probability = float(trigger_probability)
                    trigger_probability = max(0.0, min(1.0, trigger_probability))
                except (TypeError, ValueError):
                    trigger_probability = 0.3

                if random.random() > trigger_probability:
                    logger.debug("[NAI] 概率未触发，跳过生图")
                    return None

                logger.info(f"[NAI] 概率兜底触发 ({trigger_probability*100:.0f}%)")

                # 兜底时使用简单的默认 prompt
                if not nai_prompt:
                    location = scene_reply.get("地点", "indoor")
                    clothing = scene_reply.get("着装", "casual clothes")
                    nai_prompt = f"1girl, {location}, {clothing}"
                    logger.info(f"[NAI] 使用默认 prompt: {nai_prompt}")

            # 生成图片（会在 nai_client 中拼接外貌和质量词）
            success, result = await self.nai_client.generate_image(nai_prompt)

            if success and result:
                logger.info(f"[NAI] 图片生成成功: {result}")
                return result
            else:
                logger.warning(f"[NAI] 图片生成失败: {result}")
                return None

        except Exception as e:
            logger.error(f"[NAI] 生成配图时出错: {e}", exc_info=True)
            return None

    def _detect_scene_type(self, user_message: str, last_scene: str = "") -> str:
        """识别场景类型"""
        user_msg_lower = user_message.lower()
        last_scene_lower = last_scene.lower() if last_scene else ""

        # 按优先级检查用户消息
        for scene_type in [SCENE_TYPE_EXPLICIT, SCENE_TYPE_INTIMATE, SCENE_TYPE_ROMANTIC, SCENE_TYPE_REST]:
            keywords = SCENE_TYPE_KEYWORDS.get(scene_type, [])
            for keyword in keywords:
                if keyword in user_msg_lower:
                    return scene_type

        # 检查上次场景（用于连续场景）
        if last_scene_lower:
            for scene_type in [SCENE_TYPE_EXPLICIT, SCENE_TYPE_INTIMATE]:
                keywords = SCENE_TYPE_KEYWORDS.get(scene_type, [])
                for keyword in keywords:
                    if keyword in last_scene_lower:
                        if scene_type == SCENE_TYPE_EXPLICIT:
                            return SCENE_TYPE_INTIMATE
                        return scene_type

        return SCENE_TYPE_NORMAL

    def _resolve_active_state(self, chat_id: str, user_id: Optional[str]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """根据当前消息确定应该使用的场景状态"""
        session_id = build_session_id(chat_id, user_id)
        state = self.db.get_scene_state(session_id)
        if state:
            return session_id, state

        if user_id:
            user_state = self.db.get_state_by_user(chat_id, user_id)
            if user_state:
                return user_state["chat_id"], user_state

        if not user_id:
            legacy_state = self.db.get_scene_state(chat_id)
            if legacy_state:
                return chat_id, legacy_state

            fallback_state = self.db.get_latest_session_state(chat_id)
            if fallback_state:
                logger.warning(f"[SceneFormat] 回退为最近一次会话: {fallback_state['chat_id']}")
                return fallback_state["chat_id"], fallback_state

        return session_id, None

    def _should_handle_message(self, message: MaiMessages) -> bool:
        """判断当前消息是否应该由场景模式回复"""
        if message.is_private_message:
            return True

        add_cfg = message.additional_data or {}
        if add_cfg.get("at_bot") or add_cfg.get("is_mentioned"):
            return True

        if self._segments_contain_mention(getattr(message, "message_segments", [])):
            return True

        nickname = str(global_config.bot.nickname or "")
        if nickname and nickname in (message.plain_text or ""):
            return True

        return False

    def _segments_contain_mention(self, segments) -> bool:
        """递归检测消息段中是否包含 mention_bot"""
        try:
            for seg in segments or []:
                seg_type = getattr(seg, "type", "")
                if seg_type == "mention_bot":
                    return True
                if seg_type == "seglist":
                    if self._segments_contain_mention(getattr(seg, "data", [])):
                        return True
        except Exception:
            return False
        return False

    @classmethod
    def get_handler_info(cls) -> EventHandlerInfo:
        """返回处理器信息"""
        return EventHandlerInfo(
            name="scene_format_handler",
            component_type=ComponentType.EVENT_HANDLER,
            description="场景格式生成处理器（双模型架构：planner判断+reply生成）",
            event_type=cls.event_type,
            weight=cls.weight,
            intercept_message=cls.intercept_message
        )
