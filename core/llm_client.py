"""
插件独立的 LLM 客户端
支持 OpenAI 兼容的 API 格式
优化版本：添加 session 复用、更好的错误处理
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
        self.base_url = base_url.rstrip('/') if base_url else ""
        self.api_key = api_key or ""
        self.model = model or ""
        self.temperature = self._validate_float(temperature, 0.7, 0.0, 2.0)
        self.max_tokens = self._validate_int(max_tokens, 2048, 1, 32768)
        self.timeout = self._validate_int(timeout, 60, 5, 600)
        self.max_retries = self._validate_int(max_retries, 3, 1, 10)
        self.retry_interval = self._validate_float(retry_interval, 1.0, 0.1, 30.0)
        self.extra_params = extra_params or {}

        # 复用的 session
        self._session: Optional[aiohttp.ClientSession] = None

    @staticmethod
    def _validate_float(value: Any, default: float, min_val: float, max_val: float) -> float:
        """验证并限制浮点数范围"""
        try:
            value = float(value)
            return max(min_val, min(max_val, value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _validate_int(value: Any, default: int, min_val: int, max_val: int) -> int:
        """验证并限制整数范围"""
        try:
            value = int(value)
            return max(min_val, min(max_val, value))
        except (TypeError, ValueError):
            return default

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 aiohttp session（复用连接）"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        """关闭 session"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def generate_response_async(self, prompt: str) -> Tuple[str, Dict[str, Any]]:
        """
        异步生成回复

        Args:
            prompt: 提示词

        Returns:
            (回复文本, 元数据)
        """
        if not self.base_url or not self.api_key or not self.model:
            raise ValueError("LLM 客户端配置不完整：缺少 base_url、api_key 或 model")

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

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            **self.extra_params
        }

        last_error = None
        session = await self._get_session()

        for attempt in range(self.max_retries):
            try:
                async with session.post(url, headers=headers, json=payload) as response:
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
                    elif response.status == 429:
                        # 速率限制，等待更长时间
                        last_error = "API 速率限制"
                        wait_time = self.retry_interval * (attempt + 2)
                        logger.warning(f"LLM 速率限制，等待 {wait_time}s 后重试")
                        await asyncio.sleep(wait_time)
                        continue
                    elif response.status >= 500:
                        # 服务器错误，可重试
                        error_text = await response.text()
                        last_error = f"服务器错误 {response.status}: {error_text[:200]}"
                        logger.warning(f"LLM 服务器错误 (尝试 {attempt + 1}/{self.max_retries}): {last_error}")
                    else:
                        # 客户端错误，不重试
                        error_text = await response.text()
                        last_error = f"API 错误 {response.status}: {error_text[:200]}"
                        logger.error(f"LLM 客户端错误: {last_error}")
                        break

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

        logger.error(f"LLM 调用最终失败: {last_error}")
        raise Exception(f"LLM 调用失败: {last_error}")

    def __del__(self):
        """析构时尝试关闭 session"""
        if self._session and not self._session.closed:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.close())
                else:
                    loop.run_until_complete(self.close())
            except Exception:
                pass


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
            from src.llm_models.utils_model import LLMRequest
            from src.config.config import model_config
            return LLMRequest(
                model_set=model_config.model_task_config.replyer,
                request_type="scene_reply_generation"
            )
