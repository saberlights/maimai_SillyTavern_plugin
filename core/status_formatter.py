"""
状态格式化模块
负责角色状态的显示格式化
"""
from typing import Dict, Any, Callable
from .utils import parse_status_json_fields


class StatusFormatter:
    """状态格式化器"""

    def __init__(self, get_config: Callable):
        self.get_config = get_config

    def build_status_summary(self, character_status: Dict[str, Any], compact: bool = False) -> str:
        """
        构建角色状态摘要

        Args:
            character_status: 角色状态字典
            compact: 是否使用紧凑格式

        Returns:
            格式化的状态摘要字符串
        """
        parsed = parse_status_json_fields(character_status)

        if compact:
            return (
                f"生理: {character_status.get('physiological_state', '呼吸平稳')} | "
                f"阴道: {character_status.get('vaginal_state', '放松')} | "
                f"湿润: {character_status.get('vaginal_wetness', '正常')} | "
                f"快感: {character_status.get('pleasure_value', 0)}/{character_status.get('pleasure_threshold', 100)} | "
                f"污染: {character_status.get('corruption_level', 0)}"
            )

        # 完整格式
        status_lines = [
            f"生理状态: {character_status.get('physiological_state', '呼吸平稳')}",
            f"阴道状态: {character_status.get('vaginal_state', '放松')}",
            f"湿润度: {character_status.get('vaginal_wetness', '正常')}",
            f"快感值: {character_status.get('pleasure_value', 0)}/{character_status.get('pleasure_threshold', 100)}",
            f"污染度: {character_status.get('corruption_level', 0)}",
            f"怀孕状态: {character_status.get('pregnancy_status', '未受孕')}",
            f"体内精液: {character_status.get('semen_volume', 0)}ml",
            f"当前道具: {', '.join(parsed['inventory']) if parsed['inventory'] else '无'}"
        ]

        # 条件显示字段
        semen_volume = character_status.get('semen_volume', 0)
        if semen_volume > 0 and parsed['semen_sources']:
            status_lines.append(f"精液来源: {', '.join(parsed['semen_sources'])}")

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

        if parsed['vaginal_foreign']:
            status_lines.append(f"阴道内异物: {', '.join(parsed['vaginal_foreign'])}")

        if parsed['permanent_mods']:
            mods_text = ", ".join([f"{k}({v})" for k, v in parsed['permanent_mods'].items()])
            status_lines.append(f"永久改造: {mods_text}")

        if parsed['body_condition']:
            condition_text = ", ".join([f"{k}:{v}" for k, v in parsed['body_condition'].items()])
            status_lines.append(f"部位状况: {condition_text}")

        if parsed['fetishes']:
            fetishes_text = ", ".join([
                f"{name}Lv{data.get('等级', 0)}({data.get('经验', 0)}exp)" if isinstance(data, dict) else f"{name}"
                for name, data in parsed['fetishes'].items()
            ])
            status_lines.append(f"已有性癖: {fetishes_text}")

        return "\n".join(status_lines)

    def format_status_changes(self, original_status: Dict[str, Any], final_status: Dict[str, Any], state_decision: Dict[str, Any]) -> str:
        """格式化状态变化提示"""
        if not self.get_config("scene.status_changes.enabled", True):
            return ""

        changes = []
        format_type = self.get_config("scene.status_changes.format", "detailed")

        # 快感值变化
        old_pleasure = original_status.get("pleasure_value", 0) or 0
        new_pleasure = final_status.get("pleasure_value", 0) or 0
        if old_pleasure != new_pleasure:
            delta = new_pleasure - old_pleasure
            if format_type == "detailed":
                threshold = final_status.get("pleasure_threshold", 100) or 100
                changes.append(f"快感值: {old_pleasure} → {new_pleasure}/{threshold} ({'+' if delta > 0 else ''}{delta})")
            else:
                changes.append(f"快感值 {'+' if delta > 0 else ''}{delta}")

        # 湿润度变化
        old_wetness = original_status.get("vaginal_wetness", "正常")
        new_wetness = final_status.get("vaginal_wetness", "正常")
        if old_wetness != new_wetness:
            if format_type == "detailed":
                changes.append(f"湿润度: {old_wetness} → {new_wetness}")
            else:
                changes.append(f"湿润度 → {new_wetness}")

        # 污染度变化
        old_corruption = original_status.get("corruption_level", 0) or 0
        new_corruption = final_status.get("corruption_level", 0) or 0
        if old_corruption != new_corruption:
            delta = new_corruption - old_corruption
            if format_type == "detailed":
                changes.append(f"污染度: {old_corruption} → {new_corruption} (+{delta})")
            else:
                changes.append(f"污染度 +{delta}")

        # 生理状态变化
        old_physio = original_status.get("physiological_state", "呼吸平稳")
        new_physio = final_status.get("physiological_state", "呼吸平稳")
        if old_physio != new_physio:
            if format_type == "detailed":
                changes.append(f"生理: {new_physio}")
            else:
                changes.append(f"生理变化")

        # 阴道状态变化
        old_vaginal = original_status.get("vaginal_state", "放松")
        new_vaginal = final_status.get("vaginal_state", "放松")
        if old_vaginal != new_vaginal:
            if format_type == "detailed":
                changes.append(f"阴道: {old_vaginal} → {new_vaginal}")
            else:
                changes.append(f"阴道 → {new_vaginal}")

        # 精液量变化
        old_semen = original_status.get("semen_volume", 0) or 0
        new_semen = final_status.get("semen_volume", 0) or 0
        if old_semen != new_semen:
            delta = new_semen - old_semen
            if format_type == "detailed":
                changes.append(f"精液: {old_semen}ml → {new_semen}ml ({'+' if delta > 0 else ''}{delta}ml)")
            else:
                changes.append(f"精液 {'+' if delta > 0 else ''}{delta}ml")

        # 怀孕状态变化
        old_pregnancy = original_status.get("pregnancy_status", "未受孕")
        new_pregnancy = final_status.get("pregnancy_status", "未受孕")
        if old_pregnancy != new_pregnancy:
            if format_type == "detailed":
                if new_pregnancy == "受孕中":
                    pregnancy_source = final_status.get("pregnancy_source", "未知")
                    changes.append(f"怀孕: {old_pregnancy} → {new_pregnancy} ({pregnancy_source})")
                else:
                    changes.append(f"怀孕: {old_pregnancy} → {new_pregnancy}")
            else:
                changes.append(f"怀孕 → {new_pregnancy}")

        if not changes:
            return ""

        return "📊 " + " | ".join(changes)

    def format_status_bar(self, status: Dict[str, Any]) -> str:
        """格式化状态栏显示"""
        if not self.get_config("scene.status_bar.enabled", True):
            return ""

        display_mode = self.get_config("scene.status_bar.display_mode", "compact")
        use_progress_bar = self.get_config("scene.status_bar.use_progress_bar", True)

        pleasure = status.get("pleasure_value", 0) or 0
        threshold = status.get("pleasure_threshold", 100) or 100
        wetness = status.get("vaginal_wetness", "正常")
        corruption = status.get("corruption_level", 0) or 0
        physio = status.get("physiological_state", "呼吸平稳")
        semen = status.get("semen_volume", 0) or 0
        pregnancy = status.get("pregnancy_status", "未受孕")
        vaginal = status.get("vaginal_state", "放松")

        # 根据快感值选择心形图标
        heart_icon = self._get_heart_icon(pleasure, threshold)

        if display_mode == "compact":
            parts = []
            if use_progress_bar:
                pleasure_bar = self._make_progress_bar(pleasure, threshold, 10)
                parts.append(f"{heart_icon} {pleasure_bar} {pleasure}/{threshold}")
            else:
                parts.append(f"{heart_icon} {pleasure}/{threshold}")

            # 湿润度图标根据程度变化
            wetness_icon = self._get_wetness_icon(wetness)
            parts.append(f"{wetness_icon} {wetness}")

            # 生理状态截取更多字符
            parts.append(f"🌡️ {physio[:12]}")

            if semen > 0:
                parts.append(f"💦 {semen}ml")

            if pregnancy == "受孕中":
                parts.append(f"🤰 受孕中")

            return f"┈┈ 状态 ┈┈\n" + " | ".join(parts)

        elif display_mode == "full":
            lines = ["╭───── 角色状态 ─────╮"]

            if use_progress_bar:
                pleasure_bar = self._make_progress_bar(pleasure, threshold, 12)
                lines.append(f"│ {heart_icon} 快感: {pleasure_bar} {pleasure}/{threshold}")
            else:
                lines.append(f"│ {heart_icon} 快感: {pleasure}/{threshold}")

            wetness_icon = self._get_wetness_icon(wetness)
            lines.append(f"│ {wetness_icon} 湿润: {wetness}")
            lines.append(f"│ 😈 污染: {corruption}")
            lines.append(f"│ 🌡️ 生理: {physio}")
            lines.append(f"│ 🔮 阴道: {vaginal}")

            if semen > 0:
                lines.append(f"│ 💦 精液: {semen}ml")

            if pregnancy == "受孕中":
                pregnancy_source = status.get("pregnancy_source", "未知")
                pregnancy_counter = status.get("pregnancy_counter", 0)
                lines.append(f"│ 🤰 怀孕: {pregnancy_counter}天 ({pregnancy_source})")

            lines.append("╰────────────────────╯")
            return "\n".join(lines)

        elif display_mode == "changes_only":
            return ""

        return ""

    @staticmethod
    def _get_heart_icon(pleasure: int, threshold: int) -> str:
        """根据快感值返回不同的心形图标"""
        ratio = pleasure / threshold if threshold > 0 else 0
        if ratio >= 0.9:
            return "💗"  # 跳动的心
        elif ratio >= 0.7:
            return "💕"  # 双心
        elif ratio >= 0.5:
            return "❤️"  # 红心
        elif ratio >= 0.3:
            return "🩷"  # 粉心
        else:
            return "🤍"  # 白心

    @staticmethod
    def _get_wetness_icon(wetness: str) -> str:
        """根据湿润度返回不同的图标"""
        wetness_icons = {
            "正常": "💧",
            "微湿": "💧",
            "湿润": "💦",
            "淫湿": "💦",
            "爱液横流": "🌊"
        }
        return wetness_icons.get(wetness, "💧")

    @staticmethod
    def _make_progress_bar(value: int, max_value: int, length: int = 10) -> str:
        """生成进度条"""
        if max_value <= 0:
            max_value = 100
        ratio = min(1.0, max(0.0, value / max_value))
        filled = int(ratio * length)
        empty = length - filled
        return "█" * filled + "░" * empty
