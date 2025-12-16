"""
日程定时生成事件处理器
参考 chat_summary_plugin 实现
"""
import asyncio
from datetime import datetime, timedelta
from typing import Tuple, Optional

from src.plugin_system.base.base_events_handler import BaseEventHandler
from src.plugin_system.base.component_types import EventType, EventHandlerInfo, MaiMessages, ComponentType
from src.common.logger import get_logger

from ..core.scene_db import SceneDB
from ..core.llm_client import LLMClientFactory
from ..commands.schedule_command import generate_schedule, GLOBAL_SCHEDULE_USER

logger = get_logger("schedule_handler")


class ScheduleScheduler:
    """日程定时生成调度器"""

    def __init__(self, config_getter):
        """初始化调度器"""
        self.get_config = config_getter
        self.is_running = False
        self.task = None
        self.last_execution_date = None

    async def start(self, schedule_generator):
        """启动定时任务"""
        if self.is_running:
            return

        enabled = self.get_config("plugin.enabled", True)
        auto_schedule_enabled = self.get_config("scene.schedule.enabled", True)

        if not enabled or not auto_schedule_enabled:
            logger.info("日程自动生成未启用")
            return

        self.is_running = True
        self.task = asyncio.create_task(self._schedule_loop(schedule_generator))

        schedule_time = self.get_config("scene.schedule.time", "05:00")
        logger.info(f"✅ 日程定时任务已启动 - 执行时间: {schedule_time}")

    async def stop(self):
        """停止定时任务"""
        if not self.is_running:
            return

        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("日程定时任务已停止")

    async def _schedule_loop(self, schedule_generator):
        """定时任务循环"""
        while self.is_running:
            try:
                now = datetime.now()
                schedule_time_str = self.get_config("scene.schedule.time", "05:00")

                # 解析执行时间
                try:
                    hour, minute = map(int, schedule_time_str.split(":"))
                except ValueError:
                    logger.error(f"无效的时间格式: {schedule_time_str}，使用默认值 05:00")
                    hour, minute = 5, 0

                # 计算今天的执行时间点
                today_schedule = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

                # 如果今天的时间点已过，则计算明天的时间点
                if now >= today_schedule:
                    today_schedule += timedelta(days=1)

                # 计算等待秒数
                wait_seconds = (today_schedule - now).total_seconds()
                logger.info(f"⏰ 下次日程生成时间: {today_schedule.strftime('%Y-%m-%d %H:%M:%S')} (等待 {int(wait_seconds/3600)}小时{int((wait_seconds%3600)/60)}分钟)")

                # 等待到执行时间
                await asyncio.sleep(wait_seconds)

                # 检查是否还在运行
                if not self.is_running:
                    break

                # 检查今天是否已执行（避免重复）
                current_date = datetime.now().date()
                if self.last_execution_date == current_date:
                    continue

                # 执行日程生成
                logger.info(f"⏰ 开始执行每日自动日程生成 - {current_date}")
                await schedule_generator()
                self.last_execution_date = current_date
                logger.info("✅ 每日自动日程生成完成")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ 定时任务执行出错: {e}", exc_info=True)
                # 出错后等待1分钟再重试
                await asyncio.sleep(60)


class DailyScheduleEventHandler(BaseEventHandler):
    """每日自动日程生成事件处理器"""

    event_type = EventType.ON_START
    handler_name = "daily_schedule_handler"
    handler_description = "每日定时自动生成日程"
    weight = 10
    intercept_message = False

    # 类变量：确保只启动一个调度器
    _scheduler = None
    _scheduler_started = False

    def __init__(self):
        super().__init__()
        self.db = None
        self.llm = None

    def _ensure_initialized(self):
        """确保组件已初始化"""
        if self.db is None:
            self.db = SceneDB()
        if self.llm is None:
            self.llm = LLMClientFactory.create_reply_client(self.get_config)

    async def execute(
        self, message: Optional[MaiMessages]
    ) -> Tuple[bool, bool, Optional[str], Optional[any], Optional[MaiMessages]]:
        """执行事件处理"""
        # 确保只启动一个调度器实例
        if not DailyScheduleEventHandler._scheduler_started:
            DailyScheduleEventHandler._scheduler_started = True
            DailyScheduleEventHandler._scheduler = ScheduleScheduler(self.get_config)

            self._ensure_initialized()

            # 启动时检查是否需要立即生成
            await self._check_and_generate_if_needed()

            # 启动定时任务
            await DailyScheduleEventHandler._scheduler.start(self._generate_daily_schedule)

        return True, True, None, None, None

    async def _check_and_generate_if_needed(self):
        """启动时检查是否需要生成今日日程"""
        try:
            today_date = datetime.now().strftime("%Y-%m-%d")
            last_generated = self.db.get_schedule_metadata(GLOBAL_SCHEDULE_USER, "last_generated")

            if last_generated != today_date:
                logger.info(f"今日日程尚未生成（上次: {last_generated}），立即生成...")
                await self._generate_daily_schedule()
        except Exception as e:
            logger.error(f"检查日程时出错: {e}", exc_info=True)

    async def _generate_daily_schedule(self):
        """生成今日日程"""
        try:
            self._ensure_initialized()
            result = await generate_schedule(self.db, self.llm)
            if result:
                logger.info("自动生成日程成功")
            else:
                logger.warning("自动生成日程失败")
        except Exception as e:
            logger.error(f"生成日程失败: {e}", exc_info=True)

    @classmethod
    def get_handler_info(cls) -> EventHandlerInfo:
        """返回处理器信息"""
        return EventHandlerInfo(
            name=cls.handler_name,
            component_type=ComponentType.EVENT_HANDLER,
            description=cls.handler_description,
            event_type=cls.event_type
        )
