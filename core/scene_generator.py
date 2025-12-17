"""
场景生成模块
负责调用 LLM 生成场景内容和状态判断
"""
import re
from typing import Dict, Any, Optional, Tuple, Callable
from src.config.config import global_config
from src.common.logger import get_logger
from .utils import parse_json_response, normalize_planner_decision, get_default_decision
from .state_manager import (
    SCENE_TYPE_NORMAL, SCENE_TYPE_ROMANTIC, SCENE_TYPE_INTIMATE,
    SCENE_TYPE_EXPLICIT, SCENE_TYPE_REST
)

logger = get_logger("scene_generator")


class SceneGenerator:
    """场景生成器"""

    def __init__(self, planner_llm, reply_llm, preset_manager, status_formatter):
        self.planner_llm = planner_llm
        self.reply_llm = reply_llm
        self.preset_manager = preset_manager
        self.status_formatter = status_formatter

    async def plan_state_changes(
        self,
        user_message: str,
        current_location: str,
        current_clothing: str,
        last_scene: str,
        character_status: Dict[str, Any],
        conversation_context: str = "",
        scene_type: str = SCENE_TYPE_NORMAL
    ) -> Dict[str, Any]:
        """使用planner模型判断所有状态变化"""
        bot_name = global_config.bot.nickname
        bot_personality = getattr(global_config.personality, "personality", "")
        bot_reply_style = getattr(global_config.personality, "reply_style", "")

        status_summary = self.status_formatter.build_status_summary(character_status, compact=False)
        scene_type_guidance = self._get_scene_type_guidance(scene_type)

        prompt = self._build_planner_prompt(
            bot_name, bot_personality, bot_reply_style,
            current_location, current_clothing, last_scene,
            status_summary, conversation_context, user_message,
            scene_type_guidance
        )

        try:
            logger.info(f"[Planner] Prompt:\n{prompt}")
            response, _ = await self.planner_llm.generate_response_async(prompt)
            logger.info(f"[Planner] Response:\n{response}")

            decision = parse_json_response(response)
            if not decision:
                return get_default_decision()

            decision = normalize_planner_decision(decision)
            decision.setdefault("地点变化", False)
            decision.setdefault("新地点", "")
            decision.setdefault("着装变化", False)
            decision.setdefault("新着装", "")
            decision.setdefault("角色状态更新", {})
            decision["新地点"] = self._normalize_scene_field(decision.get("新地点", ""))
            decision["新着装"] = self._normalize_scene_field(decision.get("新着装", ""))

            return decision

        except Exception as e:
            logger.error(f"[Planner] 状态决策失败: {e}")
            return get_default_decision()

    async def generate_scene_reply(
        self,
        user_message: str,
        current_location: str,
        current_clothing: str,
        last_scene: str,
        character_status: Dict[str, Any],
        state_decision: Dict[str, Any],
        conversation_context: str = ""
    ) -> Optional[Dict[str, str]]:
        """使用reply模型生成场景描述和回复"""
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

        status_summary = self.status_formatter.build_status_summary(character_status, compact=False)

        # 构建完整的创作任务
        task_info = self._build_reply_task(
            bot_name, bot_personality, bot_reply_style,
            location_instruction, clothing_instruction,
            final_location, final_clothing,
            status_summary, conversation_context, user_message
        )

        try:
            # 使用新的结构化 prompt 构建方式
            enhanced_prompt = self.preset_manager.build_structured_prompt(
                task_info=task_info,
                include_nsfw=True,
                include_summary=True,
                include_tucao=True,
                include_cot=True  # 启用轻量思维链
            )

            logger.info(f"[Reply] Prompt (enhanced):\n{enhanced_prompt}")
            response, _ = await self.reply_llm.generate_response_async(enhanced_prompt)
            logger.info(f"[Reply] Response:\n{response}")

            reply_data = parse_json_response(response)
            if not reply_data:
                logger.error(f"[Reply] JSON解析失败")
                return None

            required_fields = ["地点", "着装", "场景"]
            for field in required_fields:
                if field not in reply_data:
                    logger.error(f"[Reply] 缺少字段: {field}")
                    return None

            reply_location = self._normalize_scene_field(reply_data.get("地点", ""))
            reply_clothing = self._normalize_scene_field(reply_data.get("着装", ""))

            final_location = self._normalize_scene_field(final_location)
            final_clothing = self._normalize_scene_field(final_clothing)

            if reply_location and reply_location != final_location:
                logger.info("[Reply] 模型输出地点与状态决策不一致，采用模型结果")
                final_location = reply_location
                state_decision["地点变化"] = True
                state_decision["新地点"] = final_location

            if reply_clothing and reply_clothing != final_clothing:
                logger.info("[Reply] 模型输出着装与状态决策不一致，采用模型结果")
                final_clothing = reply_clothing
                state_decision["着装变化"] = True
                state_decision["新着装"] = final_clothing

            reply_data["地点"] = final_location
            reply_data["着装"] = final_clothing

            return reply_data

        except Exception as e:
            logger.error(f"[Reply] 生成场景回复失败: {e}")
            return None

    async def single_model_generate(
        self,
        user_message: str,
        current_location: str,
        current_clothing: str,
        last_scene: str,
        character_status: Dict[str, Any],
        conversation_context: str = "",
        scene_type: str = SCENE_TYPE_NORMAL
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, str]]]:
        """单模型模式：一次调用同时处理状态判断和场景生成"""
        bot_name = global_config.bot.nickname
        bot_personality = getattr(global_config.personality, "personality", "")
        bot_reply_style = getattr(global_config.personality, "reply_style", "")

        status_summary = self.status_formatter.build_status_summary(character_status, compact=True)
        scene_type_detail = self._get_scene_type_detail(scene_type)

        # 构建完整的创作任务
        task_info = self._build_single_model_task(
            bot_name, bot_personality, bot_reply_style,
            current_location, current_clothing, status_summary,
            conversation_context, user_message, scene_type_detail
        )

        try:
            # 使用新的结构化 prompt 构建方式
            enhanced_prompt = self.preset_manager.build_structured_prompt(
                task_info=task_info,
                include_nsfw=True,
                include_summary=True,
                include_tucao=True,
                include_cot=True  # 启用轻量思维链
            )

            logger.info(f"[SingleModel] Prompt:\n{enhanced_prompt}")
            response, _ = await self.reply_llm.generate_response_async(enhanced_prompt)
            logger.info(f"[SingleModel] Response:\n{response}")

            result = parse_json_response(response)
            if not result:
                logger.error("[SingleModel] JSON解析失败")
                return get_default_decision(), None

            # 标准化结果，处理布尔字符串等
            result = normalize_planner_decision(result)

            state_decision = {
                "地点变化": result.get("地点变化", False),
                "新地点": self._normalize_scene_field(result.get("新地点", "")),
                "着装变化": result.get("着装变化", False),
                "新着装": self._normalize_scene_field(result.get("新着装", "")),
                "角色状态更新": result.get("角色状态更新", {})
            }

            # 根据状态决策确定最终地点和着装
            if state_decision["地点变化"] and state_decision["新地点"]:
                final_location = state_decision["新地点"]
            else:
                final_location = self._normalize_scene_field(result.get("地点", current_location))

            if state_decision["着装变化"] and state_decision["新着装"]:
                final_clothing = state_decision["新着装"]
            else:
                final_clothing = self._normalize_scene_field(result.get("着装", current_clothing))

            scene_reply = {
                "地点": final_location,
                "着装": final_clothing,
                "场景": result.get("场景", "")
            }

            # 透传智能配图相关字段，单模型模式也能触发生图逻辑
            if "建议配图" in result:
                scene_reply["建议配图"] = result.get("建议配图")
            if "nai_prompt" in result:
                scene_reply["nai_prompt"] = result.get("nai_prompt", "")

            if not scene_reply["场景"]:
                logger.error("[SingleModel] 场景内容为空")
                return state_decision, None

            return state_decision, scene_reply

        except Exception as e:
            logger.error(f"[SingleModel] 生成失败: {e}", exc_info=True)
            return get_default_decision(), None

    def _get_scene_type_guidance(self, scene_type: str) -> str:
        """获取场景类型判断提示"""
        guidance_map = {
            SCENE_TYPE_ROMANTIC: """
【系统识别场景类型：浪漫互动】
建议快感值变化范围：+5~20
湿润度可能从"正常"变为"微湿"
生理状态应体现温馨、心跳加速等""",
            SCENE_TYPE_INTIMATE: """
【系统识别场景类型：亲密接触】
建议快感值变化范围：+15~40
湿润度应有明显提升
生理状态应体现身体反应""",
            SCENE_TYPE_EXPLICIT: """
【系统识别场景类型：明确性行为】
建议快感值变化范围：+30~60
湿润度、阴道状态等应有显著变化
需要综合更新多项状态""",
            SCENE_TYPE_REST: """
【系统识别场景类型：休息恢复】
状态应趋于平静和恢复
快感值不应增加""",
            SCENE_TYPE_NORMAL: """
【系统识别场景类型：普通对话】
除非对话内容涉及心理唤起，否则角色状态更新应为空 {}"""
        }
        return guidance_map.get(scene_type, guidance_map[SCENE_TYPE_NORMAL])

    def _get_scene_type_detail(self, scene_type: str) -> str:
        """获取场景类型详细提示（单模型模式）"""
        detail_map = {
            SCENE_TYPE_ROMANTIC: """【系统识别：浪漫互动场景】
建议快感值+5~20，湿润度可能变为"微湿"，生理状态体现温馨感""",
            SCENE_TYPE_INTIMATE: """【系统识别：亲密接触场景】
建议快感值+15~40，湿润度应提升，生理状态体现身体反应""",
            SCENE_TYPE_EXPLICIT: """【系统识别：明确性行为场景】
建议快感值+30~60，多项状态应有显著变化""",
            SCENE_TYPE_REST: """【系统识别：休息恢复场景】
状态应趋于平静，快感值不应增加""",
            SCENE_TYPE_NORMAL: """【系统识别：普通对话场景】
除非涉及心理唤起，否则角色状态更新应为空"""
        }
        return detail_map.get(scene_type, detail_map[SCENE_TYPE_NORMAL])

    def _build_planner_prompt(self, bot_name, bot_personality, bot_reply_style,
                               current_location, current_clothing, last_scene,
                               status_summary, conversation_context, user_message,
                               scene_type_guidance) -> str:
        """构建 Planner 模型的 prompt"""
        return f"""【你的身份】
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
{scene_type_guidance}

【任务】
根据用户消息和场景类型，判断哪些状态需要改变。

【核心判断原则】
1. 真实性：符合真实的生理和心理反应
2. 渐进性：状态变化要渐进，不要突然跳跃
3. 场景匹配：根据场景强度合理判断数值变化
4. 有互动就有反应：不要过度保守，但也不要过度敏感

【状态字段说明】
- pleasure_value: 快感值增量，单次上限60
- vaginal_wetness: "正常"→"微湿"→"湿润"→"淫湿"→"爱液横流"
- vaginal_state: "放松"/"轻微收缩"/"无意识收缩"/"紧绷"/"痉挛"
- physiological_state: 生理状态描述
- corruption_level: 污染度增量，仅腐化事件时增加，上限20
- semen_volume: 体内精液增量(ml)
- anal_development: 后穴开发度增量，上限20

【输出格式】
严格按照JSON格式输出：

```json
{{
  "地点变化": false,
  "新地点": "",
  "着装变化": false,
  "新着装": "",
  "角色状态更新": {{
    // 只输出需要变化的字段
    // 数值字段输出增量
  }}
}}
```

【重要提醒】
- 普通日常对话 → "角色状态更新" 必须为 {{}}
- 数值字段输出增量，不是最终值"""

    def _build_reply_task(self, bot_name, bot_personality, bot_reply_style,
                          location_instruction, clothing_instruction,
                          final_location, final_clothing,
                          status_summary, conversation_context, user_message) -> str:
        """构建双模型模式 Reply 的完整创作任务"""
        return f"""=== 创作任务 ===

【要创作的角色】
角色名：{bot_name}

【角色性格特质】
{bot_personality}

【角色回复风格】
{bot_reply_style}

【当前场景状态】
{location_instruction}
{clothing_instruction}

【角色身体/心理状态】（创作时必须体现这些状态）
{status_summary}

【历史对话】
{conversation_context or "暂无历史记录"}

【用户消息】
{user_message}

【任务】
根据以上信息，生成完整的小说化场景回复。

**重要提醒**：
- 你的回复内容必须符合当前角色状态！
- 如果快感值较高，描写中要体现身体的敏感和反应
- 回复的语气、动作、心理描写都要与状态一致

1. 地点：{final_location}
2. 着装：{final_clothing}
3. 场景：创作一段小说化的场景描写

【智能配图】
判断标准：这个场景画成图，和之前会有明显不同吗？
✅ 需要配图：
  - 姿势/动作变化（站→坐→躺、拥抱、亲吻等）
  - 表情明显变化（脸红、害羞、沉醉、高潮表情）
  - 服装状态变化（脱衣、衣衫不整、换装）
  - 新环境首次出现
  - 身体反应明显（颤抖、出汗、潮红）
❌ 不需要配图：
  - 纯对话（只有说话，没有动作）
  - 内心独白/心理描写
  - 和上一个场景画面差不多
  - 过渡性描写

nai_prompt 要求（建议配图=true时必填）：
  * 英文短语，逗号分隔
  * 必须包含：人数(1girl)、具体姿势、表情、当前服装状态、环境
  * 根据角色状态：快感高→flushed/panting/trembling/ahegao，出汗→sweating
  * 不加质量词

【输出格式】
严格按照JSON格式输出：

```json
{{
  "地点": "{final_location}",
  "着装": "{final_clothing}",
  "场景": "第一段场景描写\\n\\n第二段场景描写\\n\\n第三段场景描写（如有）",
  "建议配图": ,
  "nai_prompt": ""
}}
```"""

    def _build_single_model_task(self, bot_name, bot_personality, bot_reply_style,
                                  current_location, current_clothing, status_summary,
                                  conversation_context, user_message, scene_type_detail) -> str:
        """构建单模型模式的完整创作任务"""
        return f"""=== 创作任务 ===

【要创作的角色】
角色名：{bot_name}

【角色性格特质】
{bot_personality}

【角色回复风格】
{bot_reply_style}

【当前场景状态】
地点：{current_location}
着装：{current_clothing}

【角色身体/心理状态】（创作时必须体现这些状态）
{status_summary}

【历史对话】
{conversation_context or "暂无历史记录"}

【用户消息】
{user_message}

{scene_type_detail}

【任务】
同时完成三件事：
1. 判断状态变化（地点、着装、角色状态）
2. 生成小说化的场景描写
3. 判断是否需要配图

【状态字段说明】
- pleasure_value: 快感值增量，单次上限60
- vaginal_wetness: "正常"→"微湿"→"湿润"→"淫湿"→"爱液横流"
- vaginal_state: "放松"/"轻微收缩"/"无意识收缩"/"紧绷"/"痉挛"
- physiological_state: 生理状态描述
- corruption_level: 污染度增量，仅腐化事件时增加，上限20
- semen_volume: 体内精液增量(ml)
- anal_development: 后穴开发度增量，上限20

**重要提醒**：
- 普通对话时角色状态更新必须为空 {{}}
- 状态变化要与场景描写一致

【智能配图】
判断标准：这个场景画成图，和之前会有明显不同吗？
✅ 需要配图：
  - 姿势/动作变化（站→坐→躺、拥抱、亲吻等）
  - 表情明显变化（脸红、害羞、沉醉、高潮表情）
  - 服装状态变化（脱衣、衣衫不整、换装）
  - 新环境首次出现
  - 身体反应明显（颤抖、出汗、潮红）
❌ 不需要配图：
  - 纯对话（只有说话，没有动作）
  - 内心独白/心理描写
  - 和上一个场景画面差不多
  - 过渡性描写

nai_prompt 要求（建议配图=true时必填）：
  * 英文短语，逗号分隔
  * 必须包含：人数(1girl)、具体姿势、表情、当前服装状态、环境
  * 根据角色状态：快感高→flushed/panting/trembling/ahegao，出汗→sweating
  * 不加质量词

【输出格式】严格JSON：
```json
{{
  "地点变化": false,
  "新地点": "",
  "着装变化": false,
  "新着装": "",
  "角色状态更新": {{}},
  "地点": "{current_location}",
  "着装": "{current_clothing}",
  "场景": "场景描写内容...",
  "建议配图": ,
  "nai_prompt": ""
}}
```"""

    @staticmethod
    def _normalize_scene_field(value: Optional[str]) -> str:
        """清理地点/着装等短文本中的异常空格"""
        if not value:
            return ""
        text = str(value).replace("\u3000", " ").strip()
        if not text:
            return ""
        # 去除汉字之间的空格
        text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
        # 再折叠其它空白
        text = re.sub(r"\s+", " ", text).strip()
        return text
