"""
预设管理器 - 解析和管理 SillyTavern 预设
"""
import copy
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from src.common.logger import get_logger
from .scene_db import SceneDB

logger = get_logger("preset_manager")


DEFAULT_IMPORT_PROFILE = {
    "prompt_keys": ["prompts"],
    "prompt_order_keys": ["prompt_order", "promptOrder"],
    "prompt_order_character_ids": [100001],
    "prompt_order_use_first": False,
    "main_identifiers": ["main"],
    "ignore_identifiers": [
        "nsfw",
        "jailbreak",
        "chatHistory",
        "worldInfoBefore",
        "worldInfoAfter",
        "dialogueExamples",
        "charDescription",
        "charPersonality",
        "scenario",
        "personaDescription",
        "enhanceDefinitions"
    ],
    "style_detection": {
        "name_keywords": ["文风", "风格", "✔️", "style"],
        "identifier_keywords": [],
        "identifier_prefixes": [],
        "identifier_suffixes": [],
        "identifier_whitelist": []
    },
    "guideline_detection": {
        "name_keywords": ["指南", "指引", "guide", "zhinan"],
        "identifier_whitelist": []
    },
    "forbidden_detection": {
        "name_keywords": ["禁词", "禁用", "banlist", "forbidden"],
        "identifier_whitelist": []
    }
}

DEFAULT_IMPORT_CONFIG = {
    "profiles": {
        "default": DEFAULT_IMPORT_PROFILE
    },
    "preset_profiles": {}
}


class PresetManager:
    """预设管理器 - 处理 SillyTavern 预设文件"""

    def __init__(self, db: SceneDB = None):
        self.db = db or SceneDB()
        self.plugin_dir = Path(__file__).parent.parent
        (self.profile_configs,
         self.preset_profile_map,
         self._preset_profile_casefold) = self._load_import_config()

    def import_preset_from_file(self, file_path: str) -> bool:
        """
        从文件导入预设

        Args:
            file_path: 预设文件路径（相对于 data/ 目录或绝对路径）

        Returns:
            是否导入成功
        """
        try:
            # 处理路径
            path = Path(file_path)
            if not path.is_absolute():
                # 如果是相对路径，尝试在 data/ 目录中查找
                data_path = self.plugin_dir / "data" / file_path
                if data_path.exists():
                    path = data_path
                elif path.exists():
                    path = path
                else:
                    logger.error(f"找不到预设文件: {file_path}")
                    return False

            if not path.exists():
                logger.error(f"预设文件不存在: {path}")
                return False

            # 读取 JSON
            with open(path, 'r', encoding='utf-8') as f:
                preset_data = json.load(f)

            # 提取预设名称（从文件名）
            preset_name = path.stem

            logger.info(f"开始导入预设: {preset_name}")

            # 解析并保存预设
            return self._parse_and_save_preset(preset_name, str(path), preset_data)

        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}")
            return False
        except Exception as e:
            logger.error(f"导入预设失败: {e}", exc_info=True)
            return False

    def _parse_and_save_preset(self, preset_name: str, file_path: str, preset_data: dict) -> bool:
        """解析并保存预设数据"""
        try:
            profile, profile_name = self._get_import_profile(preset_name)

            # 保存预设配置（包含完整的 prompt_order 结构）
            metadata = json.dumps({
                "temperature": preset_data.get("temperature"),
                "top_p": preset_data.get("top_p"),
                "max_tokens": preset_data.get("openai_max_tokens"),
                "prompt_order": self._extract_prompt_order(preset_data, profile),
                "import_profile": profile_name
            })

            self.db.save_preset(preset_name, file_path, metadata)

            # 清空旧的 prompts
            self.db.clear_preset_prompts(preset_name)

            # 提取 prompts
            prompts = self._extract_prompts(preset_data, profile)

            saved_count = 0
            for prompt in prompts:
                # 过滤掉 marker prompts（没有 content 的占位符）
                if prompt.get("marker", False):
                    continue

                # 只保存有内容的 prompts
                if prompt.get("content"):
                    self.db.save_preset_prompt(preset_name, prompt)
                    saved_count += 1

            logger.info(f"预设 '{preset_name}' 导入成功，保存了 {saved_count} 个 prompts (profile={profile_name})")
            return True

        except Exception as e:
            logger.error(f"解析预设数据失败: {e}", exc_info=True)
            return False

    def get_available_styles(self, preset_name: str) -> List[Dict[str, str]]:
        """
        获取预设中可用的文风列表

        Returns:
            [{"identifier": "...", "name": "..."}, ...]
        """
        prompts = self.db.get_preset_prompts(preset_name)
        profile, _ = self._get_import_profile(preset_name)

        styles = []
        seen_identifiers = set()

        for prompt in prompts:
            identifier = prompt.get("identifier", "")
            name = prompt.get("name", "")

            # 跳过系统级别的 prompts
            if self._should_ignore_identifier(identifier, profile):
                continue

            if self._is_main_identifier(identifier, profile):
                continue

            if self._is_style_prompt(name, identifier, profile):
                if identifier and identifier not in seen_identifiers:
                    styles.append({
                        "identifier": identifier,
                        "name": name
                    })
                    seen_identifiers.add(identifier)

        logger.info(f"预设 '{preset_name}' 中找到 {len(styles)} 个文风")
        return styles

    def get_preset_prompt_by_identifier(self, preset_name: str, identifier: str) -> Optional[str]:
        """
        获取指定预设中特定 identifier 的 prompt 内容

        Args:
            preset_name: 预设名称
            identifier: prompt 标识符

        Returns:
            prompt 内容，如果不存在返回 None
        """
        prompts = self.db.get_preset_prompts(preset_name)

        for prompt in prompts:
            if prompt.get("identifier") == identifier:
                return prompt.get("content", "").strip()

        return None

    def get_preset_prompts_by_name_pattern(self, preset_name: str, name_patterns: List[str]) -> List[str]:
        """
        根据名称模式获取 prompts 内容列表

        Args:
            preset_name: 预设名称
            name_patterns: 名称模式列表（包含关系）

        Returns:
            匹配的 prompt 内容列表
        """
        prompts = self.db.get_preset_prompts(preset_name)
        matched_contents = []

        for prompt in prompts:
            name = prompt.get("name", "")
            content = prompt.get("content", "").strip()

            if not content:
                continue

            # 检查是否匹配任一模式
            for pattern in name_patterns:
                if pattern in name:
                    matched_contents.append(content)
                    break

        return matched_contents

    def build_full_preset_prompt(self, base_prompt: str, include_main: bool = True,
                                  include_guidelines: bool = True,
                                  include_style: bool = True) -> str:
        """
        构建包含完整预设内容的 prompt，使用 prompt_order 结构

        Args:
            base_prompt: 基础 prompt（场景任务描述）
            include_main: 是否包含主提示
            include_guidelines: 是否包含指南和禁词表
            include_style: 是否包含激活的文风

        Returns:
            完整的增强 prompt
        """
        # 获取激活的预设
        active_style = self.db.get_active_style()

        if not active_style:
            logger.debug("没有激活的预设，返回原始 prompt")
            return base_prompt

        preset_name = active_style["preset_name"]
        style_identifier = active_style.get("style_identifier")

        # 尝试使用 prompt_order 结构
        ordered_prompts = self._get_ordered_prompts(preset_name)

        if ordered_prompts:
            # 使用 prompt_order 的顺序构建
            return self._build_from_prompt_order(
                preset_name, base_prompt, ordered_prompts, style_identifier,
                include_main, include_guidelines, include_style
            )
        else:
            # 回退到旧的模式匹配方法
            logger.info("未找到 prompt_order 结构,使用传统方法")
            return self._build_from_pattern_matching(
                base_prompt, preset_name, style_identifier,
                include_main, include_guidelines, include_style
            )

    def _get_ordered_prompts(self, preset_name: str) -> Optional[List[Dict]]:
        """
        从预设的 prompt_order 中获取启用的 prompts 顺序

        Returns:
            按照 prompt_order 排序的 prompts 列表，如果没有 prompt_order 则返回 None
        """
        try:
            profile, _ = self._get_import_profile(preset_name)
            # 从预设的元数据中读取 prompt_order
            metadata = self.db.get_preset(preset_name)
            if not metadata:
                return None

            preset_data = json.loads(metadata.get("metadata", "{}"))
            prompt_order_list = preset_data.get("prompt_order", [])

            if not prompt_order_list:
                return None

            order_config = None
            target_ids = profile.get("prompt_order_character_ids", [])
            for config in prompt_order_list:
                char_id = config.get("character_id", config.get("characterId"))
                if self._match_character_id(char_id, target_ids):
                    order_config = config
                    break

            if not order_config and profile.get("prompt_order_use_first") and prompt_order_list:
                order_config = prompt_order_list[0]

            if not order_config:
                logger.debug("未找到匹配的 prompt_order 配置")
                return None

            # 获取所有 prompts
            all_prompts = self.db.get_preset_prompts(preset_name)
            prompts_by_id = {p["identifier"]: p for p in all_prompts}

            ordered = []
            for item in order_config.get("order", []):
                identifier = item.get("identifier")
                enabled = item.get("enabled", False)

                if enabled and identifier in prompts_by_id:
                    ordered.append(prompts_by_id[identifier])

            logger.info(f"从 prompt_order 获取到 {len(ordered)} 个启用的 prompts")
            return ordered if ordered else None

        except Exception as e:
            logger.error(f"解析 prompt_order 失败: {e}")
            return None

    def _build_from_prompt_order(self, preset_name: str, base_prompt: str,
                                   ordered_prompts: List[Dict],
                                   style_identifier: str, include_main: bool,
                                   include_guidelines: bool, include_style: bool) -> str:
        """使用 prompt_order 顺序构建完整 prompt"""
        profile, _ = self._get_import_profile(preset_name)
        prompt_parts = []
        added_count = {"main": 0, "guidelines": 0, "style": 0, "other": 0}

        for prompt in ordered_prompts:
            identifier = prompt.get("identifier", "")
            name = prompt.get("name", "")
            content = prompt.get("content", "").strip()

            # 跳过 marker prompts (没有实际内容的占位符)
            if prompt.get("marker", False) or not content:
                continue

            if self._should_ignore_identifier(identifier, profile):
                continue

            # 分类处理
            # 1. 主提示
            if self._is_main_identifier(identifier, profile):
                if include_main:
                    prompt_parts.append(content)
                    added_count["main"] += 1
                continue

            # 2. 指南和禁词表
            if (self._is_guideline_prompt(name, identifier, profile) or
                    self._is_forbidden_prompt(name, identifier, profile)):
                if include_guidelines:
                    prompt_parts.append(content)
                    added_count["guidelines"] += 1
                continue

            # 3. 文风相关
            if style_identifier and self._identifiers_equal(identifier, style_identifier):
                if include_style:
                    prompt_parts.append(content)
                    added_count["style"] += 1
                continue

            # 4. 其他启用的 prompts (可能是质量控制规则等)
            if include_guidelines:
                prompt_parts.append(content)
                added_count["other"] += 1

        # 在合适的位置插入 base_prompt (通常在 main 之后)
        if added_count["main"] > 0:
            prompt_parts.insert(added_count["main"], base_prompt)
        else:
            prompt_parts.insert(0, base_prompt)

        logger.info(f"[prompt_order模式] 已添加: main={added_count['main']}, "
                    f"guidelines={added_count['guidelines']}, style={added_count['style']}, "
                    f"other={added_count['other']}")

        return "\n\n".join(prompt_parts)

    def _build_from_pattern_matching(self, base_prompt: str, preset_name: str,
                                      style_identifier: str, include_main: bool,
                                      include_guidelines: bool, include_style: bool) -> str:
        """使用传统的模式匹配方法构建 prompt（旧版兼容）"""
        prompt_parts = []
        profile, _ = self._get_import_profile(preset_name)
        all_prompts = self.db.get_preset_prompts(preset_name)

        # 1. 主提示层
        if include_main:
            main_prompt = None
            for prompt in all_prompts:
                identifier = prompt.get("identifier", "")
                if self._is_main_identifier(identifier, profile):
                    content = prompt.get("content", "").strip()
                    if content:
                        main_prompt = content
                        break
            if main_prompt:
                prompt_parts.append(main_prompt)
                logger.debug("已添加主提示")

        # 2. 基础任务层
        prompt_parts.append(base_prompt)

        # 3. 写作规则层
        if include_guidelines:
            guideline_count = 0
            for prompt in all_prompts:
                identifier = prompt.get("identifier", "")
                name = prompt.get("name", "")
                content = prompt.get("content", "").strip()

                if not content:
                    continue

                if self._is_main_identifier(identifier, profile):
                    continue

                if self._should_ignore_identifier(identifier, profile):
                    continue

                if style_identifier and self._identifiers_equal(identifier, style_identifier):
                    continue

                if (self._is_guideline_prompt(name, identifier, profile) or
                        self._is_forbidden_prompt(name, identifier, profile)):
                    prompt_parts.append(content)
                    guideline_count += 1

            if guideline_count > 0:
                logger.debug(f"已添加 {guideline_count} 个指南/禁词规则")

        # 4. 文风层
        if include_style and style_identifier:
            style_prompts = self.db.get_style_prompts(style_identifier, preset_name)
            style_rules = [p.get("content", "").strip() for p in style_prompts if p.get("content")]
            if style_rules:
                prompt_parts.extend(style_rules)
                logger.debug("已添加文风")

        logger.info(f"[模式匹配模式] 已构建完整预设 prompt，包含 {len(prompt_parts)} 个部分")
        return "\n\n".join(prompt_parts)

    def apply_style_to_prompt(self, base_prompt: str, style_identifier: str = None) -> str:
        """
        将激活的文风规则应用到 prompt（旧版兼容方法）

        Args:
            base_prompt: 基础 prompt
            style_identifier: 文风标识（如果为 None，则使用当前激活的文风）

        Returns:
            增强后的 prompt
        """
        # 调用新的完整方法，但只包含文风
        return self.build_full_preset_prompt(
            base_prompt,
            include_main=False,
            include_guidelines=False,
            include_style=True
        )

    def list_presets(self) -> List[Dict[str, Any]]:
        """列出所有已导入的预设"""
        return self.db.get_preset_list()

    def delete_preset(self, preset_name: str) -> bool:
        """删除预设"""
        try:
            self.db.delete_preset(preset_name)
            return True
        except Exception as e:
            logger.error(f"删除预设失败: {e}")
            return False

    def activate_style(self, preset_name: str, style_identifier: str) -> bool:
        """激活指定文风"""
        try:
            # 验证预设存在
            presets = self.db.get_preset_list()
            if not any(p["preset_name"] == preset_name for p in presets):
                logger.error(f"预设 '{preset_name}' 不存在")
                return False

            # 验证文风存在
            styles = self.get_available_styles(preset_name)
            style = next((s for s in styles if s["identifier"] == style_identifier), None)

            if not style:
                logger.error(f"文风 '{style_identifier}' 在预设 '{preset_name}' 中不存在")
                return False

            # 激活文风
            self.db.set_active_style(preset_name, style_identifier, style["name"])
            logger.info(f"已激活文风: {style['name']}")
            return True

        except Exception as e:
            logger.error(f"激活文风失败: {e}", exc_info=True)
            return False

    def deactivate_style(self) -> bool:
        """取消激活文风"""
        try:
            self.db.clear_active_style()
            return True
        except Exception as e:
            logger.error(f"取消激活文风失败: {e}")
            return False

    def get_current_style(self) -> Optional[Dict[str, Any]]:
        """获取当前激活的文风"""
        return self.db.get_active_style()

    # ==================== 预设导入配置 ====================

    def _load_import_config(self) -> Tuple[Dict[str, Any], Dict[str, str], Dict[str, str]]:
        """加载用户配置的预设导入规则"""
        config_path = self.plugin_dir / "data" / "preset_import_config.json"
        config_data = copy.deepcopy(DEFAULT_IMPORT_CONFIG)

        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    user_config = json.load(f)
                config_data = self._deep_merge_dict(config_data, user_config)
            except Exception as e:
                logger.error(f"读取自定义预设导入配置失败，使用默认配置: {e}")

        raw_profiles = config_data.get("profiles", {}) or {}
        profiles = {}
        default_profile = raw_profiles.get("default", DEFAULT_IMPORT_PROFILE)

        for name, profile_data in raw_profiles.items():
            if name == "default":
                profiles[name] = copy.deepcopy(default_profile)
            else:
                profiles[name] = self._deep_merge_dict(
                    copy.deepcopy(default_profile),
                    profile_data
                )

        if "default" not in profiles:
            profiles["default"] = copy.deepcopy(DEFAULT_IMPORT_PROFILE)

        preset_profiles = config_data.get("preset_profiles", {}) or {}
        preset_profile_casefold = {k.casefold(): v for k, v in preset_profiles.items()}

        logger.info(
            f"已加载 {len(profiles)} 个预设导入配置档案，映射 {len(preset_profiles)} 个预设"
        )

        return profiles, preset_profiles, preset_profile_casefold

    def _deep_merge_dict(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """深拷贝并合并字典"""
        result = copy.deepcopy(base)
        for key, value in (override or {}).items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = self._deep_merge_dict(result.get(key, {}), value)
            else:
                result[key] = value
        return result

    def _get_import_profile(self, preset_name: str) -> Tuple[Dict[str, Any], str]:
        """根据预设名获取导入配置"""
        if not preset_name:
            preset_name = ""

        key = preset_name.casefold()
        profile_name = self._preset_profile_casefold.get(key)

        if not profile_name or profile_name not in self.profile_configs:
            profile_name = "default"

        profile = copy.deepcopy(self.profile_configs.get(profile_name, self.profile_configs["default"]))
        return profile, profile_name

    def _get_value_by_path(self, data: Any, path: str) -> Any:
        """使用点分路径提取嵌套字段"""
        if not path:
            return None

        parts = path.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    def _extract_prompts(self, preset_data: Dict[str, Any], profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        for path in profile.get("prompt_keys", ["prompts"]):
            result = self._get_value_by_path(preset_data, path) if path else None
            if isinstance(result, list):
                return result
        logger.warning("未在预设中找到 prompts 字段，使用空列表")
        return []

    def _extract_prompt_order(self, preset_data: Dict[str, Any], profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        for path in profile.get("prompt_order_keys", ["prompt_order"]):
            result = self._get_value_by_path(preset_data, path) if path else None
            if isinstance(result, list):
                return result
        return []

    def _normalize_text(self, value: Any) -> str:
        return str(value).strip().casefold() if value is not None else ""

    def _match_keywords(self, keywords: List[str], value: Any) -> bool:
        target = self._normalize_text(value)
        for keyword in keywords or []:
            if keyword and self._normalize_text(keyword) in target:
                return True
        return False

    def _equals_any(self, value: Any, candidates: List[Any]) -> bool:
        target = self._normalize_text(value)
        for candidate in candidates or []:
            if target == self._normalize_text(candidate):
                return True
        return False

    def _startswith_any(self, value: Any, prefixes: List[str]) -> bool:
        target = self._normalize_text(value)
        for prefix in prefixes or []:
            normalized = self._normalize_text(prefix)
            if normalized and target.startswith(normalized):
                return True
        return False

    def _endswith_any(self, value: Any, suffixes: List[str]) -> bool:
        target = self._normalize_text(value)
        for suffix in suffixes or []:
            normalized = self._normalize_text(suffix)
            if normalized and target.endswith(normalized):
                return True
        return False

    def _should_ignore_identifier(self, identifier: str, profile: Dict[str, Any]) -> bool:
        return self._equals_any(identifier, profile.get("ignore_identifiers", []))

    def _is_main_identifier(self, identifier: str, profile: Dict[str, Any]) -> bool:
        return self._equals_any(identifier, profile.get("main_identifiers", ["main"]))

    def _is_style_prompt(self, name: str, identifier: str, profile: Dict[str, Any]) -> bool:
        style_cfg = profile.get("style_detection", {}) or {}
        return (
            self._equals_any(identifier, style_cfg.get("identifier_whitelist", [])) or
            self._startswith_any(identifier, style_cfg.get("identifier_prefixes", [])) or
            self._endswith_any(identifier, style_cfg.get("identifier_suffixes", [])) or
            self._match_keywords(style_cfg.get("identifier_keywords", []), identifier) or
            self._match_keywords(style_cfg.get("name_keywords", []), name)
        )

    def _is_guideline_prompt(self, name: str, identifier: str, profile: Dict[str, Any]) -> bool:
        guide_cfg = profile.get("guideline_detection", {}) or {}
        return (
            self._equals_any(identifier, guide_cfg.get("identifier_whitelist", [])) or
            self._match_keywords(guide_cfg.get("name_keywords", []), name)
        )

    def _is_forbidden_prompt(self, name: str, identifier: str, profile: Dict[str, Any]) -> bool:
        forbid_cfg = profile.get("forbidden_detection", {}) or {}
        return (
            self._equals_any(identifier, forbid_cfg.get("identifier_whitelist", [])) or
            self._match_keywords(forbid_cfg.get("name_keywords", []), name)
        )

    def _identifiers_equal(self, identifier: str, target: str) -> bool:
        return self._normalize_text(identifier) == self._normalize_text(target)

    def _match_character_id(self, value: Any, targets: List[Any]) -> bool:
        if not targets:
            return False
        normalized_value = self._normalize_text(value)
        for target in targets:
            if normalized_value == self._normalize_text(target):
                return True
        return False
