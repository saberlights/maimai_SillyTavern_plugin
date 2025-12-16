"""
日程生成与查看命令
"""
import json
import re
from datetime import datetime
from typing import Tuple, Optional, List
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.component_types import CommandInfo, ComponentType
from src.chat.message_receive.message import MessageRecv
from src.config.config import global_config
from src.common.logger import get_logger
from ..core.scene_db import SceneDB
from ..core.llm_client import LLMClientFactory

logger = get_logger("schedule_command")

# 全局用户ID
GLOBAL_SCHEDULE_USER = "global_schedule"


class ScheduleGenerateCommand(BaseCommand):
    """日程生成命令 - 让LLM自由发挥生成有随机性的日程"""

    command_name = "场景日程生成"
    command_description = "生成今日日程安排"
    command_pattern = r"^/s(?:cene|c)\s+(?:schedule|日程)(?:\s+(?:generate|gen|生成))?$"

    def __init__(self, message: MessageRecv, plugin_config: Optional[dict] = None):
        super().__init__(message, plugin_config)
        self.db = SceneDB()
        self.llm = LLMClientFactory.create_reply_client(self.get_config)

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        """执行命令"""
        # 权限检查
        from .admin_command import SceneAdminCommand
        message_info = self.message.message_info
        user_id = str(message_info.user_info.user_id)
        platform = getattr(message_info, "platform", "")
        group_info = getattr(message_info, "group_info", None)
        chat_id = group_info.group_id if group_info and getattr(group_info, "group_id", None) else user_id

        if not SceneAdminCommand.check_user_permission(platform, chat_id, user_id, self.get_config):
            await self.send_text("❌ 当前会话已开启管理员模式，仅管理员可使用")
            return False, "没有权限", 2

        logger.info(f"[ScheduleCommand] 生成全局日程")
        await self.send_text("📅 正在生成今日日程...")

        try:
            result = await generate_schedule(self.db, self.llm)
            if result:
                schedules, reply = result
                await self.send_text(reply)
                return True, "日程生成成功", 2
            else:
                await self.send_text("❌ 日程生成失败，请稍后重试")
                return False, "日程生成失败", 2

        except Exception as e:
            logger.error(f"生成日程时出错: {e}", exc_info=True)
            await self.send_text(f"❌ 生成日程失败: {str(e)}")
            return False, str(e), 2

    @classmethod
    def get_command_info(cls) -> CommandInfo:
        return CommandInfo(
            name=cls.command_name,
            component_type=ComponentType.COMMAND,
            description=cls.command_description,
            command_pattern=cls.command_pattern
        )


class ScheduleViewCommand(BaseCommand):
    """日程查看命令"""

    command_name = "场景日程查看"
    command_description = "查看当前日程安排"
    command_pattern = r"^/s(?:cene|c)\s+(?:schedule|日程)\s+(?:view|查看|show|list)$"

    def __init__(self, message: MessageRecv, plugin_config: Optional[dict] = None):
        super().__init__(message, plugin_config)
        self.db = SceneDB()

    async def execute(self) -> Tuple[bool, Optional[str], int]:
        """执行命令"""
        try:
            today = datetime.now()
            today_weekday = today.weekday()
            today_date = today.strftime("%Y-%m-%d")

            # 获取日程
            schedules = self.db.get_day_schedules(GLOBAL_SCHEDULE_USER, today_weekday)

            # 获取生成日期
            last_generated = self.db.get_schedule_metadata(GLOBAL_SCHEDULE_USER, "last_generated")

            if not schedules:
                await self.send_text("📅 当前没有日程安排\n\n使用 /sc 日程 生成今日日程")
                return True, "无日程", 2

            # 检查日程是否过期
            is_outdated = last_generated and last_generated != today_date

            # 格式化输出
            reply = self._format_view_reply(schedules, today, last_generated, is_outdated)
            await self.send_text(reply)
            return True, "查看成功", 2

        except Exception as e:
            logger.error(f"查看日程时出错: {e}", exc_info=True)
            await self.send_text(f"❌ 查看日程失败: {str(e)}")
            return False, str(e), 2

    def _format_view_reply(self, schedules: List[dict], today: datetime,
                           last_generated: Optional[str], is_outdated: bool) -> str:
        """格式化查看回复"""
        weekday_names = {
            0: "星期一", 1: "星期二", 2: "星期三", 3: "星期四",
            4: "星期五", 5: "星期六", 6: "星期日"
        }
        weekday_cn = weekday_names[today.weekday()]
        date_str = today.strftime("%Y年%m月%d日")
        current_time = today.strftime("%H:%M")

        lines = []

        if is_outdated:
            lines.append("⚠️ 日程已过期，建议重新生成\n")

        lines.append(f"📅 【{date_str} {weekday_cn}】")
        lines.append(f"当前时间: {current_time}\n")

        # 找到当前活动
        current_minutes = today.hour * 60 + today.minute
        current_activity_idx = -1

        for idx, item in enumerate(schedules):
            start_parts = item.get('time_start', '00:00').split(':')
            end_parts = item.get('time_end', '00:00').split(':')
            start_min = int(start_parts[0]) * 60 + int(start_parts[1])
            end_min = int(end_parts[0]) * 60 + int(end_parts[1])

            if start_min <= current_minutes < end_min:
                current_activity_idx = idx
                break

        # 输出日程
        for idx, item in enumerate(schedules):
            time_range = f"{item.get('time_start', '??:??')}-{item.get('time_end', '??:??')}"
            activity = item.get('activity', '未知')
            location = item.get('location', '')
            desc = item.get('description', '')
            clothing = item.get('clothing', '')

            # 标记当前活动
            if idx == current_activity_idx:
                prefix = "▶"
            else:
                prefix = " "

            line = f"{prefix} {time_range}  {activity}"
            if location:
                line += f" @ {location}"
            lines.append(line)

            # 显示详情
            details = []
            if desc:
                details.append(desc)
            if clothing:
                details.append(f"着装: {clothing}")
            if details:
                lines.append(f"    └ {' | '.join(details)}")

        lines.append("")
        if last_generated:
            lines.append(f"生成于: {last_generated}")

        return "\n".join(lines)

    @classmethod
    def get_command_info(cls) -> CommandInfo:
        return CommandInfo(
            name=cls.command_name,
            component_type=ComponentType.COMMAND,
            description=cls.command_description,
            command_pattern=cls.command_pattern
        )


# ==================== 核心生成逻辑 ====================

def _get_bot_config() -> dict:
    """获取bot配置"""
    return {
        "name": global_config.bot.nickname,
        "personality": getattr(global_config.personality, "personality", ""),
        "reply_style": getattr(global_config.personality, "reply_style", "")
    }


def _build_schedule_prompt(bot_config: dict) -> str:
    """构建日程生成prompt - 让LLM自由发挥"""
    today = datetime.now()
    weekday_names = {
        0: "星期一", 1: "星期二", 2: "星期三", 3: "星期四",
        4: "星期五", 5: "星期六", 6: "星期日"
    }
    weekday_cn = weekday_names[today.weekday()]
    date_str = today.strftime("%Y年%m月%d日")
    is_weekend = today.weekday() >= 5

    return f"""【角色信息】
名字：{bot_config['name']}

【性格与身份】
{bot_config['personality']}

【今天】
{date_str} {weekday_cn}
{"周末，可以放松一下" if is_weekend else "工作日"}

【任务】
根据角色的身份和性格，生成今天的日程安排。

【核心要求】
1. 要有真实感和生活气息，像真人的一天
2. 每天都要不一样！不要千篇一律
3. 可以有随机的小事件、意外、临时安排
4. 时间不要太整齐（比如7:23而不是7:00）
5. 活动之间可以有空隙、发呆、摸鱼
6. description要写得生动具体，体现当天的心情和状态
7. 根据角色性格来安排活动，不要太死板

【发挥空间】
- 今天的心情状态可以随机
- 可能有意外事件（快递、电话、朋友来访等）
- 可能熬夜或早睡
- 可能赖床或早起
- 用餐可能有变化（外卖、换口味、没胃口等）
- 晚间活动可以多样（追剧、游戏、看书、运动等）

【格式要求】
- time_start/time_end: HH:MM格式
- activity: 简短活动名（2-6字）
- description: 具体描述（写心情、细节、小事件）
- location: 地点
- clothing: 着装

【输出】
```json
{{
  "schedule": [
    {{
      "time_start": "07:23",
      "time_end": "07:45",
      "activity": "赖床起床",
      "description": "闹钟响了两遍才爬起来，有点困",
      "location": "卧室",
      "clothing": "睡衣"
    }}
  ]
}}
```

生成完整的今日日程（从起床到睡觉），要有随机性和变化："""


def _parse_schedule_json(llm_response: str) -> Optional[dict]:
    """解析LLM返回的JSON"""
    try:
        json_match = re.search(r'```json\s*(.*?)\s*```', llm_response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 尝试找到JSON对象
            json_match = re.search(r'\{[\s\S]*"schedule"[\s\S]*\}', llm_response)
            if json_match:
                json_str = json_match.group(0)
            else:
                json_str = llm_response

        schedules = json.loads(json_str)

        if "schedule" not in schedules:
            logger.warning("日程JSON缺少 schedule 字段")
            return None

        if not isinstance(schedules["schedule"], list):
            logger.warning("schedule 字段不是列表")
            return None

        return schedules

    except json.JSONDecodeError as e:
        logger.error(f"JSON解析失败: {e}")
        return None
    except Exception as e:
        logger.error(f"解析日程时出错: {e}")
        return None


def _format_schedule_reply(schedules: dict) -> str:
    """格式化日程回复"""
    weekday_names = {
        0: "星期一", 1: "星期二", 2: "星期三", 3: "星期四",
        4: "星期五", 5: "星期六", 6: "星期日"
    }
    today = datetime.now()
    weekday_cn = weekday_names[today.weekday()]
    date_str = today.strftime("%Y年%m月%d日")

    lines = [f"📅 今日日程生成成功！\n"]
    lines.append(f"【{date_str} {weekday_cn}】\n")

    for item in schedules.get("schedule", []):
        time_start = item.get('time_start', '??:??')
        time_end = item.get('time_end', '??:??')
        time_range = f"{time_start}-{time_end}"
        activity = item.get('activity', '未知')
        location = item.get('location', '')
        desc = item.get('description', '')

        line = f"  {time_range}  {activity}"
        if location:
            line += f" @ {location}"
        if desc:
            line += f"\n    └ {desc}"
        lines.append(line)

    lines.append("")
    lines.append("使用 /sc schedule view 查看日程")
    lines.append("使用 /sc on 开始场景对话")

    return "\n".join(lines)


async def generate_schedule(db: SceneDB, llm_client) -> Optional[Tuple[dict, str]]:
    """
    生成日程的核心逻辑（供命令和定时任务共用）

    Returns:
        (schedules, reply_text) 或 None
    """
    try:
        bot_config = _get_bot_config()
        prompt = _build_schedule_prompt(bot_config)

        logger.debug(f"日程生成Prompt:\n{prompt}")

        llm_response, _ = await llm_client.generate_response_async(prompt)

        logger.debug(f"LLM返回:\n{llm_response}")

        schedules = _parse_schedule_json(llm_response)

        if not schedules:
            logger.error("JSON解析失败")
            return None

        # 保存到数据库
        today = datetime.now()
        today_weekday = today.weekday()
        today_date = today.strftime("%Y-%m-%d")

        db.clear_user_schedules(GLOBAL_SCHEDULE_USER)
        for item in schedules["schedule"]:
            db.save_schedule(GLOBAL_SCHEDULE_USER, today_weekday, item)

        # 保存生成日期
        db.set_schedule_metadata(GLOBAL_SCHEDULE_USER, "last_generated", today_date)

        logger.info(f"日程已保存，生成日期: {today_date}")

        reply = _format_schedule_reply(schedules)
        return schedules, reply

    except Exception as e:
        logger.error(f"生成日程失败: {e}", exc_info=True)
        return None
