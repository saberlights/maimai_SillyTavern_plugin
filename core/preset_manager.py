"""
预设管理器 - 使用内置预设内容
"""
from typing import Optional, List, Dict, Any
from src.common.logger import get_logger
from .scene_db import SceneDB
from .preset_content import (
    STYLES,
    get_base_rules,
    get_nsfw_rules,
    get_format_rules,
    get_creative_rules,
    get_summary_format,
    get_tucao_format,
    get_chain_of_thought,
    get_style,
    get_all_style_names,
    build_full_prompt,
    get_prefix_rules,
    get_suffix_rules
)

logger = get_logger("preset_manager")


class PresetManager:
    """预设管理器 - 使用内置 Izumi 预设内容"""

    def __init__(self, db: SceneDB = None):
        self.db = db or SceneDB()

    def get_styles(self) -> List[Dict[str, str]]:
        """获取所有可用文风"""
        styles = []
        for name in get_all_style_names():
            content = get_style(name)
            styles.append({
                "id": name,  # 使用名称作为 ID
                "name": name,
                "content_len": len(content)
            })
        return styles

    def get_style_content(self, style_name: str) -> Optional[str]:
        """获取指定文风的内容"""
        return get_style(style_name) or None

    def get_style_by_name(self, name: str) -> Optional[Dict[str, str]]:
        """根据名称查找文风"""
        name_lower = name.lower()
        for style_name in get_all_style_names():
            if name_lower in style_name.lower():
                return {
                    "id": style_name,
                    "name": style_name,
                    "content_len": len(get_style(style_name))
                }
        return None

    def build_enhanced_prompt(self, base_prompt: str, style_name: Optional[str] = None,
                               include_nsfw: bool = True, include_summary: bool = True,
                               include_tucao: bool = True, include_cot: bool = False) -> str:
        """
        构建增强的 prompt

        Args:
            base_prompt: 基础 prompt（场景任务描述）
            style_name: 文风名称（可选）
            include_nsfw: 是否包含 NSFW 规则
            include_summary: 是否包含摘要格式
            include_tucao: 是否包含吐槽格式
            include_cot: 是否包含思维链

        Returns:
            增强后的完整 prompt
        """
        # 如果没有指定文风，检查数据库中是否有激活的文风
        if not style_name:
            current = self.get_current_style()
            if current:
                style_name = current.get("name")

        result = build_full_prompt(
            base_prompt, style_name, include_nsfw,
            include_summary, include_tucao, include_cot
        )

        components = ["基础规则"]
        if include_nsfw:
            components.append("NSFW+创意自由")
        components.append("格式规则")
        if include_summary:
            components.append("摘要格式")
        if include_tucao:
            components.append("吐槽格式")
        if include_cot:
            components.append("思维链")
        if style_name:
            components.append(f"文风[{style_name}]")
        components.append("场景prompt")

        logger.info(f"构建增强 prompt: {' + '.join(components)}")

        return result

    # ==================== 文风激活管理 ====================

    def activate_style(self, style_name: str) -> bool:
        """激活指定文风"""
        if style_name not in STYLES:
            logger.error(f"文风不存在: {style_name}")
            return False

        self.db.set_active_style("izumi", style_name, style_name)
        logger.info(f"已激活文风: {style_name}")
        return True

    def activate_style_by_name(self, name: str) -> Optional[str]:
        """根据名称激活文风，返回激活的文风名称"""
        style = self.get_style_by_name(name)
        if not style:
            return None

        if self.activate_style(style["name"]):
            return style["name"]
        return None

    def deactivate_style(self) -> bool:
        """取消激活文风"""
        try:
            self.db.clear_active_style()
            logger.info("已清除激活的文风")
            return True
        except Exception as e:
            logger.error(f"清除文风失败: {e}")
            return False

    def get_current_style(self) -> Optional[Dict[str, Any]]:
        """获取当前激活的文风"""
        active = self.db.get_active_style()
        if not active:
            return None

        # 优先使用 style_identifier，如果不在 STYLES 中则尝试 style_name
        style_name = active.get("style_identifier")
        if not style_name or style_name not in STYLES:
            # 尝试从 style_name 字段提取（可能是旧格式）
            raw_name = active.get("style_name", "")
            # 尝试匹配内置文风名称
            for builtin_name in STYLES.keys():
                if builtin_name in raw_name:
                    style_name = builtin_name
                    break

        if style_name and style_name in STYLES:
            return {
                "id": style_name,
                "name": style_name,
                "activated_at": active.get("updated_at")
            }
        return None

    # ==================== 新架构：前置/后置分离 ====================

    def get_prefix(self, include_nsfw: bool = True) -> str:
        """
        获取前置规则（放在角色信息之前）

        按原预设架构顺序：
        1. 禁词表
        2. 创作指南
        3. 防问题规则
        4. NSFW规则（可选）
        5. 创意自由（可选）
        """
        return get_prefix_rules(include_nsfw)

    def get_suffix(self, include_summary: bool = True, include_tucao: bool = True,
                   include_cot: bool = False) -> str:
        """
        获取后置规则（放在任务说明之后）

        按原预设架构顺序：
        1. 文风（如果有激活的）
        2. 格式规则（视角、场景描写等）
        3. 摘要格式
        4. 吐槽格式
        5. 思维链（可选）
        """
        # 获取当前激活的文风
        style_name = None
        current = self.get_current_style()  # 使用统一的方法获取文风
        if current:
            style_name = current.get("name")

        # 获取当前视角设置
        perspective = self.db.get_perspective() if self.db else "第一人称"

        return get_suffix_rules(style_name, include_summary, include_tucao, include_cot, perspective)

    def build_structured_prompt(self, task_info: str,
                                 include_nsfw: bool = True,
                                 include_summary: bool = True,
                                 include_tucao: bool = True,
                                 include_cot: bool = False) -> str:
        """
        构建完整 prompt

        新结构（逻辑清晰版）：
        第一部分：泉此方身份与创作规则（告诉AI它是谁、怎么写）
        第二部分：创作任务（具体要写什么）

        Args:
            task_info: 创作任务（角色信息、状态、历史、用户消息、输出格式）
            include_nsfw: 是否包含 NSFW 规则
            include_summary: 是否包含摘要格式
            include_tucao: 是否包含吐槽格式
            include_cot: 是否包含思维链
        """
        # 第一部分：泉此方身份与创作规则
        izumi_rules = self._build_izumi_rules(include_nsfw, include_summary, include_tucao, include_cot)

        # 第二部分：创作任务
        # task_info 由调用方提供，包含角色信息、状态、历史、用户消息、输出格式

        # 组装
        parts = [izumi_rules, task_info]

        # 记录日志
        rule_components = ["主提示"]
        if include_nsfw:
            rule_components.append("NSFW+创意自由")
        rule_components.append("创作指南")
        if include_tucao:
            rule_components.append("吐槽格式")
        rule_components.append("格式规则")
        if include_summary:
            rule_components.append("摘要格式")
        rule_components.append("禁词表")
        current = self.get_current_style()
        if current:
            rule_components.append(f"文风[{current.get('name')}]")
        if include_cot:
            rule_components.append("思维链")

        logger.info(f"构建 prompt: [{'+'.join(rule_components)}] → [创作任务]")

        return "\n\n".join(parts)

    def _build_izumi_rules(self, include_nsfw: bool = True,
                           include_summary: bool = True,
                           include_tucao: bool = True,
                           include_cot: bool = False) -> str:
        """
        构建泉此方身份与创作规则

        顺序：
        1. 主提示（身份定义）
        2. NSFW规则（可选）
        3. 创意自由（可选）
        4. 创作指南（ZHINAN）
        5. 吐槽格式（可选）
        6. 格式规则（视角、场景描写要点）
        7. 摘要格式（可选）
        8. 禁词表
        9. 文风（如果有激活）
        10. 思维链（可选）
        """
        from .preset_content import (
            MAIN_PROMPT, NSFW_RULES, NSFW_RULES_ENHANCED, CREATIVE_FREEDOM,
            WRITING_GUIDELINES, TUCAO_FORMAT, SUMMARY_FORMAT,
            BANNED_WORDS, FORMAT_RULES_TEMPLATE,
            CHAIN_OF_THOUGHT_LIGHT
        )

        parts = []

        # 1. 主提示
        parts.append(MAIN_PROMPT)

        # 2. NSFW规则（开启时把加强规则插入到基础规则的 </NSFW> 之前）
        if include_nsfw:
            nsfw_content = NSFW_RULES.replace("</NSFW>", NSFW_RULES_ENHANCED + "\n</NSFW>")
            parts.append(nsfw_content)
        else:
            parts.append(NSFW_RULES)

        # 3. 创意自由（始终存在）
        parts.append(CREATIVE_FREEDOM)

        # 4. 创作指南
        parts.append(WRITING_GUIDELINES)

        # 5. 吐槽格式
        if include_tucao:
            parts.append(TUCAO_FORMAT)

        # 6. 格式规则（带视角）
        perspective = self.db.get_perspective() if self.db else "第一人称"
        parts.append(FORMAT_RULES_TEMPLATE.format(perspective=perspective))

        # 7. 摘要格式
        if include_summary:
            parts.append(SUMMARY_FORMAT)

        # 8. 禁词表
        parts.append(BANNED_WORDS)

        # 9. 文风
        current = self.get_current_style()
        if current:
            from .preset_content import STYLES
            style_content = STYLES.get(current.get("name"))
            if style_content:
                parts.append(style_content)

        # 10. 思维链
        if include_cot:
            parts.append(CHAIN_OF_THOUGHT_LIGHT)

        return "\n\n".join(parts)

    # ==================== 兼容旧接口 ====================

    def build_full_preset_prompt(self, base_prompt: str, include_main: bool = True,
                                  include_guidelines: bool = True,
                                  include_style: bool = True) -> str:
        """兼容旧接口 - 仍然使用旧的一体化方式"""
        style_name = None
        if include_style:
            current = self.get_current_style()
            if current:
                style_name = current.get("name")

        return self.build_enhanced_prompt(
            base_prompt,
            style_name if include_style else None,
            include_nsfw=include_guidelines
        )
