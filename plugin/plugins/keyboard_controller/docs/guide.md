# 按键控制插件

让猫娘通过键盘/鼠标操作电脑上的游戏或软件，并支持**截图 + OCR**，让非视觉模型也能"看到"屏幕。定位并锁定一个目标窗口后，可以向它注入按键、组合键、文本、鼠标操作，或截取它的画面并识别文字。插件默认随 N.E.K.O 自动启动。

## 猫娘怎么用（对话直接调用）

插件核心入口同时注册为 LLM 工具（`@llm_tool`），猫娘在对话中可直接调用，工具名以 `keyboard_` 为前缀：

| 工具名 | 作用 |
|---|---|
| `keyboard_find_windows` | 按标题/进程名搜索窗口 |
| `keyboard_set_target` | 锁定目标窗口（操作前必须先调用） |
| `keyboard_get_target` | 查询当前目标 |
| `keyboard_clear_target` | 解除目标 |
| `keyboard_press_keys` | 注入单键/组合键 |
| `keyboard_hold_key` | 长按按键/组合键指定秒数 |
| `keyboard_type_text` | 向目标窗口输入文本（长文本自动走剪贴板粘贴） |
| `keyboard_press_sequence` | 执行一串按键/文本/鼠标动作 |
| `keyboard_mouse_move` | 移动鼠标 |
| `keyboard_mouse_click` | 点击鼠标（clicks=2 双击） |
| `keyboard_mouse_drag` | 从起点按住拖到终点再松开 |
| `keyboard_mouse_wheel` | 滚动鼠标滚轮 |
| `keyboard_capture` | **截图 + OCR**，返回识别文本（供非视觉模型读屏） |
| `keyboard_find_text` | **按文字搜坐标**，供猫娘"看着屏幕点" |
| `keyboard_run_command` | **执行 shell 命令**，返回输出（供非视觉模型驱动自动化） |

使用流程（猫娘侧）：
1. `keyboard_find_windows(query=...)` 找到目标窗口的 pid
2. `keyboard_set_target(pid=...)` 锁定目标
3. `keyboard_capture(mode="target")` 读取屏幕文本，或 `keyboard_press_keys` / `keyboard_type_text` 操作

### 给非视觉模型读屏

`keyboard_capture` 是核心：截取目标窗口（或全屏）后用 RapidOCR 识别文字，把文本直接返回给模型。非视觉模型因此可以"看到"游戏界面、软件界面里的文字内容（按钮、对话、数值等），再决定按什么键。

```json
// keyboard_capture(mode="target") 返回示例
{
  "text": "开始游戏\n设置\n退出",
  "status": "ok",
  "width": 1280,
  "height": 720,
  "title": "My Game",
  "image_path": "…/data/screenshots/capture_….png"
}
```

- `text` 为识别出的文字；`status` 为 `ok` / `empty` / `unavailable` / `ocr_failed`。
- 截图默认保存在 `data/screenshots/`（可配 `save_screenshots` 关闭）。
- OCR 复用仓库共享的 RapidOCR 运行时（自动回退到 galgame/study_companion 已安装的模型）；未安装时 `status=unavailable`。

### "看着屏幕点"：按文字定位坐标

`keyboard_find_text(query, mode)` 在截图中搜索指定文字并返回其**坐标**（text + 左上/右下 + 置信度），让非视觉模型不仅能"看"还能"点"。中心点坐标 = `((left+right)/2, (top+bottom)/2)`。

```json
// keyboard_find_text(query="开始游戏", mode="target") 返回示例
{
  "status": "ok",
  "query": "开始游戏",
  "matches": [
    { "text": "开始游戏", "left": 100, "top": 200, "right": 300, "bottom": 240, "score": 0.95 }
  ]
}
```

- 只返回匹配的文字块，**返回量极小、省 token**——相比 `keyboard_capture(include_boxes=true)` 回传全部文字块坐标，查询式定位只回几个框。
- 找不到时 `status="no_match"`，可先 `keyboard_capture` 看看实际识别到了什么再调整 query。
- 典型闭环：`keyboard_find_text("开始")` 拿到中心点 → `keyboard_mouse_click(x, y)` 点击。

### 执行 shell 命令

`keyboard_run_command(command, shell="auto")` 让猫娘执行一条 shell 命令并返回输出（含错误输出）。用于非视觉模型驱动简单自动化：查目录、读文件、查 git 状态、装工具等。

```json
// keyboard_run_command(command="dir C:\\", shell="cmd") 返回示例
{
  "success": true,
  "returncode": 0,
  "output": "…目录列表…",
  "timed_out": false
}
```

- `shell` 可选 `cmd` / `powershell`（Windows 默认 `cmd.exe`）。
- 每次调用都是全新进程，无交互式会话；超时（默认 30s）自动终止，输出截断（默认 4000 字符）。
- 命令以宿主进程同等的权限运行，请勿执行未信任来源的命令。

### Vibe-coding：读写工作区文件

配合 `keyboard_run_command`，猫娘可以在**工作区**（配置 `workspace_root`，默认 `C:\Users\<用户名>\Documents`）内读、写、列代码文件，实现"写代码 → 跑命令 → 看报错 → 改"的迭代闭环：

| 工具名 | 作用 |
|---|---|
| `keyboard_list_files(path)` | 列出目录内容（文件名/类型/大小），探索项目结构 |
| `keyboard_read_file(path, start_line, line_count)` | 读取文本文件（自动识别 utf-8/gbk），大文件可分段 |
| `keyboard_write_file(path, content, append)` | 写入/追加文本到文件，不存在则创建 |

```json
// keyboard_write_file(path="hello.py", content="print('hi')") 返回示例
{ "ok": true, "path": "hello.py", "created": true, "bytes_written": 12, "message": "写入完成：hello.py" }
```

- **路径边界**：所有路径必须位于 `workspace_root` 内；`..` 越界、指向工作区外的绝对路径都会被拒绝，防止猫娘读写工作区之外的文件。
- 读取有大小上限（默认 64KB），超大文件请用 `start_line`/`line_count` 分段读。
- 典型流程：`keyboard_list_files` → `keyboard_read_file` → 分析 → `keyboard_write_file` 修改 → `keyboard_run_command` 运行验证。

**执行需确认（默认开启）**：当 `command_require_confirmation = true`（默认）时，`keyboard_run_command` 不会立即执行命令，而是把它加入**待确认队列**并返回 `awaiting_confirmation`（含 `token`）。猫娘会请你到「按键控制」面板确认：

```json
{
  "status": "awaiting_confirmation",
  "token": "3f9a2c1b",
  "command": "pip install requests",
  "message": "命令已加入待确认队列（token=3f9a2c1b）。请告知用户在「按键控制」面板确认后执行。"
}
```

- 面板「命令确认」卡片会列出待确认命令，点「确认执行」后命令才真正运行。
- 执行结果会通过 `push_message` 推回对话（`ai_behavior=respond`），猫娘和用户都能看到输出。
- 确认/拒绝入口（`confirm_command` / `reject_command`）**不注册为 LLM 工具**，猫娘无法自行绕过确认。
- 待确认命令 10 分钟未处理自动过期；队列上限 20 条，满了会拒绝新命令。

### 分析主机播放的音频

`keyboard_analyze_audio(duration=4)` 让猫娘**监听电脑当前正在播放的声音**并返回频谱特征，供非视觉模型"听到"主机在响什么：

```json
// keyboard_analyze_audio(duration=4) 返回示例
{
  "available": true,
  "silence": false,
  "volume_db": -7.7,
  "centroid_hz": 1234.5,
  "dominant_hz": 1000.0,
  "low_pct": 26.5,
  "mid_pct": 73.5,
  "high_pct": 0.0,
  "flatness_db": -22.0,
  "interpretation": "正在播放声音（音量很大，约 -8 dB），以中频为主（可能是对话、人声或器乐）..."
}
```

- 通过 Windows WASAPI **loopback** 捕获系统正在输出的音频（无需麦克风、零第三方依赖，ctypes + numpy FFT）。
- 返回：音量 `volume_db`、频谱质心 `centroid_hz`、低频/中频/高频能量占比（`low_pct`/`mid_pct`/`high_pct`）、能量最集中频率 `dominant_hz`、音调性 `flatness_db`、静音标志 `silence`，以及人类可读的 `interpretation`。
- `duration` 是监听秒数（默认 4，上限 15）。
- 电脑没在放声音时返回 `silence=true`；numpy 缺失或非 Windows 时 `available=false`。

### 自动写日记

插件会把一天内发生的操作（目标窗口切换、按键/鼠标注入、截图 OCR、文字查找、Shell 命令、音频分析、窗口操作）自动整理成 Markdown 日记。默认每小时写盘一次到 `memories/YYYY-MM-DD.md`（路径相对插件数据目录，可用 `diary_dir` 改），命名与 N.E.K.O memory 系统的 daily journal 约定一致，memory 会自动把日记导入并提炼成 facts。

| 工具名 | 作用 |
|---|---|
| `diary_status()` | 日记功能状态与今日统计（今天做了什么） |
| `diary_write_now()` | 立即把今天的事件整理并写入 `memories/` |
| `diary_read(date)` | 读取某天的日记 Markdown（date 留空读今天） |
| `diary_note(detail)` | 往今天的日记追加一条随笔（感想/约定等） |

日记长这样：

```markdown
# 2026-08-10

## 目标窗口
- `14:02` 设置目标窗口：My Game（pid 1234）

## 按键 / 鼠标输入
- `14:05` 按键 ctrl+c ×1 → My Game

## 截图 / OCR
- `14:06` target 截图 1280x720，OCR 42 字符

## 随笔
- `14:20` 今天帮主人把存档备份好了
```

- 跨天自动切换文件；写盘失败/关闭不丢失（重启后继续累积）。
- 每天最多记录 `diary_max_events_per_day` 条（默认 300），超出丢弃并计数。
- 面板「日记」卡片可查看今日统计、立即写盘、读取指定日期。
- 配置 `diary_enabled = false` 可整体关闭（面板可随时开关）。

## 面板使用流程

1. 插件默认自动启动；未启动时在 Plugin Manager 里手动启动。
2. 打开面板 →「查找窗口」，输入标题/进程名关键字（如 `游戏`、`notepad`）搜索。
3. 在结果中点「设为目标」锁定窗口（会持久化，重启不丢失）。
4. 用「截图 + OCR」读取目标窗口文字，或用 `press_keys` / `type_text` / `mouse_click` 操作目标窗口。

## 主要入口

| 入口 | 说明 |
|---|---|
| `find_windows(query)` | 按标题/进程名搜索可见窗口 |
| `set_target(pid)` / `set_target(query)` | 锁定目标窗口 |
| `get_target()` | 查询当前目标 |
| `clear_target()` | 解除目标 |
| `press_keys(keys)` | 单键/组合键，如 `space`、`ctrl+c`、`alt+f4`、`win+d` |
| `hold_key(keys, seconds)` | 长按按键/组合键指定秒数 |
| `type_text(text)` | 向目标窗口输入文本（支持中文；长文本自动改用剪贴板粘贴，更稳更快） |
| `press_sequence(sequence)` | 依次执行一串按键/文本/鼠标动作（action=click/move/drag/wheel） |
| `mouse_move(x, y)` / `mouse_click(x, y, button, clicks)` | 绝对坐标鼠标操作（clicks=2 双击） |
| `mouse_drag(x1, y1, x2, y2, button, steps)` | 从 (x1,y1) 按住拖到 (x2,y2) 再松开（滑动条/拖文件/画线） |
| `mouse_wheel(x, y, delta)` | 在 (x,y) 滚动滚轮（正上负下） |
| `capture_screen(mode, include_boxes)` | 截图 + OCR，返回识别文本（可选带文字块坐标） |
| `find_text(query, mode, max_results)` | 按文字搜屏幕坐标，供点击定位 |
| `save_screenshot(mode)` | 截图保存为 PNG，返回路径 |
| `capture_status()` | 截图/OCR 可用性 |
| `run_command(command, shell)` | 执行 shell 命令（确认模式开启时先入队等待确认） |
| `list_files(path)` | 列出工作区目录内容 |
| `read_file(path, start_line, line_count)` | 读取工作区内文件 |
| `write_file(path, content, append)` | 写入/追加工作区内文件 |
| `list_pending_commands()` | 列出待确认命令 |
| `confirm_command(token)` | 确认并执行一条待确认命令 |
| `reject_command(token)` | 拒绝一条待确认命令 |
| `analyze_audio(duration)` | 监听主机播放声音并返回频谱特征 |
| `audio_status()` | 音频分析可用性 |
| `list_supported_keys()` | 列出所有支持的键名 |
| `diary_status()` | 日记功能状态与今日统计 |
| `diary_write_now()` | 立即把今天的事件写入日记 |
| `diary_read(date)` | 读取某天的日记 |
| `diary_note(detail)` | 往今天的日记追加随笔 |
| `set_diary_enabled(enabled)` | 开关自动写日记 |

## 组合键写法

组合键用 `+` 连接：`ctrl+c`、`alt+f4`、`shift+F5`、`ctrl+shift+esc`。纯单键直接写键名：`space`、`enter`、`f5`。

## 安全说明

- 注入前会把目标窗口切到前台并校验，聚焦失败会取消注入，避免误输入到别的窗口。
- 反作弊进程（anti-cheat、battleye、vanguard 等）一律拒绝注入。
- 目标进程权限高于宿主时拒绝注入。
- 未设置目标时默认拒绝注入（除非配置 `allow_unguided_input = true`，此时对当前前台窗口操作）。

## 配置

在 `plugin.toml` 的 `[keyboard_controller]` 段：

```toml
[keyboard_controller]
default_target_window = ""      # 启动时自动定位的窗口标题/进程关键字
allow_unguided_input = false    # 允许未设置目标时对前台窗口操作
focus_retries = 3               # 聚焦重试次数
input_delay_seconds = 0.05      # 按键间隔
default_type_delay_seconds = 0.01
capture_max_long_edge = 1920    # 截图最长边（缩放）
ocr_result_max_chars = 2000     # OCR 文本返回上限
save_screenshots = true         # 是否把截图保存到 data/screenshots/
command_timeout_seconds = 30    # run_command 超时
command_max_output_chars = 4000 # run_command 输出截断上限
command_default_shell = "auto"  # run_command 默认 shell
command_require_confirmation = true # 执行命令前需用户在面板确认
workspace_root = ""                # 文件读写工具的根目录（留空=默认 C:\Users\<用户名>\Documents）
audio_capture_seconds = 4      # 音频分析默认监听秒数
diary_enabled = true           # 是否自动写日记
diary_dir = "memories"         # 日记目录（相对插件数据目录）
diary_auto_flush_seconds = 3600 # 日记自动写盘间隔（秒）
diary_max_events_per_day = 300 # 每天最多记录的事件数
diary_locale = "zh-CN"         # 日记分组标题语言（zh-CN / en）
```
