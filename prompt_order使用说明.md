# Prompt Order 架构说明

## 📌 重大升级：通用预设支持

插件现在可以**通用地处理任何 SillyTavern 预设格式**，无需针对不同预设进行手动配置！

## 🔍 问题背景

### 旧版本的局限性

之前的实现使用**硬编码的模式匹配**来识别预设内容：

```python
# 旧方法：依赖固定的命名模式
if "指南" in name or "Zhinan" in name:
    # 这是指南
if "禁词表" in name or "禁词" in name:
    # 这是禁词表
if "✔️" in name or "文风" in name:
    # 这是文风
```

**问题**：不同预设的命名差异巨大

| 预设 | 指南命名 | 禁词表命名 | 文风命名 |
|------|---------|-----------|---------|
| Izumi预设V0.2 | "指南（不能关）" | "禁词表" | "✔️文风-电竞解说" |
| mr鹿鹿预设Code2.7 | "鹿鹿 指南" | "鹿鹿 禁词表" | "mr 鹿鹿 文风" + wrapper markers |

**结果**：每次遇到新预设都需要修改代码！❌

## ✅ 新版本：基于 JSON 结构的通用方案

### SillyTavern 的 `prompt_order` 结构

所有标准 SillyTavern 预设都包含 `prompt_order` 数组：

```json
{
  "prompt_order": [
    {
      "character_id": 100000,  // 系统默认配置
      "order": [...]
    },
    {
      "character_id": 100001,  // 用户自定义配置 ⭐
      "order": [
        {
          "identifier": "main",
          "enabled": true
        },
        {
          "identifier": "2dbec179-06c2-4d22-80d8-1d0d9649ef72",
          "enabled": true
        },
        {
          "identifier": "your-style-uuid",
          "enabled": false
        }
      ]
    }
  ]
}
```

**关键发现**：
- `character_id = 100001` 包含用户的自定义 prompt 配置
- `enabled` 字段标识哪些 prompts 应该使用
- `order` 数组定义了 prompts 的排列顺序
- `identifier` 是 prompt 的唯一标识符

## 🏗️ 新架构实现

### 双模式系统

```
导入预设
    ↓
保存完整 prompt_order 到数据库
    ↓
构建 prompt 时
    ↓
尝试读取 prompt_order ────→ 成功 ──→ 【模式1】基于 prompt_order 构建
    ↓                                      ↓
    失败（旧版预设）                  使用 JSON 的顺序和启用状态
    ↓                                      ↓
【模式2】模式匹配构建 ←──────────── 回退备选方案
    ↓
完成
```

### 核心代码流程

#### 1. 导入时保存 prompt_order

```python
# preset_manager.py: _parse_and_save_preset()

metadata = json.dumps({
    "temperature": preset_data.get("temperature"),
    "top_p": preset_data.get("top_p"),
    "max_tokens": preset_data.get("openai_max_tokens"),
    "prompt_order": preset_data.get("prompt_order", [])  # ⭐ 保存完整结构
})

self.db.save_preset(preset_name, file_path, metadata)
```

#### 2. 读取 prompt_order

```python
# preset_manager.py: _get_ordered_prompts()

def _get_ordered_prompts(self, preset_name: str) -> Optional[List[Dict]]:
    # 1. 从数据库读取预设元数据
    metadata = self.db.get_preset(preset_name)
    preset_data = json.loads(metadata.get("metadata", "{}"))
    prompt_order_list = preset_data.get("prompt_order", [])

    # 2. 找到 character_id=100001 的配置
    order_config = None
    for config in prompt_order_list:
        if config.get("character_id") == 100001:
            order_config = config
            break

    # 3. 获取所有 prompts 并建立索引
    all_prompts = self.db.get_preset_prompts(preset_name)
    prompts_by_id = {p["identifier"]: p for p in all_prompts}

    # 4. 按 order 构建有序列表（只包含启用的）
    ordered = []
    for item in order_config.get("order", []):
        identifier = item.get("identifier")
        enabled = item.get("enabled", False)

        if enabled and identifier in prompts_by_id:
            ordered.append(prompts_by_id[identifier])

    return ordered
```

#### 3. 使用 prompt_order 构建

```python
# preset_manager.py: _build_from_prompt_order()

def _build_from_prompt_order(self, base_prompt, ordered_prompts,
                              style_identifier, include_main,
                              include_guidelines, include_style):
    prompt_parts = []

    for prompt in ordered_prompts:
        identifier = prompt.get("identifier")
        name = prompt.get("name")
        content = prompt.get("content", "").strip()

        # 跳过 marker prompts（占位符）
        if prompt.get("marker", False) or not content:
            continue

        # 1. 主提示
        if identifier == "main":
            if include_main:
                prompt_parts.append(content)
            continue

        # 2. 指南和禁词表（仍需部分模式匹配来分类）
        if "指南" in name or "Zhinan" in name or "禁词" in name:
            if include_guidelines:
                prompt_parts.append(content)
            continue

        # 3. 文风相关
        if identifier == style_identifier:
            if include_style:
                prompt_parts.append(content)
            continue

        # 4. 系统级 prompts（不包含）
        if identifier in ["nsfw", "jailbreak", "chatHistory", ...]:
            continue

        # 5. 其他启用的 prompts（质量控制规则等）
        if include_guidelines:
            prompt_parts.append(content)

    # 在合适位置插入 base_prompt
    if added_count["main"] > 0:
        prompt_parts.insert(added_count["main"], base_prompt)
    else:
        prompt_parts.insert(0, base_prompt)

    return "\n\n".join(prompt_parts)
```

## 🎯 两种模式的差异

### 模式1：基于 prompt_order（推荐）

**适用于**：包含 `prompt_order` 结构的标准 SillyTavern 预设

**优势**：
- ✅ 完全遵循预设的原始顺序
- ✅ 尊重 `enabled` 字段的启用/禁用状态
- ✅ 不需要猜测或模式匹配
- ✅ 通用性强，适配任何预设

**工作流程**：
1. 读取 `prompt_order` 中 `character_id=100001` 的配置
2. 遍历 `order` 数组，找出所有 `enabled=true` 的 prompts
3. 按 JSON 中的顺序添加到 prompt 列表
4. 过滤掉 marker prompts 和系统级 prompts
5. 组装最终 prompt

**日志输出示例**：
```
[preset_manager] 从 prompt_order 获取到 12 个启用的 prompts
[preset_manager] [prompt_order模式] 已添加: main=1, guidelines=2, style=1, other=8
```

### 模式2：模式匹配（回退）

**适用于**：不包含 `prompt_order` 或格式不标准的预设

**缺点**：
- ⚠️ 依赖名称模式匹配
- ⚠️ 可能遗漏某些 prompts
- ⚠️ 顺序不一定符合预设原意

**工作流程**：
1. 通过 `identifier="main"` 查找主提示
2. 通过名称包含 "指南"、"Zhinan" 查找指南
3. 通过名称包含 "禁词表"、"禁词" 查找禁词表
4. 通过 `identifier=style_identifier` 查找文风
5. 按固定顺序组装

**日志输出示例**：
```
[preset_manager] 未找到 prompt_order 结构,使用传统方法
[preset_manager] 已添加主提示
[preset_manager] 已添加 1 个指南规则
[preset_manager] 已添加禁词表
[preset_manager] 已添加文风
[preset_manager] [模式匹配模式] 已构建完整预设 prompt，包含 5 个部分
```

## 📊 兼容性测试

### Izumi预设V0.2.json

```json
"prompt_order": [
  {
    "character_id": 100001,
    "order": [
      {"identifier": "main", "enabled": true},
      {"identifier": "jailbreak", "enabled": false},
      {"identifier": "f45c69d9-db20-4d10-8e86-255e8f57f0a7", "enabled": true},
      {"identifier": "2dbec179-06c2-4d22-80d8-1d0d9649ef72", "enabled": true},
      ...
    ]
  }
]
```

**结果**：✅ 使用模式1（prompt_order）
- 主提示：1个
- 指南：1个
- 禁词表：1个
- 文风（选择时）：1个
- 其他质量控制规则：若干

### mr鹿鹿预设Code2.7.json

```json
"prompt_order": [
  {
    "character_id": 100001,
    "order": [
      {"identifier": "main", "enabled": true},
      {"identifier": "0e1b4c92-348d-4b12-86eb-682cea3947d1", "enabled": true},
      {"identifier": "mr-lulu-style-uuid", "enabled": true},
      ...
    ]
  }
]
```

**结果**：✅ 使用模式1（prompt_order）
- 即使使用 wrapper markers（"--------文风----"）
- 即使名称不同（"鹿鹿 禁词表" vs "禁词表"）
- 依然能正确识别和应用所有内容

## 🔧 关键设计决策

### 1. 为什么仍然保留部分模式匹配？

```python
if "指南" in name or "Zhinan" in name or "禁词" in name:
    if include_guidelines:
        prompt_parts.append(content)
```

**原因**：用于**分类**而非**发现**

- prompt_order 已经告诉我们**哪些 prompts 要使用**
- 模式匹配用于**区分它们的类型**（主提示 vs 指南 vs 文风）
- 这样才能响应 `include_main`、`include_guidelines`、`include_style` 参数

### 2. 为什么排除系统级 prompts？

```python
if identifier in ["nsfw", "jailbreak", "chatHistory", "worldInfoBefore",
                 "worldInfoAfter", "dialogueExamples", "charDescription",
                 "charPersonality", "scenario", "personaDescription",
                 "enhanceDefinitions"]:
    continue
```

**原因**：MaiBot 自己管理这些内容

- `chatHistory`：MaiBot 有自己的历史记录系统
- `charDescription`、`charPersonality`：来自 MaiBot 的配置文件
- `scenario`：场景插件自己生成
- `worldInfoBefore/After`：不适用于 MaiBot 场景

### 3. 为什么将 base_prompt 插入到 main 之后？

```python
if added_count["main"] > 0:
    prompt_parts.insert(added_count["main"], base_prompt)
else:
    prompt_parts.insert(0, base_prompt)
```

**原因**：层级结构设计

```
1. Main（定义AI身份）
    ↓
2. Base Prompt（场景任务和角色信息）
    ↓
3. Guidelines（写作规则）
    ↓
4. Style（文风）
```

这样 AI 先知道自己是谁（Main），然后知道要做什么（Base），最后知道怎么做（Guidelines + Style）。

## 🚀 使用方法（无变化）

用户使用方式**完全不变**：

```bash
# 1. 导入任意 SillyTavern 预设
/scene preset import Izumi预设V0.2.json
/scene preset import mr鹿鹿预设Code2.7.json

# 2. 查看可用文风
/scene preset list

# 3. 激活文风（自动应用完整预设）
/scene preset use <style_identifier>

# 4. 启动场景模式
/scene on
```

插件会**自动检测**预设格式并选择最佳处理方式！

## 📝 总结

### 升级前后对比

| 特性 | 旧版本 | 新版本 |
|------|--------|--------|
| 预设兼容性 | ❌ 依赖固定命名 | ✅ 通用支持 |
| 添加新预设 | ❌ 需要修改代码 | ✅ 零配置导入 |
| Prompt 顺序 | ⚠️ 固定硬编码 | ✅ 遵循原始顺序 |
| 启用状态 | ❌ 忽略 | ✅ 尊重 enabled 字段 |
| 回退机制 | ❌ 无 | ✅ 模式匹配兜底 |

### 核心优势

1. **通用性**：适配任何标准 SillyTavern 预设，无需修改代码
2. **准确性**：完全遵循预设的原始结构和顺序
3. **兼容性**：自动检测并回退到模式匹配
4. **可维护性**：减少硬编码，降低维护成本

现在你可以放心地导入任何 SillyTavern 预设，插件会智能处理！🎉
