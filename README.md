# 场景格式插件

为 MaiBot 提供沉浸式的场景对话体验。通过日程生成、场景描述和智能状态管理，让对话更具真实感和互动性。

## 功能特性

- **日程系统**：每天自动生成日程，支持手动生成和查看
- **场景模式**：提供 {地点}{着装}{场景} 格式的沉浸式对话
- **智能续接**：自动检测历史状态并生成过渡场景
- **双模型架构**：Planner 判断状态变化 + Reply 生成场景内容
- **状态持久化**：SQLite 保存日程和场景状态
- **NAI 配图**：支持智能场景配图（需配置 NAI API）
- **文风系统**：内置多种文风，可自由切换

## 命令列表

### 场景控制
| 命令 | 说明 |
|------|------|
| `/sc on` | 启动场景模式（有历史则续接） |
| `/sc off` | 关闭场景模式（保留状态） |
| `/sc init` | 重新初始化场景 |
| `/sc init <描述>` | 自定义初始化场景 |

### 日程管理
| 命令 | 说明 |
|------|------|
| `/sc 日程` | 手动生成今日日程 |
| `/sc schedule view` | 查看当前日程 |

日程每天自动生成（默认凌晨5点），无需手动操作。

### 状态管理
| 命令 | 说明 |
|------|------|
| `/sc status` | 查看角色状态栏 |
| `/sc status history` | 查看状态变化历史 |
| `/sc status reset` | 重置角色状态 |

### 文风管理
| 命令 | 说明 |
|------|------|
| `/sc style list` | 列出所有文风 |
| `/sc style use <n>` | 选择文风（序号或名称） |
| `/sc style clear` | 清除文风 |

### NAI 配图
| 命令 | 说明 |
|------|------|
| `/sc nai on` | 开启场景配图 |
| `/sc nai off` | 关闭场景配图 |
| `/sc nai` | 查看开关状态 |

### 其他
| 命令 | 说明 |
|------|------|
| `/sc help` | 显示帮助信息 |
| `/sc admin on/off` | 开启/关闭管理员模式 |

## 快速开始

```
1. /sc on          # 启动场景（日程会自动生成）
2. /sc style use 1 # 选择文风（可选）
3. /sc nai on      # 开启配图（可选）
4. 开始对话~
```

## 配置说明

### config.toml

```toml
[plugin]
enabled = true

[scene]
# 场景描述最大长度
max_scene_length = 1000
# 模型模式：dual（双模型）或 single（单模型）
model_mode = "dual"

[scene.schedule]
# 是否启用每日自动生成日程
enabled = true
# 每天自动生成日程的时间（HH:MM格式）
time = "05:00"

[scene.status_bar]
# 是否显示状态栏
enabled = true
# 显示模式：compact/full/changes_only
display_mode = "compact"

[admin]
# 管理员用户ID列表
admin_users = ["123456"]
# 默认是否启用管理员模式
default_admin_mode = true

[llm.planner]
# Planner 模型配置（用于状态判断）
use_custom_api = false

[llm.reply]
# Reply 模型配置（用于场景生成）
use_custom_api = false

[nai]
# NAI 生图配置
base_url = "https://your-nai-api.com"
api_key = "your-api-key"
trigger_probability = 0.3
```

## 数据存储

数据库位于 `data/scene.db`，包含以下表：

- `schedules` - 日程表
- `scene_states` - 场景状态
- `scene_history` - 场景历史
- `active_styles` - 激活的文风
- `character_status` - 角色状态
- `metadata` - 元数据（日程生成日期等）

## 许可证

本插件为 MaiBot 的一部分，遵循 MaiBot 的许可证。
