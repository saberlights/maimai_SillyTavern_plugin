"""
场景模式数据库管理
"""
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path
from src.common.logger import get_logger

logger = get_logger("scene_db")


class SceneDB:
    """场景模式数据库管理类"""

    def __init__(self, db_path: str = None):
        if db_path is None:
            # 默认路径：plugins/scene_format_plugin/data/scene.db
            plugin_dir = Path(__file__).parent.parent
            data_dir = plugin_dir / "data"
            data_dir.mkdir(exist_ok=True)
            db_path = str(data_dir / "scene.db")

        self.db_path = db_path
        self._init_database()

    def _init_database(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 表1：日程表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                weekday INTEGER NOT NULL,
                time_start TEXT NOT NULL,
                time_end TEXT NOT NULL,
                activity TEXT NOT NULL,
                description TEXT,
                location TEXT,
                clothing TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, weekday, time_start)
            )
        """)

        # 表2：场景状态表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scene_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL UNIQUE,
                enabled INTEGER DEFAULT 0,
                location TEXT,
                clothing TEXT,
                scene_description TEXT,
                last_activity TEXT,
                last_update_time TIMESTAMP,
                init_time TIMESTAMP,
                user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 缺失 user_id 列的旧表需要迁移
        cursor.execute("PRAGMA table_info(scene_states)")
        columns = [row[1] for row in cursor.fetchall()]
        if "user_id" not in columns:
            cursor.execute("ALTER TABLE scene_states ADD COLUMN user_id TEXT")
        # 添加 nai_enabled 列（如果不存在）
        if "nai_enabled" not in columns:
            cursor.execute("ALTER TABLE scene_states ADD COLUMN nai_enabled INTEGER DEFAULT 0")

        # 表3：场景历史记录
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scene_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                location TEXT,
                clothing TEXT,
                scene_description TEXT,
                user_message TEXT,
                bot_reply TEXT
            )
        """)

        # 表4：预设配置表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS preset_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                preset_name TEXT NOT NULL UNIQUE,
                file_path TEXT,
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT
            )
        """)

        # 表5：预设 prompts 表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS preset_prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                preset_name TEXT NOT NULL,
                identifier TEXT NOT NULL,
                name TEXT,
                role TEXT,
                content TEXT,
                system_prompt INTEGER,
                enabled INTEGER,
                injection_position INTEGER,
                injection_depth INTEGER,
                injection_order INTEGER,
                FOREIGN KEY (preset_name) REFERENCES preset_config(preset_name)
            )
        """)

        # 表6：激活的文风表（全局单例）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_style (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                preset_name TEXT,
                style_identifier TEXT,
                style_name TEXT,
                enabled INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 初始化 active_style 表（确保只有一行）
        cursor.execute("SELECT COUNT(*) FROM active_style")
        if cursor.fetchone()[0] == 0:
            cursor.execute("""
                INSERT INTO active_style (id, enabled)
                VALUES (1, 0)
            """)

        # 表7：角色状态表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS character_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL UNIQUE,
                -- 身体与生理
                body_condition TEXT DEFAULT '{}',
                physiological_state TEXT DEFAULT '呼吸平稳',
                vaginal_state TEXT DEFAULT '放松',
                vaginal_wetness TEXT DEFAULT '正常',
                vaginal_capacity INTEGER DEFAULT 100,
                anal_development INTEGER DEFAULT 0,
                pregnancy_status TEXT DEFAULT '未受孕',
                pregnancy_source TEXT,
                pregnancy_counter INTEGER DEFAULT 0,
                semen_volume INTEGER DEFAULT 0,
                semen_sources TEXT DEFAULT '[]',
                vaginal_foreign TEXT DEFAULT '[]',
                -- 快感与成长
                pleasure_value INTEGER DEFAULT 0,
                pleasure_threshold INTEGER DEFAULT 100,
                corruption_level INTEGER DEFAULT 0,
                fetishes TEXT DEFAULT '{}',
                permanent_mods TEXT DEFAULT '{}',
                inventory TEXT DEFAULT '[]',
                -- 元数据
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()
        logger.info(f"数据库初始化完成: {self.db_path}")

    # ==================== 日程管理 ====================

    def save_schedule(self, user_id: str, weekday: int, schedule_item: Dict[str, Any]):
        """保存单条日程"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT OR REPLACE INTO schedules
                (user_id, weekday, time_start, time_end, activity, description, location, clothing)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                weekday,
                schedule_item.get("time_start"),
                schedule_item.get("time_end"),
                schedule_item.get("activity"),
                schedule_item.get("description", ""),
                schedule_item.get("location", ""),
                schedule_item.get("clothing", "")
            ))
            conn.commit()
        except Exception as e:
            logger.error(f"保存日程失败: {e}")
            conn.rollback()
        finally:
            conn.close()

    def save_schedules_batch(self, user_id: str, schedules: Dict[str, List[Dict]]):
        """批量保存一周日程"""
        weekday_map = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6
        }

        # 先清空该用户的旧日程
        self.clear_user_schedules(user_id)

        # 批量插入
        for day_name, schedule_list in schedules.items():
            weekday = weekday_map.get(day_name.lower())
            if weekday is None:
                continue

            for item in schedule_list:
                self.save_schedule(user_id, weekday, item)

        logger.info(f"已保存用户 {user_id} 的一周日程")

    def clear_user_schedules(self, user_id: str):
        """清空用户的所有日程"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM schedules WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    def get_current_activity(self, user_id: str, current_time: datetime) -> Optional[Dict[str, Any]]:
        """获取当前时间的活动，支持跨午夜的时间段"""
        weekday = current_time.weekday()  # 0=Monday, 6=Sunday
        prev_weekday = (weekday - 1) % 7
        time_minutes = self._time_to_minutes(current_time.strftime("%H:%M"))

        if time_minutes is None:
            return None

        # 先在当天日程中查找（包含当天晚上跨到午夜前的部分）
        current_day_schedules = self.get_day_schedules(user_id, weekday)
        activity = self._match_activity_from_list(
            current_day_schedules,
            time_minutes,
            include_cross_midnight_start=True
        )
        if activity:
            return activity

        # 再在前一天的跨午夜日程中查找凌晨部分
        previous_day_schedules = self.get_day_schedules(user_id, prev_weekday)
        activity = self._match_activity_from_list(
            previous_day_schedules,
            time_minutes,
            include_cross_midnight_start=False
        )
        if activity:
            return activity

        # 如果是凌晨时段(00:00-06:00)且找不到日程，返回默认"睡觉"活动
        if 0 <= time_minutes < 360:  # 0:00-6:00
            logger.info(f"凌晨时段未找到日程，返回默认睡觉活动")
            return {
                "activity": "睡觉",
                "description": "深夜休息",
                "location": "卧室",
                "clothing": "睡衣"
            }

        return None

    @staticmethod
    def _time_to_minutes(time_str: str) -> Optional[int]:
        """将 'HH:MM' 转换为分钟数"""
        try:
            hour_str, minute_str = time_str.split(":")
            hour = int(hour_str)
            minute = int(minute_str)
            return hour * 60 + minute
        except Exception:
            logger.warning(f"无法解析时间格式: {time_str}")
            return None

    def _match_activity_from_list(
        self,
        schedules: List[Dict[str, Any]],
        current_minutes: int,
        include_cross_midnight_start: bool
    ) -> Optional[Dict[str, Any]]:
        """
        根据给定的分钟数在日程列表中查找匹配活动。

        include_cross_midnight_start=True 表示匹配当天从晚上开始跨午夜的部分，
        False 表示匹配凌晨属于前一天的部分。
        """
        for item in schedules:
            start_minutes = self._time_to_minutes(item.get("time_start", ""))
            end_minutes = self._time_to_minutes(item.get("time_end", ""))

            if start_minutes is None or end_minutes is None:
                continue

            crosses_midnight = end_minutes <= start_minutes

            if not crosses_midnight:
                if start_minutes <= current_minutes < end_minutes:
                    return item
            else:
                # 同一天的晚间部分
                if include_cross_midnight_start and current_minutes >= start_minutes:
                    return item
                # 前一天跨到凌晨的部分
                if not include_cross_midnight_start and current_minutes < end_minutes:
                    return item

        return None

    def get_day_schedules(self, user_id: str, weekday: int) -> List[Dict[str, Any]]:
        """获取某天的所有日程"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM schedules
            WHERE user_id = ? AND weekday = ?
            ORDER BY time_start
        """, (user_id, weekday))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    # ==================== 场景状态管理 ====================

    def get_scene_state(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """获取场景状态"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM scene_states WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return dict(row)
        return None

    def get_latest_session_state(self, stream_id: str) -> Optional[Dict[str, Any]]:
        """获取某个stream对应的最近一次场景状态（用于无法确定用户时的回退）"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        pattern = f"{stream_id}:%"
        cursor.execute(
            """
            SELECT * FROM scene_states
            WHERE chat_id LIKE ?
            ORDER BY updated_at DESC
            LIMIT 1
        """,
            (pattern,),
        )

        row = cursor.fetchone()
        conn.close()

        if row:
            return dict(row)
        return None

    def get_state_by_user(self, stream_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """根据 stream_id + user_id 获取场景状态"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        pattern = f"{stream_id}:%"
        cursor.execute(
            """
            SELECT * FROM scene_states
            WHERE chat_id LIKE ? AND user_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
        """,
            (pattern, user_id),
        )

        row = cursor.fetchone()
        conn.close()

        if row:
            return dict(row)
        return None

    def is_scene_enabled(self, chat_id: str) -> bool:
        """检查场景模式是否启用"""
        state = self.get_scene_state(chat_id)
        return state is not None and state.get("enabled", 0) == 1

    def create_scene_state(self, chat_id: str, location: str, clothing: str,
                          scene_description: str, activity: str, user_id: str):
        """创建场景状态"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            cursor.execute("""
                INSERT OR REPLACE INTO scene_states
                (chat_id, enabled, location, clothing, scene_description,
                 last_activity, last_update_time, init_time, user_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (chat_id, 1, location, clothing, scene_description, activity, now, now, user_id, now))
            conn.commit()
        except Exception as e:
            logger.error(f"创建场景状态失败: {e}")
            conn.rollback()
        finally:
            conn.close()

    def update_scene_state(self, chat_id: str, location: str = None, clothing: str = None,
                          scene_description: str = None, activity: str = None):
        """更新场景状态"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        updates = []
        params = []

        if location is not None:
            updates.append("location = ?")
            params.append(location)
        if clothing is not None:
            updates.append("clothing = ?")
            params.append(clothing)
        if scene_description is not None:
            updates.append("scene_description = ?")
            params.append(scene_description)
        if activity is not None:
            updates.append("last_activity = ?")
            params.append(activity)

        if updates:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            updates.append("last_update_time = ?")
            params.append(now)
            updates.append("updated_at = ?")
            params.append(now)

            params.append(chat_id)

            sql = f"UPDATE scene_states SET {', '.join(updates)} WHERE chat_id = ?"
            cursor.execute(sql, params)
            conn.commit()

        conn.close()

    def enable_scene(self, chat_id: str):
        """启用场景模式"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE scene_states SET enabled = 1 WHERE chat_id = ?", (chat_id,))
        conn.commit()
        conn.close()

    def disable_scene(self, chat_id: str):
        """禁用场景模式（保留状态）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE scene_states SET enabled = 0 WHERE chat_id = ?", (chat_id,))
        conn.commit()
        conn.close()

    def clear_scene_state(self, chat_id: str):
        """清空场景状态（用于重新初始化）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scene_states WHERE chat_id = ?", (chat_id,))
        conn.commit()
        conn.close()

    # ==================== 场景历史管理 ====================

    def add_scene_history(self, chat_id: str, location: str, clothing: str,
                         scene_description: str, user_message: str, bot_reply: str):
        """添加场景历史记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO scene_history
            (chat_id, location, clothing, scene_description, user_message, bot_reply)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (chat_id, location, clothing, scene_description, user_message, bot_reply))

        conn.commit()
        conn.close()

    def get_recent_history(self, chat_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """获取最近的历史记录"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM scene_history
            WHERE chat_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (chat_id, limit))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in reversed(rows)]

    # ==================== 预设管理 ====================

    def save_preset(self, preset_name: str, file_path: str, metadata: str = None):
        """保存预设配置"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT OR REPLACE INTO preset_config (preset_name, file_path, metadata)
                VALUES (?, ?, ?)
            """, (preset_name, file_path, metadata))
            conn.commit()
            logger.info(f"预设 '{preset_name}' 已保存")
        except Exception as e:
            logger.error(f"保存预设失败: {e}")
            conn.rollback()
        finally:
            conn.close()

    def save_preset_prompt(self, preset_name: str, prompt_data: Dict[str, Any]):
        """保存预设的单个 prompt"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO preset_prompts
                (preset_name, identifier, name, role, content, system_prompt, enabled,
                 injection_position, injection_depth, injection_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                preset_name,
                prompt_data.get("identifier", ""),
                prompt_data.get("name", ""),
                prompt_data.get("role", ""),
                prompt_data.get("content", ""),
                1 if prompt_data.get("system_prompt") else 0,
                1 if prompt_data.get("enabled", True) else 0,
                prompt_data.get("injection_position", 0),
                prompt_data.get("injection_depth", 0),
                prompt_data.get("injection_order", 0)
            ))
            conn.commit()
        except Exception as e:
            logger.error(f"保存 prompt 失败: {e}")
            conn.rollback()
        finally:
            conn.close()

    def clear_preset_prompts(self, preset_name: str):
        """清空指定预设的所有 prompts"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM preset_prompts WHERE preset_name = ?", (preset_name,))
        conn.commit()
        conn.close()

    def get_preset(self, preset_name: str) -> Optional[Dict[str, Any]]:
        """获取指定预设的配置信息"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM preset_config WHERE preset_name = ?", (preset_name,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return dict(row)
        return None

    def get_preset_list(self) -> List[Dict[str, Any]]:
        """获取所有已导入的预设列表"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM preset_config ORDER BY imported_at DESC")
        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_preset_prompts(self, preset_name: str) -> List[Dict[str, Any]]:
        """获取指定预设的所有 prompts"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM preset_prompts
            WHERE preset_name = ?
            ORDER BY injection_order, injection_position
        """, (preset_name,))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_style_prompts(self, style_identifier: str, preset_name: str) -> List[Dict[str, Any]]:
        """获取指定文风的 prompts（忽略 enabled 字段，因为选择文风就代表要使用它）"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM preset_prompts
            WHERE preset_name = ? AND identifier = ?
            ORDER BY injection_order, injection_position
        """, (preset_name, style_identifier))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def set_active_style(self, preset_name: str, style_identifier: str, style_name: str):
        """设置激活的文风"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            UPDATE active_style
            SET preset_name = ?, style_identifier = ?, style_name = ?, enabled = 1, updated_at = ?
            WHERE id = 1
        """, (preset_name, style_identifier, style_name, now))

        conn.commit()
        conn.close()
        logger.info(f"已激活文风: {style_name} (来自预设 {preset_name})")

    def clear_active_style(self):
        """清除激活的文风"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("UPDATE active_style SET enabled = 0 WHERE id = 1")
        conn.commit()
        conn.close()
        logger.info("已清除激活的文风")

    def get_active_style(self) -> Optional[Dict[str, Any]]:
        """获取当前激活的文风"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM active_style WHERE id = 1")
        row = cursor.fetchone()
        conn.close()

        if row and row["enabled"] == 1:
            return dict(row)
        return None

    def delete_preset(self, preset_name: str):
        """删除预设及其所有 prompts"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("DELETE FROM preset_prompts WHERE preset_name = ?", (preset_name,))
            cursor.execute("DELETE FROM preset_config WHERE preset_name = ?", (preset_name,))
            conn.commit()
            logger.info(f"已删除预设: {preset_name}")
        except Exception as e:
            logger.error(f"删除预设失败: {e}")
            conn.rollback()
        finally:
            conn.close()

    # ==================== 角色状态管理 ====================

    def get_character_status(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """获取角色状态"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM character_status WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return dict(row)
        return None

    def init_character_status(self, chat_id: str):
        """初始化角色状态（如果不存在）"""
        existing = self.get_character_status(chat_id)
        if existing:
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO character_status (chat_id)
                VALUES (?)
            """, (chat_id,))
            conn.commit()
            logger.info(f"角色状态已初始化: {chat_id}")
        except Exception as e:
            logger.error(f"初始化角色状态失败: {e}")
            conn.rollback()
        finally:
            conn.close()

    def update_character_status(self, chat_id: str, updates: Dict[str, Any]):
        """更新角色状态"""
        if not updates:
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 确保状态存在
        self.init_character_status(chat_id)

        update_fields = []
        params = []

        for field, value in updates.items():
            update_fields.append(f"{field} = ?")
            params.append(value)

        if update_fields:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            update_fields.append("updated_at = ?")
            params.append(now)
            params.append(chat_id)

            sql = f"UPDATE character_status SET {', '.join(update_fields)} WHERE chat_id = ?"
            cursor.execute(sql, params)
            conn.commit()
            logger.debug(f"角色状态已更新: {chat_id}, 字段: {list(updates.keys())}")

        conn.close()

    def clear_character_status(self, chat_id: str):
        """清空角色状态（重置为默认值）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM character_status WHERE chat_id = ?", (chat_id,))
        conn.commit()
        conn.close()
        logger.info(f"角色状态已清空: {chat_id}")

    # ==================== NAI 生图开关管理 ====================

    def set_nai_enabled(self, chat_id: str, enabled: bool):
        """设置 NAI 生图开关"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            UPDATE scene_states
            SET nai_enabled = ?, updated_at = ?
            WHERE chat_id = ?
        """, (1 if enabled else 0, now, chat_id))

        conn.commit()
        conn.close()
        logger.info(f"NAI 生图开关已设置为 {enabled}: {chat_id}")

    def get_nai_enabled(self, chat_id: str) -> bool:
        """获取 NAI 生图开关状态"""
        state = self.get_scene_state(chat_id)
        if state:
            return state.get("nai_enabled", 0) == 1
        return False
