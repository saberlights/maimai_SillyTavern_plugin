"""
状态管理模块
负责角色状态的验证、更新、衰减和一致性检查
"""
import json
from datetime import datetime
from typing import Dict, Any, Optional, Callable
from src.common.logger import get_logger
from .utils import parse_datetime
from .scene_db import SceneDB

logger = get_logger("state_manager")

# ==================== 常量定义 ====================

# 场景类型定义
SCENE_TYPE_NORMAL = "normal"
SCENE_TYPE_ROMANTIC = "romantic"
SCENE_TYPE_INTIMATE = "intimate"
SCENE_TYPE_EXPLICIT = "explicit"
SCENE_TYPE_REST = "rest"

# 场景类型对应的快感值变化范围 [最小, 最大]
SCENE_TYPE_PLEASURE_RANGES = {
    SCENE_TYPE_NORMAL: (0, 5),
    SCENE_TYPE_ROMANTIC: (5, 20),
    SCENE_TYPE_INTIMATE: (15, 40),
    SCENE_TYPE_EXPLICIT: (30, 60),
    SCENE_TYPE_REST: (-20, -5)
}

# 衰减配置
DECAY_NORMAL = 5
DECAY_ROMANTIC = 0
DECAY_REST = 15
TIME_DECAY_THRESHOLD = 30  # 分钟
TIME_DECAY_AMOUNT = 20

# 状态一致性规则：快感值阈值 -> 最低湿润度
PLEASURE_WETNESS_RULES = [
    (30, "微湿"),
    (50, "湿润"),
    (70, "淫湿"),
    (90, "爱液横流")
]

# 湿润度等级顺序
WETNESS_LEVELS = ["正常", "微湿", "湿润", "淫湿", "爱液横流"]

# 阴道状态等级顺序
VAGINAL_STATES = ["放松", "轻微收缩", "无意识收缩", "紧绷", "痉挛"]


class StateManager:
    """状态管理器"""

    def __init__(self, db: SceneDB):
        self.db = db

    def validate_state_decision(self, decision: Dict[str, Any], current_status: Dict[str, Any]) -> Dict[str, Any]:
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
                    logger.warning(f"[StateManager] 快感值增加过大({value})，限制为60")
                    value = 60
                elif value < -100:
                    logger.warning(f"[StateManager] 快感值减少过大({value})，限制为-100")
                    value = -100

                # 计算新的快感值
                current_pleasure = current_status.get("pleasure_value", 0) or 0
                threshold = current_status.get("pleasure_threshold", 100) or 100
                new_pleasure = current_pleasure + value

                # 如果超过阈值，自动触发高潮并重置
                if new_pleasure >= threshold:
                    logger.info(f"[StateManager] 快感值达到阈值({new_pleasure}/{threshold})，触发高潮重置")
                    value = -current_pleasure + (threshold // 4)
                    validated_updates["physiological_state"] = "高潮后的余韵中颤抖"

                validated_updates["pleasure_value"] = value

        # 2. 污染度验证
        if "corruption_level" in status_updates:
            value = status_updates["corruption_level"]
            if isinstance(value, (int, float)):
                if value > 20:
                    logger.warning(f"[StateManager] 污染度增加过大({value})，限制为20")
                    value = 20
                elif value < 0:
                    value = 0

                current_corruption = current_status.get("corruption_level", 0) or 0
                if current_corruption + value > 100:
                    value = max(0, 100 - current_corruption)

                if value > 0:
                    validated_updates["corruption_level"] = value

        # 3. 体内精液验证
        if "semen_volume" in status_updates:
            value = status_updates["semen_volume"]
            if isinstance(value, (int, float)):
                if value > 150:
                    logger.warning(f"[StateManager] 精液量增加过大({value}ml)，限制为150ml")
                    value = 150
                elif value < -500:
                    value = -500

                current_volume = current_status.get("semen_volume", 0) or 0
                new_volume = current_volume + value
                if new_volume < 0:
                    value = -current_volume
                if new_volume > 500:
                    value = max(0, 500 - current_volume)

                validated_updates["semen_volume"] = value

        # 4. 生理状态验证
        if "physiological_state" in status_updates:
            value = str(status_updates["physiological_state"])
            if len(value) > 100:
                value = value[:100]
            validated_updates["physiological_state"] = value

        # 5. 阴道状态验证
        if "vaginal_state" in status_updates:
            value = str(status_updates["vaginal_state"])
            if value in VAGINAL_STATES:
                validated_updates["vaginal_state"] = value
            else:
                logger.warning(f"[StateManager] 阴道状态值({value})不合法，忽略")

        # 6. 湿润度验证
        if "vaginal_wetness" in status_updates:
            value = str(status_updates["vaginal_wetness"])
            if value in WETNESS_LEVELS:
                current_wetness = current_status.get("vaginal_wetness", "正常")
                current_idx = WETNESS_LEVELS.index(current_wetness) if current_wetness in WETNESS_LEVELS else 0
                new_idx = WETNESS_LEVELS.index(value)
                if abs(new_idx - current_idx) > 2:
                    logger.warning(f"[StateManager] 湿润度变化过大：{current_wetness} → {value}")
                validated_updates["vaginal_wetness"] = value
            else:
                logger.warning(f"[StateManager] 湿润度值({value})不合法，忽略")

        # 7. 怀孕状态验证
        if "pregnancy_status" in status_updates:
            allowed_values = ["未受孕", "受孕中"]
            value = str(status_updates["pregnancy_status"])
            if value in allowed_values:
                current_pregnancy = current_status.get("pregnancy_status", "未受孕")
                if current_pregnancy != value:
                    logger.info(f"[StateManager] 怀孕状态变化: {current_pregnancy} → {value}")
                validated_updates["pregnancy_status"] = value

        # 8. 其他字段
        if "pregnancy_source" in status_updates:
            validated_updates["pregnancy_source"] = str(status_updates["pregnancy_source"])

        if "pregnancy_counter" in status_updates:
            value = status_updates["pregnancy_counter"]
            if isinstance(value, (int, float)):
                validated_updates["pregnancy_counter"] = int(value)

        # JSON 字段处理
        for field in ["semen_sources", "vaginal_foreign", "inventory"]:
            if field in status_updates:
                value = status_updates[field]
                if isinstance(value, list):
                    validated_updates[field] = json.dumps(value, ensure_ascii=False)
                elif isinstance(value, str):
                    validated_updates[field] = value

        for field in ["fetishes", "permanent_mods", "body_condition"]:
            if field in status_updates:
                value = status_updates[field]
                if isinstance(value, dict):
                    if field == "permanent_mods" and value:
                        logger.info(f"[StateManager] 永久性改造更新: {list(value.keys())}")
                    validated_updates[field] = json.dumps(value, ensure_ascii=False)
                elif isinstance(value, str):
                    validated_updates[field] = value

        # 9. 后穴开发度验证
        if "anal_development" in status_updates:
            value = status_updates["anal_development"]
            if isinstance(value, (int, float)):
                if value > 20:
                    value = 20
                elif value < -100:
                    value = -100
                current_development = current_status.get("anal_development", 0) or 0
                new_development = current_development + value
                if new_development < 0:
                    value = -current_development
                elif new_development > 100:
                    value = max(0, 100 - current_development)
                validated_updates["anal_development"] = value

        # 10. 阴道容量验证
        if "vaginal_capacity" in status_updates:
            value = status_updates["vaginal_capacity"]
            if isinstance(value, (int, float)):
                if value > 40:
                    value = 40
                elif value < -100:
                    value = -100
                current_capacity = current_status.get("vaginal_capacity", 100) or 100
                new_capacity = current_capacity + value
                if new_capacity < 50:
                    value = max(-current_capacity, 50 - current_capacity)
                elif new_capacity > 300:
                    value = min(value, 300 - current_capacity)
                validated_updates["vaginal_capacity"] = value

        decision["角色状态更新"] = validated_updates

        if validated_updates:
            logger.info(f"[StateManager] 状态验证通过: {list(validated_updates.keys())}")

        return decision

    def apply_state_updates_preview(self, current_status: Dict[str, Any], state_decision: Dict[str, Any]) -> Dict[str, Any]:
        """应用状态更新，生成临时预览状态（供 reply 使用）"""
        preview_status = dict(current_status)
        status_updates = state_decision.get("角色状态更新", {})

        if not status_updates:
            return preview_status

        numeric_fields = ['pleasure_value', 'corruption_level', 'semen_volume', 'anal_development', 'vaginal_capacity']
        json_fields = ['semen_sources', 'vaginal_foreign', 'inventory', 'fetishes', 'permanent_mods', 'body_condition']

        for key, value in status_updates.items():
            if isinstance(value, (int, float)) and key in numeric_fields:
                current_value = preview_status.get(key, 0) or 0
                preview_status[key] = current_value + value
            elif key in json_fields:
                preview_status[key] = value
            else:
                preview_status[key] = value

        logger.debug(f"[StateManager] 应用状态更新预览: {list(status_updates.keys())}")
        return preview_status

    def apply_time_decay(self, session_id: str, current_state: Dict[str, Any], character_status: Dict[str, Any]) -> bool:
        """应用时间衰减：如果距离上次互动超过阈值，衰减快感值"""
        last_update_str = current_state.get("last_update_time")
        last_update = parse_datetime(last_update_str)

        if not last_update:
            return False

        try:
            time_diff_minutes = (datetime.now() - last_update).total_seconds() / 60

            if time_diff_minutes > TIME_DECAY_THRESHOLD:
                current_pleasure = character_status.get("pleasure_value", 0) or 0
                if current_pleasure > 0:
                    decay_amount = min(current_pleasure, TIME_DECAY_AMOUNT)
                    new_pleasure = max(0, current_pleasure - decay_amount)
                    self.db.update_character_status(session_id, {"pleasure_value": new_pleasure})
                    logger.info(f"[StateManager] 时间衰减: 快感值 {current_pleasure} → {new_pleasure}")
                    return True
        except Exception as e:
            logger.warning(f"[StateManager] 时间衰减计算失败: {e}")

        return False

    def apply_scene_decay(self, state_decision: Dict[str, Any], scene_type: str, character_status: Dict[str, Any]) -> Dict[str, Any]:
        """根据场景类型应用衰减"""
        status_updates = state_decision.get("角色状态更新", {})
        current_pleasure = character_status.get("pleasure_value", 0) or 0

        # 如果 planner 已经判断了快感值变化，验证是否在合理范围内
        if "pleasure_value" in status_updates:
            pleasure_delta = status_updates["pleasure_value"]
            if isinstance(pleasure_delta, (int, float)):
                min_range, max_range = SCENE_TYPE_PLEASURE_RANGES.get(scene_type, (0, 60))
                if pleasure_delta > 0 and pleasure_delta > max_range:
                    logger.warning(f"[StateManager] 快感值增加({pleasure_delta})超出场景类型({scene_type})范围，限制为{max_range}")
                    status_updates["pleasure_value"] = max_range
                    state_decision["角色状态更新"] = status_updates
            return state_decision

        # 根据场景类型决定衰减
        decay = 0
        if scene_type == SCENE_TYPE_NORMAL and current_pleasure > 0:
            decay = DECAY_NORMAL
        elif scene_type == SCENE_TYPE_REST and current_pleasure > 0:
            decay = DECAY_REST
        elif scene_type == SCENE_TYPE_ROMANTIC:
            decay = DECAY_ROMANTIC
        elif scene_type in [SCENE_TYPE_INTIMATE, SCENE_TYPE_EXPLICIT]:
            decay = 0

        if decay > 0 and current_pleasure > 0:
            actual_decay = min(decay, current_pleasure)
            status_updates["pleasure_value"] = -actual_decay
            state_decision["角色状态更新"] = status_updates
            logger.debug(f"[StateManager] 场景衰减({scene_type}): 快感值 -{actual_decay}")

        return state_decision

    def ensure_status_consistency(self, state_decision: Dict[str, Any], current_status: Dict[str, Any], scene_type: str = SCENE_TYPE_NORMAL) -> Dict[str, Any]:
        """确保状态之间的逻辑一致性"""
        status_updates = state_decision.get("角色状态更新", {})

        # 计算最终快感值
        current_pleasure = current_status.get("pleasure_value", 0) or 0
        pleasure_delta = status_updates.get("pleasure_value", 0)
        if isinstance(pleasure_delta, (int, float)):
            final_pleasure = current_pleasure + pleasure_delta
        else:
            final_pleasure = current_pleasure
        final_pleasure = max(0, final_pleasure)

        # 获取当前/更新后的湿润度
        current_wetness = current_status.get("vaginal_wetness", "正常")
        final_wetness = status_updates.get("vaginal_wetness", current_wetness)
        current_wetness_idx = WETNESS_LEVELS.index(final_wetness) if final_wetness in WETNESS_LEVELS else 0

        # 规则1：快感值与湿润度关联（提升）
        wetness_adjusted = False
        for threshold, min_wetness in PLEASURE_WETNESS_RULES:
            if final_pleasure >= threshold:
                min_wetness_idx = WETNESS_LEVELS.index(min_wetness) if min_wetness in WETNESS_LEVELS else 0
                if current_wetness_idx < min_wetness_idx:
                    status_updates["vaginal_wetness"] = min_wetness
                    logger.info(f"[StateManager] 一致性修正: 快感值{final_pleasure}，湿润度调整为{min_wetness}")
                    wetness_adjusted = True
                break

        # 规则1.5：快感值下降时湿润度也应逐渐降低
        if not wetness_adjusted and pleasure_delta < -10 and current_wetness_idx > 0:
            if final_pleasure < 30 and current_wetness_idx >= 2:
                new_wetness_idx = max(0, current_wetness_idx - 1)
                status_updates["vaginal_wetness"] = WETNESS_LEVELS[new_wetness_idx]
                logger.info(f"[StateManager] 一致性修正: 快感值下降，湿润度调整为{WETNESS_LEVELS[new_wetness_idx]}")

        # 规则2：高快感值时自动更新生理状态
        updated_physio = status_updates.get("physiological_state", "")
        current_physio = current_status.get("physiological_state", "")
        is_orgasm_state = any(keyword in updated_physio or keyword in current_physio
                             for keyword in ["高潮", "余韵", "颤抖", "痉挛"])

        if "physiological_state" not in status_updates and scene_type != SCENE_TYPE_REST and not is_orgasm_state:
            if final_pleasure >= 80:
                status_updates["physiological_state"] = "呼吸急促，身体剧烈颤抖"
            elif final_pleasure >= 60:
                status_updates["physiological_state"] = "呼吸急促，身体微微发热"
            elif final_pleasure >= 40:
                status_updates["physiological_state"] = "呼吸略微急促"

        state_decision["角色状态更新"] = status_updates
        return state_decision
