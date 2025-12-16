"""
上下文构建模块
负责构建对话历史上下文
"""
from typing import Dict, Any, List, Optional, Callable
from .utils import collapse_text, truncate_text
from .scene_db import SceneDB


class ContextBuilder:
    """上下文构建器"""

    def __init__(self, db: SceneDB, get_config: Callable):
        self.db = db
        self.get_config = get_config

    def build_context_block(self, session_id: Optional[str], context_type: str = "reply") -> str:
        """
        构建最近对话上下文片段，供提示词使用

        Args:
            session_id: 会话ID
            context_type: 上下文类型，"planner"或"reply"

        Returns:
            格式化的历史上下文字符串
        """
        if not session_id:
            return ""

        try:
            if context_type == "planner":
                config_key = "scene.planner_context_messages"
                default_limit = 1
            else:
                config_key = "scene.reply_context_messages"
                default_limit = 10

            limit = self.get_config(config_key, default_limit)
            limit = int(limit)
        except (TypeError, ValueError):
            limit = default_limit if context_type == "reply" else 1

        limit = max(0, min(limit, 50))
        if limit == 0:
            return ""

        history = self.db.get_recent_history(session_id, limit) if self.db else []

        if not history:
            return "【最近场景对话】暂无历史记录"

        # 智能上下文处理
        if context_type == "reply" and len(history) > 5:
            return self._build_smart_context(history)
        else:
            return self._build_standard_context(history, context_type)

    def _build_standard_context(self, history: List[Dict], context_type: str) -> str:
        """构建标准格式的上下文"""
        limit = len(history)

        if context_type == "planner":
            header = f"【最近场景对话】（最近{limit}轮）"
        else:
            header = f"【最近场景对话】（最早在前，共{limit}轮）"

        lines: List[str] = [header]
        for idx, record in enumerate(history, 1):
            timestamp = record.get("timestamp") or ""
            location = record.get("location") or "未知"
            clothing = record.get("clothing") or "未知"
            user_msg = collapse_text(record.get("user_message"))
            bot_reply = collapse_text(record.get("bot_reply"))
            scene_preview = collapse_text(record.get("scene_description"))
            if scene_preview:
                scene_preview = truncate_text(scene_preview, 80)

            lines.append(f"{idx}. [{timestamp}] 地点：{location} / 着装：{clothing}")
            lines.append(f"    用户：{user_msg or '（无内容）'}")
            lines.append(f"    Bot：{bot_reply or '（无内容）'}")
            if scene_preview:
                lines.append(f"    场景：{scene_preview}")

        return "\n".join(lines)

    def _build_smart_context(self, history: List[Dict]) -> str:
        """
        智能上下文构建：对长历史进行摘要+详细的混合处理
        """
        total = len(history)
        recent_count = min(5, total)
        summary_count = total - recent_count

        lines: List[str] = [f"【最近场景对话】（共{total}轮）"]

        # 早期历史摘要
        if summary_count > 0:
            early_history = history[:summary_count]
            summary = self._summarize_history(early_history)
            lines.append(f"\n【早期对话摘要】（{summary_count}轮）")
            lines.append(summary)

        # 最近历史详细
        recent_history = history[summary_count:]
        lines.append(f"\n【最近对话详情】（{recent_count}轮）")

        for idx, record in enumerate(recent_history, 1):
            timestamp = record.get("timestamp") or ""
            location = record.get("location") or "未知"
            clothing = record.get("clothing") or "未知"
            user_msg = collapse_text(record.get("user_message"))
            bot_reply = collapse_text(record.get("bot_reply"))
            scene_preview = collapse_text(record.get("scene_description"))
            if scene_preview:
                scene_preview = truncate_text(scene_preview, 100)

            lines.append(f"{idx}. [{timestamp}] 📍{location} / 👗{clothing}")
            lines.append(f"    用户：{user_msg or '（无内容）'}")
            lines.append(f"    Bot：{bot_reply or '（无内容）'}")
            if scene_preview:
                lines.append(f"    场景：{scene_preview}")

        return "\n".join(lines)

    def _summarize_history(self, history: List[Dict]) -> str:
        """将历史记录压缩为摘要"""
        if not history:
            return "无"

        # 提取地点变化轨迹
        locations = []
        for record in history:
            loc = record.get("location", "")
            if loc and (not locations or locations[-1] != loc):
                locations.append(loc)

        # 提取关键用户消息
        key_messages = []
        for record in history:
            user_msg = record.get("user_message", "")
            if user_msg:
                short_msg = user_msg[:20] + "..." if len(user_msg) > 20 else user_msg
                key_messages.append(short_msg)

        summary_parts = []

        if locations:
            loc_str = " → ".join(locations[:5])
            if len(locations) > 5:
                loc_str += " → ..."
            summary_parts.append(f"地点轨迹: {loc_str}")

        if key_messages:
            msgs = key_messages[:3]
            if len(key_messages) > 3:
                msgs.append(f"...等{len(key_messages)}条消息")
            summary_parts.append(f"对话要点: {'; '.join(msgs)}")

        return "\n".join(summary_parts) if summary_parts else "普通对话"
