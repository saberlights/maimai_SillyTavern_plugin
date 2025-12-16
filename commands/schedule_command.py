"""
日程生成命令
"""
import json
import re
from typing import Tuple, Optional
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.component_types import CommandInfo, ComponentType
from src.chat.message_receive.message import MessageRecv
from src.config.config import global_config
from src.common.logger import get_logger
from ..core.scene_db import SceneDB
from ..core.llm_client import LLMClientFactory

logger = get_logger("schedule_generate_command")


class ScheduleGenerateCommand(BaseCommand):
    """日程生成命令 - 根据bot人设生成一周日程"""

    command_name = "场景日程"
    command_description = "根据bot人设生成一周场景日程安排"
    command_pattern = r"^/s(?:cene|c)\s+(?:schedule|日程).*$"

    def __init__(self, message: MessageRecv, plugin_config: Optional[dict] = None):
        super().__init__(message, plugin_config)
        self.db = SceneDB()
        # 使用 reply 模型生成日程（根据配置选择自定义 API 或 MaiBot 原生）
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

        # 使用固定的全局 user_id，所有用户共享同一个日程
        global_user_id = "global_schedule"

        logger.info(f"[ScheduleCommand] 生成全局日程")

        # 发送处理中反馈
        await self.send_text("📅 正在生成今日日程...")

        try:
            # 1. 读取bot人设
            bot_config = self._get_bot_config()

            logger.info(f"[ScheduleCommand] Bot配置: {bot_config}")

            # 2. 构建prompt
            prompt = self._build_schedule_generation_prompt(bot_config)

            logger.debug(f"日程生成Prompt:\n{prompt}")

            # 3. 调用LLM
            llm_response, _ = await self.llm.generate_response_async(prompt)

            logger.debug(f"LLM返回:\n{llm_response}")

            # 4. 解析JSON
            schedules = self._parse_schedule_json(llm_response)

            if not schedules:
                logger.error(f"[ScheduleCommand] JSON解析失败")
                error_msg = "❌ 日程生成失败，请稍后重试"
                await self.send_text(error_msg)
                return False, "JSON解析失败", 2

            # 5. 保存到数据库（仅保存当天，先清理旧数据）
            from datetime import datetime
            today_weekday = datetime.now().weekday()  # 0=Monday, 6=Sunday

            # 确保旧日程不会跨天残留
            self.db.clear_user_schedules(global_user_id)
            for item in schedules["schedule"]:
                self.db.save_schedule(global_user_id, today_weekday, item)

            logger.info(f"[ScheduleCommand] 全局日程已保存到数据库")

            # 6. 生成回复
            reply = self._format_schedule_reply(schedules)

            logger.info(f"[ScheduleCommand] 生成回复成功，长度: {len(reply)}")

            await self.send_text(reply)
            return True, "日程生成成功", 2

        except Exception as e:
            logger.error(f"生成日程时出错: {e}", exc_info=True)
            error_msg = f"❌ 生成日程失败: {str(e)}"
            await self.send_text(error_msg)
            return False, str(e), 2

    def _get_bot_config(self) -> dict:
        """获取bot配置（从全局配置读取）"""
        return {
            "name": global_config.bot.nickname,
            "personality": getattr(global_config.personality, "personality", ""),
            "reply_style": getattr(global_config.personality, "reply_style", "")
        }

    def _build_schedule_generation_prompt(self, bot_config: dict) -> str:
        """构建日程生成prompt"""
        from datetime import datetime

        # 获取今天是星期几
        weekday_names = {
            0: "星期一", 1: "星期二", 2: "星期三", 3: "星期四",
            4: "星期五", 5: "星期六", 6: "星期日"
        }
        today = datetime.now()
        weekday_cn = weekday_names[today.weekday()]

        return f"""【Bot人设】
名字：{bot_config['name']}

【性格特质与身份】
{bot_config['personality']}

【回复风格】
{bot_config['reply_style']}

【任务】
根据以上人设，生成符合这个角色的【今天（{weekday_cn}）的日程安排】。

【要求】
1. 符合角色身份特征和生活背景
2. 符合作息习惯（合理的睡眠和用餐时间）
3. 包含日常活动（起床、吃饭、睡觉、学习/工作、娱乐、运动等）
4. 体现角色的兴趣爱好和特点
5. 每个活动包含：
   - time_start: 开始时间（HH:MM格式）
   - time_end: 结束时间（HH:MM格式）
   - activity: 活动名称（简短，如"上课"、"午餐"）
   - description: 活动描述（可选，如"数学和英语课"）
   - location: 地点（如"教室"、"食堂"、"宿舍"）
   - clothing: 着装（如"校服"、"便服"、"运动服"、"睡衣"）

【注意】
- 时间不要重叠
- 睡觉时间要合理（通常22:00-06:00或23:00-07:00）
- 根据今天是{weekday_cn}来安排活动（工作日/周末有所不同）
- 至少包含：起床、三餐、主要活动、睡觉

【输出格式】
严格按照JSON格式输出，用```json包裹：

```json
{{
  "schedule": [
    {{
      "time_start": "07:00",
      "time_end": "07:30",
      "activity": "起床洗漱",
      "description": "",
      "location": "宿舍",
      "clothing": "睡衣"
    }},
    {{
      "time_start": "07:30",
      "time_end": "08:00",
      "activity": "早餐",
      "description": "",
      "location": "食堂",
      "clothing": "便服"
    }},
    {{
      "time_start": "08:00",
      "time_end": "12:00",
      "activity": "上课",
      "description": "数学、英语",
      "location": "教室",
      "clothing": "校服"
    }}
  ]
}}
```

请生成完整的今日日程。"""

    def _parse_schedule_json(self, llm_response: str) -> Optional[dict]:
        """解析LLM返回的JSON"""
        try:
            # 提取```json包裹的内容
            json_match = re.search(r'```json\s*(.*?)\s*```', llm_response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # 尝试直接解析
                json_str = llm_response

            # 解析JSON
            schedules = json.loads(json_str)

            # 验证格式
            if "schedule" not in schedules:
                logger.warning(f"日程JSON缺少 schedule 字段")
                return None

            if not isinstance(schedules["schedule"], list):
                logger.warning(f"schedule 字段不是列表")
                return None

            return schedules

        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}")
            return None
        except Exception as e:
            logger.error(f"解析日程时出错: {e}")
            return None

    def _format_schedule_reply(self, schedules: dict) -> str:
        """格式化日程回复"""
        from datetime import datetime

        weekday_names = {
            0: "星期一", 1: "星期二", 2: "星期三", 3: "星期四",
            4: "星期五", 5: "星期六", 6: "星期日"
        }
        today = datetime.now()
        weekday_cn = weekday_names[today.weekday()]
        date_str = today.strftime("%Y年%m月%d日")

        reply_lines = [f"✅ 今日日程生成成功！\n"]
        reply_lines.append(f"【{date_str} {weekday_cn}】")

        schedule_list = schedules.get("schedule", [])
        for item in schedule_list:
            time_range = f"{item['time_start']}-{item['time_end']}"
            activity = item['activity']
            location = item.get('location', '')
            desc = item.get('description', '')

            if desc:
                activity_text = f"{activity}（{desc}）"
            else:
                activity_text = activity

            reply_lines.append(f"  {time_range}  {activity_text} @ {location}")

        reply_lines.append("")
        reply_lines.append("已保存到数据库，可以使用 /scene on 开始场景对话")

        return "\n".join(reply_lines)

    @classmethod
    def get_command_info(cls) -> CommandInfo:
        """返回命令信息"""
        return CommandInfo(
            name=cls.command_name,
            component_type=ComponentType.COMMAND,
            description=cls.command_description,
            command_pattern=cls.command_pattern
        )
