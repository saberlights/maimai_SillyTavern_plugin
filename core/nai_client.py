"""
NAI 生图客户端
用于在场景模式下生成配图
优化版本：添加 SSL 配置选项、session 复用、更好的错误处理
"""
import asyncio
import aiohttp
import base64
import ssl
import time
import uuid
import imghdr
from typing import Optional, Dict, Any, Tuple
from pathlib import Path
from src.common.logger import get_logger

logger = get_logger("scene_nai_client")

# 图片输出目录
_IMAGE_OUTPUT_DIR = Path(__file__).parent.parent / "generated_images"
_IMAGE_OUTPUT_DIR.mkdir(exist_ok=True)

# 清理配置
_MAX_FILES = 80
_FILE_RETENTION_SECONDS = 30 * 60  # 30 分钟
_LAST_CLEANUP_TIME = 0


class NaiClient:
    """
    NAI 生图客户端
    支持 NAI 4.5 API
    """

    def __init__(self, get_config_func):
        """
        初始化 NAI 客户端

        Args:
            get_config_func: 获取配置的函数
        """
        self.get_config = get_config_func
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 aiohttp session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._get_timeout())
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        """关闭 session"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _get_timeout(self) -> int:
        """获取超时配置"""
        timeout = self.get_config("nai.timeout", 120)
        try:
            return max(30, min(300, int(timeout)))
        except (TypeError, ValueError):
            return 120

    def _get_ssl_context(self) -> Optional[ssl.SSLContext]:
        """获取 SSL 上下文配置"""
        ssl_verify = self.get_config("nai.ssl_verify", True)
        if ssl_verify:
            return None  # 使用默认 SSL 验证
        else:
            # 创建不验证证书的 SSL 上下文（仅在明确配置时使用）
            logger.warning("[NAI] SSL 验证已禁用，这可能存在安全风险")
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx

    async def generate_image(self, prompt: str) -> Tuple[bool, Optional[str]]:
        """
        生成图片

        Args:
            prompt: 英文提示词

        Returns:
            (是否成功, 图片路径或错误信息)
        """
        # 获取配置
        base_url = self.get_config("nai.base_url", "https://std.loliyc.com")
        endpoint = self.get_config("nai.endpoint", "/generate")
        api_key = self.get_config("nai.api_key", "")

        if not api_key:
            return False, "NAI API Token 未配置"

        # 构建完整提示词
        full_prompt = self._build_full_prompt(prompt)

        # 构建请求参数
        params = self._build_request_params(full_prompt, api_key)

        url = f"{base_url.rstrip('/')}{endpoint}"
        max_retries = self._get_int_config("nai.max_retries", 2, 1, 5)
        retry_interval = self._get_float_config("nai.retry_interval", 2.0, 0.5, 10.0)

        last_error = None
        session = await self._get_session()
        ssl_context = self._get_ssl_context()

        for attempt in range(max_retries):
            try:
                async with session.get(url, params=params, ssl=ssl_context) as response:
                    if response.status == 200:
                        return await self._handle_success_response(response)
                    elif response.status == 429:
                        last_error = "API 速率限制"
                        wait_time = retry_interval * (attempt + 2)
                        logger.warning(f"NAI 速率限制，等待 {wait_time}s 后重试")
                        await asyncio.sleep(wait_time)
                        continue
                    elif response.status >= 500:
                        error_text = await response.text()
                        last_error = f"服务器错误 {response.status}: {error_text[:100]}"
                        logger.warning(f"NAI 服务器错误 (尝试 {attempt + 1}/{max_retries}): {last_error}")
                    else:
                        error_text = await response.text()
                        last_error = f"HTTP {response.status}: {error_text[:100]}"
                        logger.warning(f"NAI 生图失败 (尝试 {attempt + 1}/{max_retries}): {last_error}")

            except asyncio.TimeoutError:
                last_error = f"请求超时 ({self._get_timeout()}s)"
                logger.warning(f"NAI 生图超时 (尝试 {attempt + 1}/{max_retries})")
            except aiohttp.ClientError as e:
                last_error = f"网络错误: {str(e)}"
                logger.warning(f"NAI 网络错误 (尝试 {attempt + 1}/{max_retries}): {e}")
            except Exception as e:
                last_error = f"未知错误: {str(e)}"
                logger.error(f"NAI 生图异常 (尝试 {attempt + 1}/{max_retries}): {e}", exc_info=True)

            if attempt < max_retries - 1:
                await asyncio.sleep(retry_interval)

        logger.error(f"NAI 生图最终失败: {last_error}")
        return False, last_error

    def _build_full_prompt(self, prompt: str) -> str:
        """构建完整的提示词"""
        full_prompt = prompt

        # 外貌描述
        appearance_desc = self.get_config("scene.appearance_description", "").strip()
        if appearance_desc:
            full_prompt = f"{appearance_desc}, {full_prompt}"

        # 正面提示词后缀
        prompt_suffix = self.get_config("nai.prompt_suffix", "masterpiece, best quality")
        if prompt_suffix:
            full_prompt = f"{full_prompt}, {prompt_suffix}"

        return full_prompt

    def _build_request_params(self, full_prompt: str, api_key: str) -> Dict[str, Any]:
        """构建请求参数"""
        params = {
            "tag": full_prompt,
            "model": self.get_config("nai.model", "nai-diffusion-4-5-full"),
            "token": api_key,
            "negative": self.get_config("nai.negative_prompt", ""),
            "sampler": self.get_config("nai.sampler", "k_euler_ancestral"),
            "steps": self._get_int_config("nai.steps", 28, 1, 50),
            "scale": self._get_float_config("nai.guidance_scale", 5.0, 1.0, 20.0),
            "cfg": self._get_float_config("nai.cfg", 0.0, 0.0, 1.0),
            "noise_schedule": self.get_config("nai.noise_schedule", "karras"),
            "nocache": self._get_int_config("nai.nocache", 1, 0, 1),
            "size": self.get_config("nai.size", "竖图"),
        }

        # 画师预设
        artist_preset = self.get_config("nai.artist_preset", "")
        if artist_preset:
            params["artist"] = artist_preset

        return params

    def _get_int_config(self, key: str, default: int, min_val: int, max_val: int) -> int:
        """获取整数配置并验证范围"""
        value = self.get_config(key, default)
        try:
            return max(min_val, min(max_val, int(value)))
        except (TypeError, ValueError):
            return default

    def _get_float_config(self, key: str, default: float, min_val: float, max_val: float) -> float:
        """获取浮点数配置并验证范围"""
        value = self.get_config(key, default)
        try:
            return max(min_val, min(max_val, float(value)))
        except (TypeError, ValueError):
            return default

    async def _handle_success_response(self, response: aiohttp.ClientResponse) -> Tuple[bool, Optional[str]]:
        """处理成功的响应"""
        content_type = response.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await response.json()
            image_data = data.get("url") or data.get("image_url") or data.get("image") or data.get("data")
            if image_data:
                if image_data.startswith("http"):
                    session = await self._get_session()
                    image_data = await self._download_image(session, image_data)
                file_path = self._save_image(image_data)
                if file_path:
                    logger.info(f"NAI 生图成功: {file_path}")
                    return True, file_path
                return False, "图片保存失败"
            return False, "响应中无图片数据"
        else:
            image_bytes = await response.read()
            image_base64 = base64.b64encode(image_bytes).decode('utf-8')
            file_path = self._save_image(image_base64)
            if file_path:
                logger.info(f"NAI 生图成功: {file_path}")
                return True, file_path
            return False, "图片保存失败"

    async def _download_image(self, session: aiohttp.ClientSession, url: str) -> str:
        """下载图片并返回 Base64"""
        ssl_context = self._get_ssl_context()
        try:
            async with session.get(url, ssl=ssl_context) as response:
                if response.status == 200:
                    image_bytes = await response.read()
                    return base64.b64encode(image_bytes).decode('utf-8')
        except Exception as e:
            logger.error(f"下载图片失败: {e}")
        return ""

    def _save_image(self, image_base64: str) -> Optional[str]:
        """保存 Base64 图片到文件"""
        try:
            self._cleanup_old_files()

            if image_base64.startswith("data:image"):
                image_base64 = image_base64.split(",", 1)[1]

            image_bytes = base64.b64decode(image_base64)

            image_type = imghdr.what(None, h=image_bytes) or "png"
            extension = "jpg" if image_type == "jpeg" else image_type

            file_name = f"scene_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}.{extension}"
            file_path = _IMAGE_OUTPUT_DIR / file_name

            with open(file_path, "wb") as f:
                f.write(image_bytes)

            return str(file_path)

        except Exception as e:
            logger.error(f"保存图片失败: {e}", exc_info=True)
            return None

    def _cleanup_old_files(self):
        """清理过期的图片文件"""
        global _LAST_CLEANUP_TIME

        now = time.time()
        if now - _LAST_CLEANUP_TIME < 300:
            return

        _LAST_CLEANUP_TIME = now

        try:
            files = list(_IMAGE_OUTPUT_DIR.glob("scene_*.png")) + \
                    list(_IMAGE_OUTPUT_DIR.glob("scene_*.jpg")) + \
                    list(_IMAGE_OUTPUT_DIR.glob("scene_*.webp"))

            files.sort(key=lambda f: f.stat().st_mtime)

            # 删除过期文件
            for f in files:
                try:
                    if now - f.stat().st_mtime > _FILE_RETENTION_SECONDS:
                        f.unlink()
                        logger.debug(f"清理过期图片: {f.name}")
                except OSError:
                    pass  # 文件可能正在使用

            # 删除超额文件
            files = list(_IMAGE_OUTPUT_DIR.glob("scene_*.*"))
            if len(files) > _MAX_FILES:
                files.sort(key=lambda f: f.stat().st_mtime)
                for f in files[:len(files) - _MAX_FILES]:
                    try:
                        f.unlink()
                        logger.debug(f"清理超额图片: {f.name}")
                    except OSError:
                        pass

        except Exception as e:
            logger.error(f"清理图片文件失败: {e}")

    def __del__(self):
        """析构时尝试关闭 session"""
        if self._session and not self._session.closed:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.close())
            except Exception:
                pass
