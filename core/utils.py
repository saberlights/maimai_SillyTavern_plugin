"""
场景模式工具函数模块
提供通用的工具函数，供其他模块使用
"""
import json
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from src.common.logger import get_logger

logger = get_logger("scene_utils")


def safe_json_loads(text: str, default=None):
    """安全解析JSON字符串"""
    if not text:
        return default if default is not None else {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default if default is not None else {}


def parse_json_response(response: str) -> Optional[dict]:
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
        relaxed = parse_structured_text(response)
        if relaxed:
            logger.warning("[Utils] 使用宽松解析提取场景内容")
            return relaxed
        return None
    except Exception as e:
        logger.error(f"解析响应时出错: {e}")
    return None


def parse_structured_text(response: str) -> Optional[dict]:
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


def collapse_text(text: Optional[str]) -> str:
    """压缩多余空白，保持提示紧凑"""
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def truncate_text(text: str, limit: int) -> str:
    """截断过长文本，避免提示过大"""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def parse_datetime(time_str: str) -> Optional[datetime]:
    """解析时间字符串，支持多种格式"""
    if not time_str:
        return None

    # 支持的时间格式列表
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y/%m/%d %H:%M:%S",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue

    logger.warning(f"[Utils] 无法解析时间格式: {time_str}")
    return None


def parse_status_json_fields(character_status: Dict[str, Any]) -> Dict[str, Any]:
    """解析角色状态中的JSON字段，返回解析后的字典"""
    return {
        "inventory": safe_json_loads(character_status.get('inventory', '[]'), []),
        "vaginal_foreign": safe_json_loads(character_status.get('vaginal_foreign', '[]'), []),
        "semen_sources": safe_json_loads(character_status.get('semen_sources', '[]'), []),
        "permanent_mods": safe_json_loads(character_status.get('permanent_mods', ''), {}),
        "body_condition": safe_json_loads(character_status.get('body_condition', '{}'), {}),
        "fetishes": safe_json_loads(character_status.get('fetishes', '{}'), {})
    }


def build_session_id(chat_id: str, user_id: Optional[str]) -> str:
    """根据聊天流与用户构建唯一会话ID"""
    user_part = user_id or "unknown_user"
    return f"{chat_id}:{user_part}"


def normalize_planner_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    """
    清除planner返回键名中的多余空格，确保字段能被识别
    注意：只清理键名中的空格，保留字符串值中的空格（如生理状态描述）
    同时处理布尔字符串 "true"/"false" 转换为真正的布尔值
    """
    normalized = {}
    # 布尔字段列表
    bool_fields = {"地点变化", "着装变化", "建议配图"}

    for key, value in decision.items():
        # 只清理键名中的空格
        clean_key = re.sub(r"\s+", "", str(key))

        # 处理值：只清理键名，保留字符串值
        if isinstance(value, str):
            # 检查是否是布尔字段，如果是则转换字符串为布尔值
            if clean_key in bool_fields:
                clean_value = value.lower().strip() in ("true", "1", "yes", "是")
            else:
                # 字符串值：只清理首尾空白，保留内部空格
                clean_value = value.strip()
        elif isinstance(value, dict):
            # 字典值：递归清理键名
            clean_value = _clean_dict_key_spaces(value)
        elif isinstance(value, list):
            # 列表值：只清理字符串元素的首尾空白
            clean_value = [v.strip() if isinstance(v, str) else v for v in value]
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


def _clean_dict_key_spaces(d: Dict[str, Any]) -> Dict[str, Any]:
    """
    递归清理字典中键名的空格，保留字符串值
    """
    cleaned = {}
    for key, value in d.items():
        # 只清理键名中的空格
        clean_key = re.sub(r"\s+", "", str(key)) if isinstance(key, str) else key

        # 处理值：保留字符串值中的空格
        if isinstance(value, str):
            clean_value = value.strip()
        elif isinstance(value, dict):
            clean_value = _clean_dict_key_spaces(value)
        elif isinstance(value, list):
            clean_value = [v.strip() if isinstance(v, str) else v for v in value]
        else:
            clean_value = value

        cleaned[clean_key] = clean_value

    return cleaned


def get_default_decision() -> Dict[str, Any]:
    """返回默认决策（无变化）"""
    return {
        "地点变化": False,
        "新地点": "",
        "着装变化": False,
        "新着装": "",
        "角色状态更新": {}
    }


def extract_scene_content(response: str) -> str:
    """
    从 LLM 返回中提取正文内容

    LLM 返回格式通常为：
    - 正文内容（可能有 <content>...</content> 包裹）
    - <tucao>...</tucao> 吐槽（穿插或末尾）
    - <details>...</details> 摘要
    - ```json {...} ``` 元数据

    此函数提取正文部分，去除元数据标签
    """
    if not response:
        return ""

    text = response

    # 1. 尝试提取 <content>...</content> 中的内容
    content_match = re.search(r'<content>(.*?)</content>', text, re.DOTALL)
    if content_match:
        text = content_match.group(1)
    else:
        # 没有 content 标签，需要清理其他标签
        # 移除 JSON 代码块（通常在末尾）
        text = re.sub(r'```json\s*\{.*?\}\s*```', '', text, flags=re.DOTALL)
        text = re.sub(r'```\s*\{.*?\}\s*```', '', text, flags=re.DOTALL)

        # 移除 <details>...</details> 摘要块
        text = re.sub(r'<details>.*?</details>', '', text, flags=re.DOTALL)

        # 移除 <think>...</think> 思维链
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)

    # 2. 移除 <tucao>...</tucao> 吐槽内容
    text = re.sub(r'<tucao>.*?</tucao>', '', text, flags=re.DOTALL)

    # 3. 移除其他可能的标签残留
    text = re.sub(r'</?content>', '', text)
    text = re.sub(r'</?tucao>', '', text)

    # 4. 清理多余空白，但保留段落结构
    # 将连续3个以上换行符压缩为2个
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 清理首尾空白
    text = text.strip()

    return text


def extract_scene_with_metadata(response: str) -> Dict[str, Any]:
    """
    从 LLM 返回中同时提取正文和 JSON 元数据

    Returns:
        {
            "content": "正文内容",
            "metadata": {...}  # JSON 元数据，可能为 None
        }
    """
    result = {
        "content": "",
        "metadata": None
    }

    # 提取正文
    result["content"] = extract_scene_content(response)

    # 提取 JSON 元数据
    result["metadata"] = parse_json_response(response)

    return result
