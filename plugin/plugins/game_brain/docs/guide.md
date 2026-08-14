# 游戏大脑使用说明

教猫娘打游戏的通用框架。整体分三个阶段：**学习 → 确认 → 游玩**。

## 前置条件

1. **`keyboard_controller` 插件**已启用（负责键鼠注入 / 截图+OCR / 窗口定位）。
2. **`mcp_adapter` 插件**已启用，且配置了 **mcp-search-server**（多引擎攻略搜索，唯一搜索通道）：
   ```toml
   [mcp_servers.search]
   transport = "stdio"
   command = "node"
   args = ["<game_brain 插件目录>/third_party/mcp-search-server/build/index.js"]
   ```
   本插件已自带构建好的 mcp-search-server（`third_party/`）。也支持配置项
   `search_engines` 指定引擎（duckduckgo/bing/sogou/baidu/brave/github/zhihu/360/csdn）。
3. 在**游戏大脑面板 → LLM 设置**里填写文本模型（必填）和视觉模型（推荐，反馈闭环看截图）。
   - 文本模型留空时会尝试回退到主系统 cloud 配置。
   - 视觉模型建议选支持图文的模型（如 `Qwen/Qwen3.5-397B-A17B`）。

> 注意：攻略搜索不再依赖 web_search 插件，完全走 mcp-search-server。

## 第一步：学习一个新游戏

面板 → 「学习一个新游戏」→ 填游戏名（如 `原神`）+ 窗口关键字（如 `Genshin`）→ 开始学习。

流程：
1. AI 联网搜索该游戏的玩法 / 键位 / 攻略（多轮搜索）。
2. LLM 从攻略 + 常识提炼出**游戏接口配置草稿**：
   - `inputs`：可用的输入操作（如 `move_forward` → 按 W）。
   - `observations`：可观测信息源（整窗 OCR、HUD 区域等）。
3. 草稿保存到本地 `data/games/<game_id>/profile.draft.json`。

## 第二步：确认接口配置 + 生成操作库

面板 → 「游戏管理」：
- 草稿状态的游戏点击 **确认接口配置**（如需修改，可先改草稿）。
- 确认后点击 **生成操作库**：LLM 基于接口配置生成 8~20 个可复用命名操作（参数化 JSON 步骤，安全解释执行，不跑任意代码）。

产物保存在 `data/games/<game_id>/`：
- `profile.json`：确认后的接口配置
- `operations.json`：基础操作库

## 第三步：开始游玩

面板 → 「开始游玩」→ 选择已确认游戏 + 输入目标（如 `去打那个 Boss`）→ 开始。

运行逻辑：
1. 确认/定位游戏窗口。
2. LLM 把目标拆成**一段操作序列**（优先引用操作库，也可以直接用按键/鼠标动作）。
3. 执行器逐条调用 `keyboard_controller` 执行（安全阀：单段最多 `segment_max_steps` 步）。
4. 每段执行后**截图 + OCR 反馈**，把画面文本（和视觉模型的图片理解）喂回 LLM。
5. LLM 决定继续 / 调整参数 / 判定完成。直到目标达成、达到 `session_max_steps`、或手动停止。

对局记录保存在 `data/games/<game_id>/sessions/`。

## 安全与注意

- **反馈闭环需要视觉模型**：截图 OCR 只能拿到文字；真正的“看图判断”（画面变化、角色位置）需要视觉模型。未配置视觉模型时，反馈主要靠 OCR 文本，能力受限。
- **坐标是屏幕绝对像素**：LLM 规划里若用 `mouse_click` 坐标，注意分辨率；不确定的动态位置优先用 `find_text` 找文字坐标。
- **反作弊游戏**：`keyboard_controller` 自带反作弊进程拦截，游戏大脑不会绕过。
- **操作需谨慎**：游玩是真实键鼠注入，建议先在安全的环境（单机游戏/沙盒）里测试。

## 配置项（`plugin.toml` 的 `[game_brain]` 段）

| 配置 | 默认 | 说明 |
|---|---|---|
| `llm_url` / `llm_api_key` / `llm_model` | 空 | 文本 LLM（面板可改） |
| `vision_url` / `vision_api_key` / `vision_model` | 空 | 视觉 LLM（面板可改） |
| `search_mcp_server` | search | mcp_adapter 中配置的 mcp-search-server 名 |
| `search_mcp_timeout_ms` | 25000 | 搜索超时（毫秒） |
| `search_engines` | 6 引擎 | 使用的搜索引擎列表 |
| `search_rounds` | 3 | 学习时搜索轮数 |
| `allow_python_operations` | false | 是否允许 LLM 生成受限 Python 操作（默认仅参数化 JSON） |
| `segment_max_steps` | 6 | 每段最大执行步数 |
| `session_max_steps` | 200 | 单局最大总步数（安全阀） |
| `step_delay_seconds` | 0.3 | 单步间隔 |
| `feedback_min_interval_seconds` | 2.0 | 截图反馈最短间隔 |
| `default_window_keywords` | 空 | 默认目标窗口关键字 |
| `allow_unguided` | false | 无目标窗口时是否允许操作 |
