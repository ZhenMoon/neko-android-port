# 游戏大脑

教猫娘打游戏的通用框架：AI 先通过 MCP 多引擎搜索（mcp-search-server，随插件
`third_party/` 自带）玩法并提炼出该游戏的接口配置，生成可复用的基础操作库；
之后把用户自然语言目标拆成操作序列，分段执行并截图+OCR 反馈闭环，根据游戏内
反馈自动调整。底层操作复用 keyboard_controller，攻略搜索复用 mcp_adapter +
mcp-search-server。所有学习产物与对局记录保存在本地。

## 依赖

- `keyboard_controller` 插件（键鼠注入 / 截图+OCR / 窗口定位）
- `mcp_adapter` 插件 + 配置 `mcp-search-server`（见 `docs/guide.md`）

## Development

This repository is meant to live at:

```text
N.E.K.O/plugin/plugins/game_brain
```

This is an in-tree plugin: its Python dependencies (`openai`, `httpx`,
`beautifulsoup4`, `markdownify`) are provided by the N.E.K.O host environment
(see the repository `requirements.txt`), so no `pyproject.toml` / `vendor/`
is required for local use.

From the N.E.K.O repository root:

```bash
python -m plugin.neko_plugin_cli.cli check game_brain
```

## Market release

Push a tag matching `plugin.toml` version to create a GitHub Release asset:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The generated `.github/workflows/release.yml` uploads `game_brain.neko-plugin`.
Use that GitHub Release URL when publishing a version in the plugin market.

## Entry

```toml
entry = "plugin.plugins.game_brain:GameBrainPlugin"
```
