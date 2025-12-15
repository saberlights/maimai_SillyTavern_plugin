"""
NAI 生图客户端
用于在场景模式下生成配图
"""
import asyncio
import aiohttp
import base64
import os
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
        prompt_suffix = self.get_config("nai.prompt_suffix", "masterpiece, best quality")
        artist_preset = self.get_config("nai.artist_preset", "")

        full_prompt = prompt
        if prompt_suffix:
            full_prompt = f"{prompt}, {prompt_suffix}"

        # 构建请求参数
        params = {
            "tag": full_prompt,
            "model": self.get_config("nai.model", "nai-diffusion-4-5-full"),
            "token": api_key,
            "negative": self.get_config("nai.negative_prompt", ""),
            "sampler": self.get_config("nai.sampler", "k_euler_ancestral"),
            "steps": self.get_config("nai.steps", 28),
            "scale": self.get_config("nai.guidance_scale", 5.0),
            "cfg": self.get_config("nai.cfg", 0.0),
            "noise_schedule": self.get_config("nai.noise_schedule", "karras"),
            "nocache": self.get_config("nai.nocache", 1),
            "size": self.get_config("nai.size", "竖图"),
        }

        # 添加画师预设
        if artist_preset:
            params["artist"] = artist_preset

        url = f"{base_url.rstrip('/')}{endpoint}"
        timeout = self.get_config("nai.timeout", 120)
        max_retries = self.get_config("nai.max_retries", 2)
        retry_interval = self.get_config("nai.retry_interval", 2.0)

        last_error = None
        for attempt in range(max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                        ssl=False  # 禁用 SSL 验证
                    ) as response:
                        if response.status == 200:
                            content_type = response.headers.get("content-type", "")

                            if "application/json" in content_type:
                                # JSON 响应
                                data = await response.json()
                                image_data = data.get("url") or data.get("image_url") or data.get("image") or data.get("data")
                                if image_data:
                                    # 如果是 URL，需要下载
                                    if image_data.startswith("http"):
                                        image_data = await self._download_image(session, image_data)
                                    file_path = self._save_image(image_data)
                                    if file_path:
                                        logger.info(f"NAI 生图成功: {file_path}")
                                        return True, file_path
                                    else:
                                        return False, "图片保存失败"
                                else:
                                    return False, "响应中无图片数据"
                            else:
                                # 二进制响应
                                image_bytes = await response.read()
                                image_base64 = base64.b64encode(image_bytes).decode('utf-8')
                                file_path = self._save_image(image_base64)
                                if file_path:
                                    logger.info(f"NAI 生图成功: {file_path}")
                                    return True, file_path
                                else:
                                    return False, "图片保存失败"
                        else:
                            error_text = await response.text()
                            last_error = f"HTTP {response.status}: {error_text[:100]}"
                            logger.warning(f"NAI 生图失败 (尝试 {attempt + 1}/{max_retries}): {last_error}")

            except asyncio.TimeoutError:
                last_error = f"请求超时 ({timeout}s)"
                logger.warning(f"NAI 生图超时 (尝试 {attempt + 1}/{max_retries})")
            except aiohttp.ClientError as e:
                last_error = f"网络错误: {str(e)}"
                logger.warning(f"NAI 网络错误 (尝试 {attempt + 1}/{max_retries}): {e}")
            except Exception as e:
                last_error = f"未知错误: {str(e)}"
                logger.error(f"NAI 生图异常 (尝试 {attempt + 1}/{max_retries}): {e}", exc_info=True)

            # 重试前等待
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_interval)

        logger.error(f"NAI 生图最终失败: {last_error}")
        return False, last_error

    async def _download_image(self, session: aiohttp.ClientSession, url: str) -> str:
        """下载图片并返回 Base64"""
        async with session.get(url, ssl=False) as response:
            if response.status == 200:
                image_bytes = await response.read()
                return base64.b64encode(image_bytes).decode('utf-8')
        return ""

    def _save_image(self, image_base64: str) -> Optional[str]:
        """保存 Base64 图片到文件"""
        try:
            # 清理过期文件
            self._cleanup_old_files()

            # 解码 Base64
            if image_base64.startswith("data:image"):
                image_base64 = image_base64.split(",", 1)[1]

            image_bytes = base64.b64decode(image_base64)

            # 检测图片格式
            image_type = imghdr.what(None, h=image_bytes) or "png"
            extension = "jpg" if image_type == "jpeg" else image_type

            # 生成文件名
            file_name = f"scene_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}.{extension}"
            file_path = _IMAGE_OUTPUT_DIR / file_name

            # 保存文件
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
        # 每 5 分钟检查一次
        if now - _LAST_CLEANUP_TIME < 300:
            return

        _LAST_CLEANUP_TIME = now

        try:
            files = list(_IMAGE_OUTPUT_DIR.glob("scene_*.png")) + \
                    list(_IMAGE_OUTPUT_DIR.glob("scene_*.jpg")) + \
                    list(_IMAGE_OUTPUT_DIR.glob("scene_*.webp"))

            # 按修改时间排序
            files.sort(key=lambda f: f.stat().st_mtime)

            # 删除过期文件
            for f in files:
                if now - f.stat().st_mtime > _FILE_RETENTION_SECONDS:
                    f.unlink()
                    logger.debug(f"清理过期图片: {f.name}")

            # 如果文件数量超过限制，删除最旧的
            files = list(_IMAGE_OUTPUT_DIR.glob("scene_*.*"))
            if len(files) > _MAX_FILES:
                files.sort(key=lambda f: f.stat().st_mtime)
                for f in files[:len(files) - _MAX_FILES]:
                    f.unlink()
                    logger.debug(f"清理超额图片: {f.name}")

        except Exception as e:
            logger.error(f"清理图片文件失败: {e}")
