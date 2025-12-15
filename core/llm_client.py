"""
插件独立的 LLM 客户端
支持 OpenAI 兼容的 API 格式
"""
import asyncio
import aiohttp
from typing import Optional, Dict, Any, Tuple
from src.common.logger import get_logger

logger = get_logger("scene_llm_client")


class LLMClient:
    """
    LLM API 客户端
    支持 OpenAI 兼容的 API 格式（OpenAI, Claude, DeepSeek 等）
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 60,
        max_retries: int = 3,
        retry_interval: float = 1.0,
        extra_params: Optional[Dict[str, Any]] = None
    ):
        """
        初始化 LLM 客户端

        Args:
            base_url: API 基础 URL（如 https://api.openai.com/v1）
            api_key: API 密钥
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大输出 token 数
            timeout: 超时时间（秒）
            max_retries: 重试次数
            retry_interval: 重试间隔（秒）
            extra_params: 额外参数
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_interval = retry_interval
        self.extra_params = extra_params or {}

    async def generate_response_async(self, prompt: str) -> Tuple[str, Dict[str, Any]]:
        """
        异步生成回复

        Args:
            prompt: 提示词

        Returns:
            (回复文本, 元数据)
        """
        messages = [{"role": "user", "content": prompt}]
        return await self._chat_completion(messages)

    async def _chat_completion(self, messages: list) -> Tuple[str, Dict[str, Any]]:
        """
        调用 Chat Completion API

        Args:
            messages: 消息列表

        Returns:
            (回复文本, 元数据)
        """
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        # 构建请求体
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            **self.extra_params
        }

        last_error = None
        for attempt in range(self.max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=self.timeout)
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            content = data["choices"][0]["message"]["content"]
                            metadata = {
                                "model": data.get("model"),
                                "usage": data.get("usage", {}),
                                "finish_reason": data["choices"][0].get("finish_reason")
                            }
                            logger.debug(f"LLM 调用成功: model={self.model}, tokens={metadata.get('usage', {})}")
                            return content, metadata
                        else:
                            error_text = await response.text()
                            last_error = f"API 错误 {response.status}: {error_text}"
                            logger.warning(f"LLM 调用失败 (尝试 {attempt + 1}/{self.max_retries}): {last_error}")

            except asyncio.TimeoutError:
                last_error = f"请求超时 ({self.timeout}s)"
                logger.warning(f"LLM 调用超时 (尝试 {attempt + 1}/{self.max_retries})")
            except aiohttp.ClientError as e:
                last_error = f"网络错误: {str(e)}"
                logger.warning(f"LLM 网络错误 (尝试 {attempt + 1}/{self.max_retries}): {e}")
            except Exception as e:
                last_error = f"未知错误: {str(e)}"
                logger.error(f"LLM 调用异常 (尝试 {attempt + 1}/{self.max_retries}): {e}", exc_info=True)

            # 重试前等待
            if attempt < self.max_retries - 1:
                await asyncio.sleep(self.retry_interval)

        # 所有重试都失败
        logger.error(f"LLM 调用最终失败: {last_error}")
        raise Exception(f"LLM 调用失败: {last_error}")


class LLMClientFactory:
    """
    LLM 客户端工厂
    根据配置创建合适的客户端（自定义 API 或 MaiBot 原生）
    """

    @staticmethod
    def create_planner_client(get_config_func) -> Any:
        """
        创建 Planner 模型客户端

        Args:
            get_config_func: 获取配置的函数

        Returns:
            LLMClient 或 MaiBot 原生 LLMRequest
        """
        use_custom = get_config_func("llm.planner.use_custom_api", False)

        if use_custom:
            return LLMClient(
                base_url=get_config_func("llm.planner.base_url", ""),
                api_key=get_config_func("llm.planner.api_key", ""),
                model=get_config_func("llm.planner.model", ""),
                temperature=get_config_func("llm.planner.temperature", 0.7),
                max_tokens=get_config_func("llm.planner.max_tokens", 2048),
                timeout=get_config_func("llm.planner.timeout", 60),
                max_retries=get_config_func("llm.planner.max_retries", 3),
                retry_interval=get_config_func("llm.planner.retry_interval", 1.0),
                extra_params=get_config_func("llm.planner.extra_params", {})
            )
        else:
            # 使用 MaiBot 原生的 LLMRequest
            from src.llm_models.utils_model import LLMRequest
            from src.config.config import model_config
            return LLMRequest(
                model_set=model_config.model_task_config.planner,
                request_type="scene_state_planning"
            )

    @staticmethod
    def create_reply_client(get_config_func) -> Any:
        """
        创建 Reply 模型客户端

        Args:
            get_config_func: 获取配置的函数

        Returns:
            LLMClient 或 MaiBot 原生 LLMRequest
        """
        use_custom = get_config_func("llm.reply.use_custom_api", False)

        if use_custom:
            return LLMClient(
                base_url=get_config_func("llm.reply.base_url", ""),
                api_key=get_config_func("llm.reply.api_key", ""),
                model=get_config_func("llm.reply.model", ""),
                temperature=get_config_func("llm.reply.temperature", 0.9),
                max_tokens=get_config_func("llm.reply.max_tokens", 4096),
                timeout=get_config_func("llm.reply.timeout", 120),
                max_retries=get_config_func("llm.reply.max_retries", 3),
                retry_interval=get_config_func("llm.reply.retry_interval", 1.0),
                extra_params=get_config_func("llm.reply.extra_params", {})
            )
        else:
            # 使用 MaiBot 原生的 LLMRequest
            from src.llm_models.utils_model import LLMRequest
            from src.config.config import model_config
            return LLMRequest(
                model_set=model_config.model_task_config.replyer,
                request_type="scene_reply_generation"
            )
