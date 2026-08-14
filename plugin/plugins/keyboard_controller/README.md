# 按键控制（keyboard_controller）

[![Verify](https://github.com/ZhenMoon/n.e.k.o_plugin_keyboard_controller/actions/workflows/verify.yml/badge.svg)](https://github.com/ZhenMoon/n.e.k.o_plugin_keyboard_controller/actions/workflows/verify.yml)

让猫娘通过键盘/鼠标操作电脑上的游戏或软件，并支持**截图 + OCR** 读屏、**shell 命令执行**、**工作区文件读写（vibe-coding）**与**主机音频分析**。

## 功能

- **键鼠注入**：定位并锁定目标窗口后注入按键、组合键、文本、鼠标点击/拖拽/滚轮/长按
- **截图 + OCR 读屏**：非视觉模型也能"看到"屏幕文字；`find_text` 可按文字定位坐标实现"看着屏幕点"
- **shell 命令执行**：驱动自动化（查目录、git、pip 等），可开启用户确认
- **工作区文件读写**：在工作区内读/写/列代码文件，配合命令执行实现 vibe-coding 迭代
- **主机音频分析**：监听电脑播放声音并返回频谱特征，让猫娘"听到"主机在响什么

## 使用

插件入口注册为 LLM 工具（`keyboard_` 前缀），猫娘在对话中可直接调用。完整使用说明见 `docs/guide.md`。

## 开发

本仓库为独立插件仓库，发布到插件市场时仓库名须为 `n.e.k.o_plugin_keyboard_controller`。

从 N.E.K.O 仓库根目录：

```bash
uv run python -m plugin.neko_plugin_cli.cli check keyboard_controller
uv run python -m plugin.neko_plugin_cli.cli check -r keyboard_controller
```

Python 运行时依赖在 `pyproject.toml` 声明并同步进 `vendor/`；`vendor/` 不入库，本地构建与 CI 在发布检查前重建。

## 发布

打一个与 `plugin.toml` 版本一致的 tag 触发 GitHub Release：

```bash
git tag v0.1.0
git push origin v0.1.0
```

`.github/workflows/release.yml` 会生成 `keyboard_controller.neko-plugin` 并上传 Release 资产。

## 入口

```toml
entry = "plugin.plugins.keyboard_controller:KeyboardControllerPlugin"
```
