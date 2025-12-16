"""
场景格式事件处理器 - 使用双模型架构
- planner模型：判断状态变化
- reply模型：生成场景描述和回复内容
"""
import json
import re
import random
from typing import Tuple, Optional, Dict, Any, List
from datetime import datetime

from maim_message import Seg
from src.plugin_system.base.base_events_handler import BaseEventHandler
from src.plugin_system.base.component_types import EventType, EventHandlerInfo, MaiMessages, CustomEventHandlerResult, ComponentType
from src.config.config import global_config
from src.common.logger import get_logger
from ..core.scene_db import SceneDB
from ..core.preset_manager import PresetManager
from ..core.llm_client import LLMClientFactory
from ..core.nai_client import NaiClient

logger = get_logger("scene_format_handler")


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
        self.db = None  # 延迟初始化
        self.planner_llm = None
        self.reply_llm = None
        self.preset_manager = None
        self.nai_client = None

    def _ensure_initialized(self):
        """确保已初始化"""
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
            # 未启用，放行到正常LLM处理
            return True, True, None, None, None

        # 仅在私聊或被明确提及时才截获
        if not self._should_handle_message(message):
            return True, True, None, None, None

        logger.info(f"[SceneFormat] 拦截到场景模式消息: {session_id}")

        try:
            # 获取用户消息
            user_message = message.plain_text or ""

            # 获取角色状态（如果不存在则初始化）
            self.db.init_character_status(session_id)
            character_status = self.db.get_character_status(session_id) or {}

            # 为Planner和Reply构建不同长度的上下文
            planner_context = self._build_context_block(session_id, context_type="planner")
            reply_context = self._build_context_block(session_id, context_type="reply")

            # 步骤1：使用planner模型判断所有状态变化（地点、着装、角色状态）
            state_decision = await self._plan_state_changes(
                user_message=user_message,
                current_location=current_state["location"],
                current_clothing=current_state["clothing"],
                last_scene=current_state["scene_description"],
                character_status=character_status,
                conversation_context=planner_context
            )

            # 步骤1.5：应用 planner 判断的状态更新到临时状态（让 reply 能看到最新状态）
            updated_character_status = self._apply_state_updates_preview(character_status, state_decision)

            # 步骤2：使用reply模型生成场景和回复（使用更新后的状态）
            scene_reply = await self._generate_scene_reply(
                user_message=user_message,
                current_location=current_state["location"],
                current_clothing=current_state["clothing"],
                last_scene=current_state["scene_description"],
                character_status=updated_character_status,  # 使用更新后的状态
                state_decision=state_decision,
                conversation_context=reply_context
            )

            if not scene_reply:
                logger.error(f"[SceneFormat] 场景回复生成失败")
                return True, True, None, None, None

            # 步骤3：更新数据库状态（场景状态 + 角色状态）
            self._update_scene_state(
                session_id=session_id,
                scene_reply=scene_reply,
                state_decision=state_decision,
                current_state=current_state,
                activity_summary=self._derive_last_activity(user_message, state_decision, scene_reply)
            )

            # 步骤4：记录历史
            self.db.add_scene_history(
                chat_id=session_id,
                location=scene_reply["地点"],
                clothing=scene_reply["着装"],
                scene_description=scene_reply["场景"],
                user_message=user_message,
                bot_reply=scene_reply["场景"]  # 使用场景内容作为回复
            )

            # 步骤5：格式化输出（支持多段落）
            # 处理场景中的换行符，将 \n\n 替换为实际的双换行
            scene_text = scene_reply['场景'].replace('\\n\\n', '\n\n').replace('\\n', '\n')

            formatted_reply = (
                f"📍 地点：{scene_reply['地点']}\n"
                f"👗 着装：{scene_reply['着装']}\n\n"
                f"🎬 场景：\n{scene_text}"
            )

            logger.info(f"[SceneFormat] 场景回复生成成功")

            # 步骤6：如果 NAI 生图已开启，尝试生成配图并与文本一起发送
            image_path = await self._try_generate_nai_image(session_id, scene_reply)

            if image_path:
                # 图文合并发送，构造一个 seglist，文本在前、图片在后
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
                # 没有图片，只发送文本
                await self.send_text(stream_id=chat_id, text=formatted_reply)

            # 返回值说明：
            # success=True: 处理成功
            # continue_chain=False: 阻止后续LLM调用
            # reply=formatted_reply: 直接回复
            return True, False, formatted_reply, None, None

        except Exception as e:
            logger.error(f"[SceneFormat] 处理场景回复时出错: {e}", exc_info=True)
            # 出错时放行到正常LLM处理
            return True, True, None, None, None

    async def _plan_state_changes(
        self,
        user_message: str,
        current_location: str,
        current_clothing: str,
        last_scene: str,
        character_status: Dict[str, Any],
        conversation_context: str = ""
    ) -> Dict[str, Any]:
        """
        步骤1：使用planner模型判断所有状态变化（地点、着装、角色状态）

        返回格式：
        {
            "地点变化": true/false,
            "新地点": "...",
            "着装变化": true/false,
            "新着装": "...",
            "角色状态更新": {
                "physiological_state": "新值",
                "vaginal_state": "新值",
                "pleasure_value": +10,
                ...
            }
        }
        """
        bot_name = global_config.bot.nickname
        bot_personality = getattr(global_config.personality, "personality", "")
        bot_reply_style = getattr(global_config.personality, "reply_style", "")

        # 格式化当前角色状态
        # 解析 JSON 字段
        import json

        # 道具栏
        inventory_raw = character_status.get('inventory', '[]')
        try:
            inventory_list = json.loads(inventory_raw) if inventory_raw else []
        except json.JSONDecodeError:
            inventory_list = []
        inventory_text = ", ".join(inventory_list) if inventory_list else "无"

        # 阴道内异物
        vaginal_foreign_raw = character_status.get('vaginal_foreign', '[]')
        try:
            vaginal_foreign_list = json.loads(vaginal_foreign_raw) if vaginal_foreign_raw else []
        except json.JSONDecodeError:
            vaginal_foreign_list = []

        # 精液来源
        semen_sources_raw = character_status.get('semen_sources', '[]')
        try:
            semen_sources_list = json.loads(semen_sources_raw) if semen_sources_raw else []
        except json.JSONDecodeError:
            semen_sources_list = []

        # 永久改造
        permanent_mods_raw = character_status.get('permanent_mods', '{}')
        try:
            permanent_mods_dict = json.loads(permanent_mods_raw) if permanent_mods_raw else {}
        except json.JSONDecodeError:
            permanent_mods_dict = {}

        # 身体部位状况
        body_condition_raw = character_status.get('body_condition', '{}')
        try:
            body_condition_dict = json.loads(body_condition_raw) if body_condition_raw else {}
        except json.JSONDecodeError:
            body_condition_dict = {}

        # 性癖
        fetishes_raw = character_status.get('fetishes', '{}')
        try:
            fetishes_dict = json.loads(fetishes_raw) if fetishes_raw else {}
        except json.JSONDecodeError:
            fetishes_dict = {}

        # 基础状态（始终显示）
        status_lines = [
            f"生理状态: {character_status.get('physiological_state', '呼吸平稳')}",
            f"阴道状态: {character_status.get('vaginal_state', '放松')}",
            f"湿润度: {character_status.get('vaginal_wetness', '正常')}",
            f"快感值: {character_status.get('pleasure_value', 0)}/{character_status.get('pleasure_threshold', 100)}",
            f"污染度: {character_status.get('corruption_level', 0)}",
            f"怀孕状态: {character_status.get('pregnancy_status', '未受孕')}",
            f"体内精液: {character_status.get('semen_volume', 0)}ml",
            f"当前道具: {inventory_text}"
        ]

        # 条件显示字段（仅在有非默认值时显示）

        # 精液来源（当体内有精液时显示）
        semen_volume = character_status.get('semen_volume', 0)
        if semen_volume > 0 and semen_sources_list:
            sources_text = ", ".join(semen_sources_list)
            status_lines.append(f"精液来源: {sources_text}")

        # 怀孕详情（当怀孕时显示来源和天数）
        pregnancy_status = character_status.get('pregnancy_status', '未受孕')
        if pregnancy_status == '受孕中':
            pregnancy_source = character_status.get('pregnancy_source', '未知')
            pregnancy_counter = character_status.get('pregnancy_counter', 0)
            status_lines.append(f"怀孕详情: 父亲({pregnancy_source}), 已怀孕{pregnancy_counter}天")

        vaginal_capacity = character_status.get('vaginal_capacity', 100)
        if vaginal_capacity != 100:
            status_lines.append(f"阴道容量: {vaginal_capacity}")

        anal_dev = character_status.get('anal_development', 0)
        if anal_dev > 0:
            status_lines.append(f"后穴开发度: {anal_dev}/100")

        if vaginal_foreign_list:
            foreign_text = ", ".join(vaginal_foreign_list)
            status_lines.append(f"阴道内异物: {foreign_text}")

        if permanent_mods_dict:
            mods_text = ", ".join([f"{k}({v})" for k, v in permanent_mods_dict.items()])
            status_lines.append(f"永久改造: {mods_text}")

        if body_condition_dict:
            condition_text = ", ".join([f"{k}:{v}" for k, v in body_condition_dict.items()])
            status_lines.append(f"部位状况: {condition_text}")

        # 性癖（显示名称、等级和经验）
        if fetishes_dict:
            fetishes_text = ", ".join([
                f"{name}Lv{data.get('等级', 0)}({data.get('经验', 0)}exp)" if isinstance(data, dict) else f"{name}"
                for name, data in fetishes_dict.items()
            ])
            status_lines.append(f"已有性癖: {fetishes_text}")

        status_summary = "\n".join(status_lines)

        prompt = f"""【你的身份】
你是 {bot_name}

【性格特质与身份】
{bot_personality}

【回复风格】
{bot_reply_style}

【当前场景状态】
地点：{current_location}
着装：{current_clothing}
上次场景：{last_scene}

【当前角色状态】
{status_summary}

{conversation_context or "【最近场景对话】暂无历史记录"}

【用户消息】
{user_message}

【任务】
你需要合理判断：根据用户的消息和当前对话内容，哪些状态需要改变？

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【状态变化原则】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**核心原则**：
1. 符合真实的生理和心理反应
2. 根据场景强度合理判断，不要机械套用规则
3. 心理唤起（幻想、臆想）也会引起生理反应
4. 状态变化要渐进，不要突然跳跃
5. 有互动/有情节就应该有反应，不要过度保守

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【各状态说明】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**1. 地点变化**
- 用户明确移动到新地点时更新
- 没有明确移动不变化

**2. 着装变化**
- 用户明确换装、脱衣、穿衣时更新
- 衣服损坏在生理状态或body_condition中描述

**3. 生理状态 (physiological_state)**
- 默认："呼吸平稳"
- 根据场景合理描述当前的整体生理状态
- 包括：呼吸、颤抖、发热、心跳、紧张等

**4. 阴道状态 (vaginal_state)**
- 默认："放松"
- 可选值："放松"、"轻微收缩"、"无意识收缩"、"紧绷"、"痉挛"
- 仅在明确性行为（插入、刺激等）时更新

**5. 湿润度 (vaginal_wetness)**
- 默认："正常"
- 递进顺序："正常" → "微湿" → "湿润" → "淫湿" → "爱液横流"
- 根据唤起程度合理更新（性刺激、爱抚、甚至强烈的幻想都可能引起）
- 符合真实生理反应

**6. 快感值 (pleasure_value)**
- 默认：0，范围：0~阈值（默认100）
- 根据刺激/唤起强度合理增加，不要机械套用固定数值
- 高潮后会自动重置
- 单次增加上限：60
- 心理唤起（幻想）也会增加快感值

**7. 污染度 (corruption_level)**
- 默认：0，范围：0~100
- **严格限制**：仅在明确的腐化事件时增加
  * 首次性行为、被多人侵犯、接触腐蚀物质、被灌输淫乱思想等
- 普通性行为不增加污染度
- 单次增加上限：20

**8. 体内精液 (semen_volume, semen_sources)**
- 默认：0ml, []
- 仅在明确体内射精时增加（+30~80ml）
- 同时记录来源到 semen_sources 数组
- 清理、流出时减少

**9. 阴道内异物 (vaginal_foreign)**
- 默认：[]
- 明确植入异物时更新（数组格式）

**10. 怀孕状态 (pregnancy_status, pregnancy_source, pregnancy_counter)**
- 默认："未受孕", null, 0
- **极其严格**：仅在特殊剧情需要时变为"受孕中"
- 不要随意改变

**11. 性癖经验 (fetishes)**
- 默认：{{}}
- 体验对应性癖内容时增加经验（+5~15exp）
- 格式：{{"性癖名": {{"经验": 值, "等级": 值}}}}

**12. 道具栏 (inventory)**
- 默认：[]
- 明确获得或失去道具时更新
- 输出完整道具栏

**13. 后穴开发度 (anal_development)**
- 默认：0，范围：0~100
- 仅在明确后穴刺激时增加
- 单次增加上限：20

**14. 阴道容量 (vaginal_capacity)**
- 默认：100，范围：50~300
- 仅在明确扩张训练时增加
- 普通性行为不改变
- 单次增加上限：40

**15. 永久改造 (permanent_mods)**
- 默认：{{}}
- **严格限制**：仅在明确永久性改造时添加（纹身、穿孔等）

**16. 身体部位状况 (body_condition)**
- 默认：{{}}
- 记录各部位的持续性特殊状态
- 格式：{{"部位": "状态"}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【判断示例（参考，不是固定规则）】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

场景1：用户温柔拥抱
→ 可能：生理状态更新, 快感值小幅增加

场景2：bot内心幻想与用户的亲密接触
→ 可能：生理状态更新, 快感值增加, 湿润度可能微湿

场景3：用户爱抚身体
→ 可能：生理状态更新, 快感值增加, 湿润度增加

场景4：普通闲聊
→ 角色状态更新为 {{}}

场景5：明确性行为
→ 多项状态更新（根据具体内容判断）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【默认行为】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- 普通日常对话（无互动/无情节）→ "角色状态更新" 为 {{}}
- 没有移动 → 地点不变
- 没有换装 → 着装不变

【输出格式】
严格按照JSON格式输出：

```json
{{
  "地点变化": false,
  "新地点": "",
  "着装变化": false,
  "新着装": "",
  "角色状态更新": {{
    "physiological_state": "新的生理状态",
    "vaginal_state": "新的阴道状态",
    "vaginal_wetness": "新的湿润度",
    "pleasure_value": 15,
    "corruption_level": 3,
    "semen_volume": 50,
    "semen_sources": ["用户"],
    "vaginal_foreign": ["触手"],
    "pregnancy_status": "受孕中",
    "pregnancy_source": "用户",
    "pregnancy_counter": 0,
    "fetishes": {{
      "口交": {{"经验": 10, "等级": 1}}
    }},
    "inventory": ["钥匙", "药水"],
    "anal_development": 5,
    "vaginal_capacity": 10,
    "permanent_mods": {{
      "纹身": "下腹部淫纹"
    }},
    "body_condition": {{
      "乳房": "红肿敏感"
    }}
  }}
}}
```

【重要提醒】
- 如果是普通日常对话（无互动），"角色状态更新" 必须为 {{}}
- 每个状态变化要合理，符合场景逻辑
- 遵守数值范围限制
- 只输出需要更新的字段，不需要输出完整列表
- 有互动就有反应，合理判断，不要过度保守"""

        try:
            logger.info(f"[Planner] Prompt:\n{prompt}")

            # 调用planner模型
            response, _ = await self.planner_llm.generate_response_async(prompt)

            logger.info(f"[Planner] Response:\n{response}")

            # 解析JSON
            decision = self._parse_json_response(response)

            if not decision:
                # 解析失败，默认不变化
                return self._get_default_decision()

            # 处理一些模型可能输出的带空格字段（例如“地 点 变 化”）
            decision = self._normalize_planner_decision(decision)

            # 确保必要字段存在
            decision.setdefault("地点变化", False)
            decision.setdefault("新地点", "")
            decision.setdefault("着装变化", False)
            decision.setdefault("新着装", "")
            decision.setdefault("角色状态更新", {})

            # 【新增】验证和限制状态变化
            decision = self._validate_state_decision(decision, character_status)

            return decision

        except Exception as e:
            logger.error(f"[Planner] 状态决策失败: {e}")
            return self._get_default_decision()

    def _normalize_planner_decision(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        """清除planner返回键名和值中的多余空格，确保字段能被识别"""
        normalized = {}
        for key, value in decision.items():
            clean_key = re.sub(r"\s+", "", str(key))

            # 清理值中的空格
            if isinstance(value, str):
                # 字符串值：移除所有空格
                clean_value = re.sub(r"\s+", "", value) if value else value
            elif isinstance(value, dict):
                # 字典值：递归清理
                clean_value = self._clean_dict_spaces(value)
            elif isinstance(value, list):
                # 列表值：清理每个元素
                clean_value = [re.sub(r"\s+", "", str(v)) if isinstance(v, str) else v for v in value]
            else:
                clean_value = value

            normalized[clean_key] = clean_value

        # 确保角色状态更新字段仍然是字典
        if "角色状态更新" not in normalized:
            for alias in ("角色状态", "状态更新"):
                if alias in normalized:
                    normalized["角色状态更新"] = normalized[alias]
                    break

        updates = normalized.get("角色状态更新")
        if isinstance(updates, dict):
            normalized["角色状态更新"] = updates
        else:
            normalized["角色状态更新"] = {}

        return normalized

    def _clean_dict_spaces(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """递归清理字典中所有字符串值的空格"""
        cleaned = {}
        for key, value in d.items():
            clean_key = re.sub(r"\s+", "", str(key)) if isinstance(key, str) else key

            if isinstance(value, str):
                clean_value = re.sub(r"\s+", "", value) if value else value
            elif isinstance(value, dict):
                clean_value = self._clean_dict_spaces(value)
            elif isinstance(value, list):
                clean_value = [re.sub(r"\s+", "", str(v)) if isinstance(v, str) else v for v in value]
            else:
                clean_value = value

            cleaned[clean_key] = clean_value

        return cleaned

    def _get_default_decision(self) -> Dict[str, Any]:
        """返回默认决策（无变化）"""
        return {
            "地点变化": False,
            "新地点": "",
            "着装变化": False,
            "新着装": "",
            "角色状态更新": {}
        }

    def _validate_state_decision(self, decision: Dict[str, Any], current_status: Dict[str, Any]) -> Dict[str, Any]:
        """验证和限制状态变化，确保符合规则"""
        status_updates = decision.get("角色状态更新", {})

        if not status_updates:
            return decision

        validated_updates = {}

        # 1. 快感值验证
        if "pleasure_value" in status_updates:
            value = status_updates["pleasure_value"]
            if isinstance(value, (int, float)):
                # 限制单次增加不超过60
                if value > 60:
                    logger.warning(f"[Planner] 快感值增加过大({value})，限制为60")
                    value = 60
                elif value < -100:
                    logger.warning(f"[Planner] 快感值减少过大({value})，限制为-100")
                    value = -100

                # 计算新的快感值
                current_pleasure = current_status.get("pleasure_value", 0) or 0
                threshold = current_status.get("pleasure_threshold", 100) or 100
                new_pleasure = current_pleasure + value

                # 如果超过阈值，自动触发高潮并重置
                if new_pleasure >= threshold:
                    logger.info(f"[Planner] 快感值达到阈值({new_pleasure}/{threshold})，触发高潮重置")
                    # 重置为低值而非 0
                    value = -current_pleasure + (threshold // 4)  # 重置到阈值的 1/4
                    validated_updates["physiological_state"] = "高潮后的余韵中颤抖"

                validated_updates["pleasure_value"] = value

        # 2. 污染度验证
        if "corruption_level" in status_updates:
            value = status_updates["corruption_level"]
            if isinstance(value, (int, float)):
                # 限制单次增加不超过20
                if value > 20:
                    logger.warning(f"[Planner] 污染度增加过大({value})，限制为20")
                    value = 20
                elif value < 0:
                    logger.warning(f"[Planner] 污染度不能减少，忽略")
                    value = 0  # 污染度不能减少

                # 计算新污染度，限制最大值为100
                current_corruption = current_status.get("corruption_level", 0) or 0
                if current_corruption + value > 100:
                    logger.warning(f"[Planner] 污染度超过上限(100)，限制增加量")
                    value = max(0, 100 - current_corruption)

                if value > 0:
                    validated_updates["corruption_level"] = value

        # 3. 体内精液验证
        if "semen_volume" in status_updates:
            value = status_updates["semen_volume"]
            if isinstance(value, (int, float)):
                # 限制单次增加不超过150ml
                if value > 150:
                    logger.warning(f"[Planner] 精液量增加过大({value}ml)，限制为150ml")
                    value = 150
                elif value < -500:
                    value = -500  # 限制减少量

                # 计算新精液量，不能为负
                current_volume = current_status.get("semen_volume", 0) or 0
                new_volume = current_volume + value
                if new_volume < 0:
                    value = -current_volume  # 最多清空到0

                # 限制最大容量为500ml
                if new_volume > 500:
                    logger.warning(f"[Planner] 体内精液超过容量上限(500ml)，限制")
                    value = max(0, 500 - current_volume)

                validated_updates["semen_volume"] = value

        # 4. 生理状态验证
        if "physiological_state" in status_updates:
            value = str(status_updates["physiological_state"])
            if len(value) > 100:
                logger.warning(f"[Planner] 生理状态描述过长，截断")
                value = value[:100]
            # 过滤敏感词（如果需要）
            validated_updates["physiological_state"] = value

        # 5. 阴道状态验证（仅允许特定值）
        if "vaginal_state" in status_updates:
            allowed_values = ["放松", "轻微收缩", "无意识收缩", "紧绷", "痉挛"]
            value = str(status_updates["vaginal_state"])
            if value not in allowed_values:
                logger.warning(f"[Planner] 阴道状态值({value})不合法，忽略")
            else:
                validated_updates["vaginal_state"] = value

        # 6. 湿润度验证（仅允许特定值和递进）
        if "vaginal_wetness" in status_updates:
            allowed_values = ["正常", "微湿", "湿润", "淫湿", "爱液横流"]
            value = str(status_updates["vaginal_wetness"])
            if value not in allowed_values:
                logger.warning(f"[Planner] 湿润度值({value})不合法，忽略")
            else:
                # 验证递进顺序（可选：防止跳跃式变化）
                current_wetness = current_status.get("vaginal_wetness", "正常")
                current_idx = allowed_values.index(current_wetness) if current_wetness in allowed_values else 0
                new_idx = allowed_values.index(value)

                # 如果跳跃超过2级，警告（但仍允许）
                if abs(new_idx - current_idx) > 2:
                    logger.warning(f"[Planner] 湿润度变化过大：{current_wetness} → {value}")

                validated_updates["vaginal_wetness"] = value

        # 7. 怀孕状态验证
        if "pregnancy_status" in status_updates:
            allowed_values = ["未受孕", "受孕中"]
            value = str(status_updates["pregnancy_status"])
            if value not in allowed_values:
                logger.warning(f"[Planner] 怀孕状态值({value})不合法，忽略")
            else:
                # 额外记录日志
                current_pregnancy = current_status.get("pregnancy_status", "未受孕")
                if current_pregnancy != value:
                    logger.info(f"[Planner] 怀孕状态变化: {current_pregnancy} → {value}")
                validated_updates["pregnancy_status"] = value

        # 8. 其他字段直接通过（但需要类型检查）
        if "pregnancy_source" in status_updates:
            validated_updates["pregnancy_source"] = str(status_updates["pregnancy_source"])

        if "pregnancy_counter" in status_updates:
            value = status_updates["pregnancy_counter"]
            if isinstance(value, (int, float)):
                validated_updates["pregnancy_counter"] = int(value)

        if "semen_sources" in status_updates:
            # 确保是列表或JSON字符串
            value = status_updates["semen_sources"]
            if isinstance(value, list):
                import json
                validated_updates["semen_sources"] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, str):
                validated_updates["semen_sources"] = value

        if "vaginal_foreign" in status_updates:
            # 确保是列表或JSON字符串
            value = status_updates["vaginal_foreign"]
            if isinstance(value, list):
                import json
                validated_updates["vaginal_foreign"] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, str):
                validated_updates["vaginal_foreign"] = value

        if "fetishes" in status_updates:
            # 确保是对象或JSON字符串
            value = status_updates["fetishes"]
            if isinstance(value, dict):
                import json
                validated_updates["fetishes"] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, str):
                validated_updates["fetishes"] = value

        if "inventory" in status_updates:
            # 确保是列表或JSON字符串
            value = status_updates["inventory"]
            if isinstance(value, list):
                import json
                validated_updates["inventory"] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, str):
                validated_updates["inventory"] = value

        # 9. 后穴开发度验证
        if "anal_development" in status_updates:
            value = status_updates["anal_development"]
            if isinstance(value, (int, float)):
                # 限制单次增加不超过20
                if value > 20:
                    logger.warning(f"[Planner] 后穴开发度增加过大({value})，限制为20")
                    value = 20
                elif value < -100:
                    value = -100  # 限制减少量

                # 计算新开发度，限制范围 0-100
                current_development = current_status.get("anal_development", 0) or 0
                new_development = current_development + value
                if new_development < 0:
                    value = -current_development  # 最多减到0
                elif new_development > 100:
                    logger.warning(f"[Planner] 后穴开发度超过上限(100)，限制")
                    value = max(0, 100 - current_development)

                validated_updates["anal_development"] = value

        # 10. 阴道容量验证
        if "vaginal_capacity" in status_updates:
            value = status_updates["vaginal_capacity"]
            if isinstance(value, (int, float)):
                # 限制单次增加不超过40
                if value > 40:
                    logger.warning(f"[Planner] 阴道容量增加过大({value})，限制为40")
                    value = 40
                elif value < -100:
                    value = -100  # 限制减少量

                # 计算新容量，限制范围 50-300
                current_capacity = current_status.get("vaginal_capacity", 100) or 100
                new_capacity = current_capacity + value
                if new_capacity < 50:
                    logger.warning(f"[Planner] 阴道容量低于下限(50)，限制")
                    value = max(-current_capacity, 50 - current_capacity)
                elif new_capacity > 300:
                    logger.warning(f"[Planner] 阴道容量超过上限(300)，限制")
                    value = min(value, 300 - current_capacity)

                validated_updates["vaginal_capacity"] = value

        # 11. 永久性改造验证
        if "permanent_mods" in status_updates:
            # 确保是对象或JSON字符串
            value = status_updates["permanent_mods"]
            if isinstance(value, dict):
                import json
                # 额外记录日志
                if value:
                    logger.info(f"[Planner] 永久性改造更新: {list(value.keys())}")
                validated_updates["permanent_mods"] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, str):
                validated_updates["permanent_mods"] = value

        # 12. 身体部位状况验证
        if "body_condition" in status_updates:
            # 确保是对象或JSON字符串
            value = status_updates["body_condition"]
            if isinstance(value, dict):
                import json
                validated_updates["body_condition"] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, str):
                validated_updates["body_condition"] = value

        decision["角色状态更新"] = validated_updates

        if validated_updates:
            logger.info(f"[Planner] 状态验证通过: {list(validated_updates.keys())}")

        return decision

    def _apply_state_updates_preview(self, current_status: Dict[str, Any], state_decision: Dict[str, Any]) -> Dict[str, Any]:
        """
        应用 planner 判断的状态更新，生成临时预览状态（供 reply 使用）

        Args:
            current_status: 当前角色状态
            state_decision: planner 判断的状态变化

        Returns:
            更新后的临时状态（不修改数据库）
        """
        import json

        # 深拷贝当前状态，避免修改原始数据
        preview_status = dict(current_status)
        status_updates = state_decision.get("角色状态更新", {})

        if not status_updates:
            return preview_status

        # 应用增量更新
        for key, value in status_updates.items():
            if isinstance(value, (int, float)) and key in ['pleasure_value', 'corruption_level', 'semen_volume', 'anal_development', 'vaginal_capacity']:
                # 数值字段：累加
                current_value = preview_status.get(key, 0) or 0
                preview_status[key] = current_value + value
            elif key in ['semen_sources', 'vaginal_foreign', 'inventory', 'fetishes', 'permanent_mods', 'body_condition']:
                # JSON 字段：直接替换（已经是 JSON 字符串）
                preview_status[key] = value
            else:
                # 其他字段：直接替换
                preview_status[key] = value

        logger.debug(f"[SceneFormat] 应用状态更新预览: {list(status_updates.keys())}")
        return preview_status

    async def _generate_scene_reply(
        self,
        user_message: str,
        current_location: str,
        current_clothing: str,
        last_scene: str,
        character_status: Dict[str, Any],
        state_decision: Dict[str, Any],
        conversation_context: str = ""
    ) -> Optional[Dict[str, str]]:
        """
        步骤2：使用reply模型生成场景描述和回复（考虑所有状态）

        返回格式：
        {
            "地点": "...",
            "着装": "...",
            "场景": "...",
            "回复": "..."
        }
        """
        bot_name = global_config.bot.nickname
        bot_personality = getattr(global_config.personality, "personality", "")
        bot_reply_style = getattr(global_config.personality, "reply_style", "")

        # 构建地点和着装指令
        if state_decision["地点变化"] and state_decision["新地点"]:
            location_instruction = f"地点已更新为：{state_decision['新地点']}"
            final_location = state_decision["新地点"]
        else:
            location_instruction = f"地点保持不变：{current_location}"
            final_location = current_location

        if state_decision["着装变化"] and state_decision["新着装"]:
            clothing_instruction = f"着装已更新为：{state_decision['新着装']}"
            final_clothing = state_decision["新着装"]
        else:
            clothing_instruction = f"着装保持不变：{current_clothing}"
            final_clothing = current_clothing

        # 格式化角色状态（用于传递给 reply 模型）
        # 解析 JSON 字段
        import json

        # 道具栏
        inventory_raw = character_status.get('inventory', '[]')
        try:
            inventory_list = json.loads(inventory_raw) if inventory_raw else []
        except json.JSONDecodeError:
            inventory_list = []
        inventory_text = ", ".join(inventory_list) if inventory_list else "无"

        # 阴道内异物
        vaginal_foreign_raw = character_status.get('vaginal_foreign', '[]')
        try:
            vaginal_foreign_list = json.loads(vaginal_foreign_raw) if vaginal_foreign_raw else []
        except json.JSONDecodeError:
            vaginal_foreign_list = []

        # 精液来源
        semen_sources_raw = character_status.get('semen_sources', '[]')
        try:
            semen_sources_list = json.loads(semen_sources_raw) if semen_sources_raw else []
        except json.JSONDecodeError:
            semen_sources_list = []

        # 永久改造
        permanent_mods_raw = character_status.get('permanent_mods', '{}')
        try:
            permanent_mods_dict = json.loads(permanent_mods_raw) if permanent_mods_raw else {}
        except json.JSONDecodeError:
            permanent_mods_dict = {}

        # 身体部位状况
        body_condition_raw = character_status.get('body_condition', '{}')
        try:
            body_condition_dict = json.loads(body_condition_raw) if body_condition_raw else {}
        except json.JSONDecodeError:
            body_condition_dict = {}

        # 性癖
        fetishes_raw = character_status.get('fetishes', '{}')
        try:
            fetishes_dict = json.loads(fetishes_raw) if fetishes_raw else {}
        except json.JSONDecodeError:
            fetishes_dict = {}

        # 基础状态（始终显示）
        status_lines = [
            f"生理状态: {character_status.get('physiological_state', '呼吸平稳')}",
            f"阴道状态: {character_status.get('vaginal_state', '放松')}",
            f"湿润度: {character_status.get('vaginal_wetness', '正常')}",
            f"快感值: {character_status.get('pleasure_value', 0)}/{character_status.get('pleasure_threshold', 100)}",
            f"污染度: {character_status.get('corruption_level', 0)}",
            f"怀孕状态: {character_status.get('pregnancy_status', '未受孕')}",
            f"体内精液: {character_status.get('semen_volume', 0)}ml",
            f"当前道具: {inventory_text}"
        ]

        # 条件显示字段（仅在有非默认值时显示）

        # 精液来源（当体内有精液时显示）
        semen_volume = character_status.get('semen_volume', 0)
        if semen_volume > 0 and semen_sources_list:
            sources_text = ", ".join(semen_sources_list)
            status_lines.append(f"精液来源: {sources_text}")

        # 怀孕详情（当怀孕时显示来源和天数）
        pregnancy_status = character_status.get('pregnancy_status', '未受孕')
        if pregnancy_status == '受孕中':
            pregnancy_source = character_status.get('pregnancy_source', '未知')
            pregnancy_counter = character_status.get('pregnancy_counter', 0)
            status_lines.append(f"怀孕详情: 父亲({pregnancy_source}), 已怀孕{pregnancy_counter}天")

        vaginal_capacity = character_status.get('vaginal_capacity', 100)
        if vaginal_capacity != 100:
            status_lines.append(f"阴道容量: {vaginal_capacity}")

        anal_dev = character_status.get('anal_development', 0)
        if anal_dev > 0:
            status_lines.append(f"后穴开发度: {anal_dev}/100")

        if vaginal_foreign_list:
            foreign_text = ", ".join(vaginal_foreign_list)
            status_lines.append(f"阴道内异物: {foreign_text}")

        if permanent_mods_dict:
            mods_text = ", ".join([f"{k}({v})" for k, v in permanent_mods_dict.items()])
            status_lines.append(f"永久改造: {mods_text}")

        if body_condition_dict:
            condition_text = ", ".join([f"{k}:{v}" for k, v in body_condition_dict.items()])
            status_lines.append(f"部位状况: {condition_text}")

        # 性癖（显示名称、等级和经验）
        if fetishes_dict:
            fetishes_text = ", ".join([
                f"{name}Lv{data.get('等级', 0)}({data.get('经验', 0)}exp)" if isinstance(data, dict) else f"{name}"
                for name, data in fetishes_dict.items()
            ])
            status_lines.append(f"已有性癖: {fetishes_text}")

        status_summary = "\n".join(status_lines)

        prompt = f"""【你的身份】
你是 {bot_name}

【性格特质与身份】
{bot_personality}

【回复风格】
{bot_reply_style}

【状态决策结果】
{location_instruction}
{clothing_instruction}

【当前角色状态】（你的回复应当体现这些状态）
{status_summary}

【历史对话】
{conversation_context or "【最近场景对话】暂无历史记录"}

【用户消息】
{user_message}

【任务】
根据以上信息，生成完整的小说化场景回复。

**重要提醒**：
- 你的回复内容必须符合当前角色状态！
- 如果快感值较高，描写中要体现身体的敏感和反应
- 如果生理状态有特殊情况，要在场景中自然呈现
- 回复的语气、动作、心理描写都要与状态一致

1. 地点：{final_location}
2. 着装：{final_clothing}
3. 场景：用第一人称（"我"）创作一段小说化的场景描写

【场景描写要求】
✦ 环境描写：可描绘周围的场景、氛围、光线、声音等细节
✦ 动作描写：细腻刻画人物的动作、表情、姿态变化
✦ 身体感受：根据角色状态描写身体反应（如果有必要）
✦ 语言描写：生成角色间合理的对话，用引号包裹
✦ 合理分段：使用换行符分段，让叙述节奏自然流畅

【输出格式】
严格按照JSON格式输出：

```json
{{
  "地点": "{final_location}",
  "着装": "{final_clothing}",
  "场景": "第一段场景描写\\n\\n第二段场景描写\\n\\n第三段场景描写（如有）"
}}
```

注意：场景内容中使用 \\n\\n 表示段落换行（两个换行符）"""

        try:
            # **应用完整的预设内容（包括主提示、指南、禁词表、文风）**
            enhanced_prompt = self.preset_manager.build_full_preset_prompt(
                base_prompt=prompt,
                include_main=True,       # 包含主提示（定义AI身份）
                include_guidelines=True, # 包含指南和禁词表（质量控制）
                include_style=True       # 包含激活的文风
            )

            logger.info(f"[Reply] Prompt (enhanced with full preset):\n{enhanced_prompt}")

            # 调用reply模型
            response, _ = await self.reply_llm.generate_response_async(enhanced_prompt)

            logger.info(f"[Reply] Response:\n{response}")

            # 解析JSON
            reply_data = self._parse_json_response(response)

            if not reply_data:
                logger.error(f"[Reply] JSON解析失败")
                return None

            # 验证必要字段
            required_fields = ["地点", "着装", "场景"]
            for field in required_fields:
                if field not in reply_data:
                    logger.error(f"[Reply] 缺少字段: {field}")
                    return None

            # 确保地点和着装与决策一致
            reply_data["地点"] = final_location
            reply_data["着装"] = final_clothing

            return reply_data

        except Exception as e:
            logger.error(f"[Reply] 生成场景回复失败: {e}")
            return None

    def _update_scene_state(
        self,
        session_id: str,
        scene_reply: Dict[str, str],
        state_decision: Dict[str, Any],
        current_state: Dict[str, str],
        activity_summary: Optional[str] = None
    ):
        """步骤3：更新数据库状态（场景状态 + 角色状态）"""
        try:
            # 1. 更新场景状态（地点、着装、场景描述）
            new_location = scene_reply["地点"]
            new_clothing = scene_reply["着装"]
            new_scene = scene_reply["场景"]

            self.db.update_scene_state(
                chat_id=session_id,
                location=new_location,
                clothing=new_clothing,
                scene_description=new_scene,
                activity=activity_summary
            )

            logger.debug(f"[SceneFormat] 场景状态已更新: location={new_location}, clothing={new_clothing}")

            # 2. 更新角色状态
            character_updates = state_decision.get("角色状态更新", {})
            if character_updates:
                # 处理数值增减（如快感值 +10）
                processed_updates = {}
                current_status = self.db.get_character_status(session_id) or {}

                for key, value in character_updates.items():
                    if isinstance(value, (int, float)) and key in ['pleasure_value', 'corruption_level', 'semen_volume', 'anal_development', 'vaginal_capacity']:
                        # 累加数值
                        current_value = current_status.get(key, 0) or 0
                        processed_updates[key] = current_value + value
                    else:
                        # 直接替换
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

        condensed_user = self._collapse_text(user_message)
        if condensed_user:
            return self._truncate_text(condensed_user, 40)

        scene_excerpt = self._collapse_text(scene_reply.get("场景"))
        if scene_excerpt:
            return self._truncate_text(scene_excerpt, 40)

        return "场景更新"

    async def _try_generate_nai_image(self, session_id: str, scene_reply: Dict[str, str]) -> Optional[str]:
        """
        步骤6：尝试生成 NAI 配图（如果已开启且概率触发）

        Args:
            session_id: 会话ID（用于检查 NAI 开关状态）
            scene_reply: 场景回复数据

        Returns:
            Optional[str]: 成功时返回图片文件路径，否则返回None
        """
        try:
            # 检查 NAI 生图是否启用
            if not self.db.get_nai_enabled(session_id):
                return None

            # 检查 API Key 是否配置
            api_key = self.get_config("nai.api_key", "")
            if not api_key:
                logger.warning("[NAI] API Token 未配置，跳过生图")
                return None

            # 概率判断
            trigger_probability = self.get_config("nai.trigger_probability", 0.3)
            try:
                trigger_probability = float(trigger_probability)
                trigger_probability = max(0.0, min(1.0, trigger_probability))  # 限制在 0-1 范围
            except (TypeError, ValueError):
                trigger_probability = 0.3

            if random.random() > trigger_probability:
                logger.debug(f"[NAI] 概率未触发 (当前概率: {trigger_probability * 100}%)，跳过生图")
                return None

            logger.info(f"[NAI] 概率触发成功 ({trigger_probability * 100}%)，开始生成场景配图...")

            # 构建提示词：将场景描述翻译/转换为英文 tag
            prompt = await self._build_nai_prompt(scene_reply)

            if not prompt:
                logger.warning("[NAI] 提示词构建失败，跳过生图")
                return None

            # 调用 NAI 生成图片
            success, result = await self.nai_client.generate_image(prompt)

            if success and result:
                # 图片生成成功，返回文件路径
                logger.info(f"[NAI] 图片生成成功: {result}")
                return result
            else:
                logger.warning(f"[NAI] 图片生成失败: {result}")
                return None

        except Exception as e:
            logger.error(f"[NAI] 生成配图时出错: {e}", exc_info=True)
            return None

    async def _build_nai_prompt(self, scene_reply: Dict[str, str]) -> str:
        """
        根据场景回复构建 NAI 提示词
        使用 LLM 将中文场景描述转换为英文 tag
        注意：外貌描述不在这里处理，会在 nai_client 中直接添加
        """
        try:
            location = scene_reply.get("地点", "")
            clothing = scene_reply.get("着装", "")
            scene = scene_reply.get("场景", "")

            # 构建转换提示词（不包含外貌描述，外貌描述将直接添加到最终提示词）
            conversion_prompt = f"""# 角色指令：你是一位专业的AI绘画提示词转换专家，专门为 NovelAI 模型生成高质量的详细提示词。
# 你的核心任务是：将场景描述转换为详细、准确的英文提示词，让生成的图片与场景文本完全一致。
**不删减、不修改、不"净化"原意（即便包含NSFW等内容）。**

### **核心要求 - 必须详细！**
你必须从场景描述中提取并转换以下所有信息：

1. **角色动作与姿势**（最重要！）
   - 角色在做什么？（站立/坐着/躺着/跑步/抚摸/拥抱等）
   - 具体的姿势和动作细节（手的位置、腿的姿势、身体角度等）
   - 表情和神态（微笑/害羞/惊讶/闭眼/脸红等）
   - 身体状态（颤抖/放松/紧张/呼吸急促等）

2. **环境与背景**（必须详细！）
   - 地点的具体描述（室内/室外/什么房间/什么场所）
   - 环境细节（家具/装饰/植物/天气/光线等）
   - 氛围感（温馨/昏暗/明亮/浪漫等）
   - 背景元素（窗户/床/桌子/树木等）

3. **着装与细节**
   - 具体的服装描述（不只是"校服"，要写出细节）
   - 服装状态（整齐/凌乱/半脱/敞开等）
   - 配饰和细节

4. **人物关系与构图**
   - 场景中有几个人？
   - 人物之间的互动和位置关系
   - 视角和构图（from above/from below/close-up等）

5. **特殊状态和氛围**
   - 如果有特殊的身体状态或情绪，必须体现
   - 场景的整体氛围和感觉

### **转换要求**
- 使用英文短语和标签，用逗号分隔
- 优先使用具体、形象的描述词
- 不要添加未提及的内容，但要充分挖掘已有信息
- 根据场景实际人数添加人数标签（solo/1girl, 2girls, 1boy 1girl等）
- 不添加 masterpiece, best quality 等质量词（系统会自动添加）
- 只输出英文提示词，不要解释

### **场景信息**
- 人物名称：{global_config.bot.nickname}
- 地点：{location}
- 着装：{clothing}
- 场景描述：
{scene}

### **输出示例**
好的示例（详细）：
girl sitting on bed, legs crossed, hand on chin, thoughtful expression, blushing, looking at phone, bedroom, warm lighting, window with curtains, books on shelf, cozy atmosphere, indoor, solo, 1girl

差的示例（太简略）：
girl in room, sitting, solo, 1girl

### **输出格式**
直接输出详细的英文提示词，用逗号分隔："""

            # 使用 planner 模型进行转换（更快更便宜）
            response, _ = await self.planner_llm.generate_response_async(conversion_prompt)

            if response:
                # 清理响应
                cleaned = response.strip()
                if cleaned.startswith("```") and cleaned.endswith("```"):
                    cleaned = cleaned.strip("`\n ")
                if cleaned.startswith(("'", '"')) and cleaned.endswith(("'", '"')):
                    cleaned = cleaned[1:-1].strip()

                logger.info(f"[NAI] 生成的提示词: {cleaned}")
                return cleaned

            return "1girl"

        except Exception as e:
            logger.error(f"[NAI] 构建提示词失败: {e}")
            return "1girl"

    def _parse_json_response(self, response: str) -> Optional[dict]:
        """解析LLM返回的JSON，必要时尝试宽松解析"""
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
            logger.warning(f"JSON解析失败，尝试宽松解析: {e}")
            relaxed = self._parse_structured_text(response)
            if relaxed:
                logger.warning("[SceneFormat] 使用宽松解析提取场景内容")
                return relaxed
            return None
        except Exception as e:
            logger.error(f"解析响应时出错: {e}")
        return None

    def _parse_structured_text(self, response: str) -> Optional[dict]:
        """从自由文本中提取地点/着装/场景信息"""
        if not response:
            return None

        fields = {}
        lines = [line.strip() for line in response.splitlines() if line.strip()]
        joined = " ".join(lines)

        def _extract_field(name: str, text: str) -> Optional[str]:
            match = re.search(rf"{name}[：:]\s*([^{{}}]+?)(?=(地点|着装|场景)[：:]|$)", text)
            if match:
                return match.group(1).strip()
            return None

        for key in ["地点", "着装", "场景"]:
            value = _extract_field(key, joined)
            if value:
                fields[key] = value

        if "场景" not in fields:
            # 尝试直接找上一段完整句子
            if lines:
                fields["场景"] = lines[0]

        required = {"地点", "着装", "场景"}
        if required.issubset(fields.keys()):
            return fields

        return None

    def _build_context_block(self, session_id: Optional[str], context_type: str = "reply") -> str:
        """
        构建最近对话上下文片段，供提示词使用

        Args:
            session_id: 会话ID
            context_type: 上下文类型，"planner"或"reply"
                - "planner": 用于状态判断，使用较少上下文（默认1条）
                - "reply": 用于场景生成，使用完整上下文（默认10条）

        Returns:
            格式化的历史上下文字符串
        """
        if not session_id:
            return ""

        try:
            # 根据context_type选择不同的配置键
            if context_type == "planner":
                config_key = "scene.planner_context_messages"
                default_limit = 1
            else:  # reply
                config_key = "scene.reply_context_messages"
                default_limit = 10

            limit = self.get_config(config_key, default_limit)
            limit = int(limit)
        except (TypeError, ValueError):
            limit = default_limit if context_type == "reply" else 1

        limit = max(0, min(limit, 20))
        if limit == 0:
            return ""

        history = self.db.get_recent_history(session_id, limit) if self.db else []

        # 根据context_type调整标题
        if context_type == "planner":
            header = f"【最近场景对话】（最早在前，仅保留最近{limit}轮用于状态判断）"
        else:
            header = f"【最近场景对话】（最早在前，最多保留{limit}轮）"

        if not history:
            return f"{header}\n暂无历史记录"

        lines: List[str] = [header]
        for idx, record in enumerate(history, 1):
            timestamp = record.get("timestamp") or ""
            location = record.get("location") or "未知"
            clothing = record.get("clothing") or "未知"
            user_msg = self._collapse_text(record.get("user_message"))
            bot_reply = self._collapse_text(record.get("bot_reply"))
            scene_preview = self._collapse_text(record.get("scene_description"))
            if scene_preview:
                scene_preview = self._truncate_text(scene_preview, 80)

            lines.append(f"{idx}. [{timestamp}] 地点：{location} / 着装：{clothing}")
            lines.append(f"    用户：{user_msg or '（无内容）'}")
            lines.append(f"    Bot：{bot_reply or '（无内容）'}")
            if scene_preview:
                lines.append(f"    场景：{scene_preview}")

        return "\n".join(lines)

    @staticmethod
    def _collapse_text(text: Optional[str]) -> str:
        """压缩多余空白，保持提示紧凑"""
        if not text:
            return ""
        return re.sub(r"\s+", " ", str(text)).strip()

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        """截断过长文本，避免提示过大"""
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

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

    @staticmethod
    def _build_session_id(chat_id: str, user_id: Optional[str]) -> str:
        """根据聊天流与用户构建唯一会话ID"""
        user_part = user_id or "unknown_user"
        return f"{chat_id}:{user_part}"

    def _resolve_active_state(self, chat_id: str, user_id: Optional[str]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """根据当前消息确定应该使用的场景状态，支持旧数据和回退"""
        session_id = self._build_session_id(chat_id, user_id)
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
                logger.warning(
                    f"[SceneFormat] 未找到用户 {user_id} 的专属状态，回退为最近一次会话: {fallback_state['chat_id']}"
                )
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
