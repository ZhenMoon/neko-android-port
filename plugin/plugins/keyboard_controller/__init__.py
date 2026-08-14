"""按键控制插件 (Keyboard Controller)

让猫娘通过键盘/鼠标操作电脑上的游戏或软件：
- find_windows / set_target / get_target / clear_target 定位并锁定目标窗口
- press_keys / type_text / press_sequence 注入按键、组合键与文本
- mouse_move / mouse_click 注入鼠标操作
- capture_screen / save_screenshot / capture_status 截图 + OCR，供非视觉模型读屏

除常规 plugin_entry 外，核心入口同时注册为 @llm_tool，猫娘在对话中可直接
调用（工具名以 ``keyboard_`` 为前缀，注册进 main_server /api/tools）。

安全边界（参考 galgame local input actuator 的策略）：
- 仅对已 set_target 的窗口注入（除非配置 allow_unguided_input）
- 反作弊进程名/标题拒绝注入
- 目标进程提权高于宿主时拒绝注入
- 注入前必须成功聚焦目标窗口，否则 Err
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    timer_interval,
    tr,
    ui,
    unwrap_or,
)

from . import _audio_analysis as audio_analysis
from . import _command_exec as command_exec
from . import _diary as diary
from . import _file_ops as file_ops
from . import _screen_capture as capture
from . import _template_match as template_match
from . import _win32_input as win32
from ._key_map import (
    KeySpecError,
    parse_combo,
    supported_key_names,
)

_STORE_TARGET_KEY = "target"
_STORE_CONFIRM_KEY = "command_require_confirmation"
_STORE_DIARY_KEY = "diary_enabled"
_STORE_DIARY_DAY_KEY = "diary_last_written_day"
_PENDING_MAX = 20
_PENDING_TTL_SECONDS = 600.0


def _is_windows() -> bool:
    return sys.platform == "win32"


@neko_plugin
class KeyboardControllerPlugin(NekoPluginBase):
    """向游戏/软件窗口注入键盘/鼠标输入，并提供截图 OCR 读屏。"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.logger = ctx.logger
        self._target: Optional[dict[str, Any]] = None
        self._cfg: dict[str, Any] = {}
        self._allow_unguided = False
        self._focus_retries = 3
        self._input_delay = 0.05
        self._type_delay = 0.01
        self._save_screenshots = True
        self._diary: Optional[diary.DiaryLog] = None
        self._diary_enabled = True
        self._diary_dir = "memories"
        self._diary_flush_seconds = diary.DEFAULT_AUTO_FLUSH_SECONDS
        self._diary_last_flush = 0.0

    # ── 生命周期 ──────────────────────────────────────────────────────

    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        self._cfg = cfg if isinstance(cfg, dict) else {}
        kb_cfg = self._cfg.get("keyboard_controller", {})
        if not isinstance(kb_cfg, dict):
            kb_cfg = {}

        self._allow_unguided = bool(kb_cfg.get("allow_unguided_input", False))
        self._focus_retries = max(1, int(kb_cfg.get("focus_retries", 3)))
        self._input_delay = float(kb_cfg.get("input_delay_seconds", 0.05))
        self._type_delay = float(kb_cfg.get("default_type_delay_seconds", 0.01))
        self._save_screenshots = bool(kb_cfg.get("save_screenshots", True))
        self._audio_capture_seconds = float(kb_cfg.get("audio_capture_seconds", audio_analysis._CAPTURE_SECONDS_DEFAULT))
        self._command_timeout = float(kb_cfg.get("command_timeout_seconds", command_exec.DEFAULT_TIMEOUT_SECONDS))
        self._command_max_output = int(kb_cfg.get("command_max_output_chars", command_exec.DEFAULT_MAX_OUTPUT_CHARS))
        self._command_default_shell = str(kb_cfg.get("command_default_shell", "auto") or "auto").strip()
        self._command_require_confirmation = bool(kb_cfg.get("command_require_confirmation", True))
        self._pending_commands: list[dict[str, Any]] = []
        self._pending_lock = asyncio.Lock()

        # ── 日记 ─────────────────────────────────────────────────────
        stored_diary_enabled = unwrap_or(await self.store.get(_STORE_DIARY_KEY), None)
        if isinstance(stored_diary_enabled, bool):
            self._diary_enabled = stored_diary_enabled
        else:
            self._diary_enabled = bool(kb_cfg.get("diary_enabled", True))
            await self.store.set(_STORE_DIARY_KEY, self._diary_enabled)
        self._diary_dir = str(kb_cfg.get("diary_dir", "memories") or "memories").strip()
        self._diary_flush_seconds = max(
            30, int(kb_cfg.get("diary_auto_flush_seconds", diary.DEFAULT_AUTO_FLUSH_SECONDS))
        )
        self._diary = diary.DiaryLog(
            enabled=self._diary_enabled,
            max_events_per_day=int(kb_cfg.get("diary_max_events_per_day", diary.DEFAULT_MAX_EVENTS_PER_DAY)),
            locale=str(kb_cfg.get("diary_locale", "zh-CN") or "zh-CN"),
        )
        self._diary_last_flush = time.time()

        self._workspace_root = str(kb_cfg.get("workspace_root", "") or "").strip()
        if not _is_windows():
            self.logger.warning("keyboard_controller only supports Windows; input entries will fail")
            return Ok({"status": "unsupported_platform", "platform": sys.platform})

        stored = unwrap_or(await self.store.get(_STORE_TARGET_KEY), None)
        if isinstance(stored, dict) and int(stored.get("pid") or 0) > 0:
            self._target = dict(stored)

        stored_confirm = unwrap_or(await self.store.get(_STORE_CONFIRM_KEY), None)
        if isinstance(stored_confirm, bool):
            self._command_require_confirmation = stored_confirm
        else:
            default_confirm = bool(kb_cfg.get("command_require_confirmation", True))
            self._command_require_confirmation = default_confirm
            await self.store.set(_STORE_CONFIRM_KEY, default_confirm)

        default_window = str(kb_cfg.get("default_target_window", "") or "").strip()
        if self._target is None and default_window:
            found = await self._auto_find_target(default_window)
            if found is not None:
                self._target = found

        self.logger.info(
            "keyboard_controller started, target={} allow_unguided={}",
            (self._target or {}).get("pid"),
            self._allow_unguided,
        )
        return Ok({"status": "running", "target": self._target})

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        try:
            capture.close_ocr_backend()
        except Exception:
            pass
        try:
            if self._diary is not None and self._diary.enabled():
                root = self._diary_dir_path()
                self._diary.flush_all_pending(root)
        except Exception:
            pass
        return Ok({"status": "shutdown"})

    # ── 内部辅助 ───────────────────────────────────────────────────────

    async def _auto_find_target(self, query: str) -> Optional[dict[str, Any]]:
        windows = await asyncio.to_thread(win32.enumerate_windows)
        needle = query.strip().lower()
        for win in windows:
            title = str(win.get("title") or "").lower()
            proc = str(win.get("process_name") or "").lower()
            if needle and (needle in title or needle in proc):
                return win
        return None

    async def _persist_target(self, target: Optional[dict[str, Any]]) -> None:
        if target is None:
            await self.store.delete(_STORE_TARGET_KEY)
        else:
            await self.store.set(_STORE_TARGET_KEY, target)

    # ── 日记辅助 ─────────────────────────────────────────────────────

    def _diary_dir_path(self) -> Path:
        base = self.data_path()
        return Path(base).joinpath(self._diary_dir)

    def _diary_record(self, kind: str, detail: str, *, ok: bool = True) -> None:
        if self._diary is None:
            return
        try:
            self._diary.record(kind, detail, ok=ok)
        except Exception as exc:
            self.logger.debug("diary record skipped: {}", exc)

    async def _diary_flush_if_due(self, *, force: bool = False) -> bool:
        """若距上次写盘超过间隔（或 force），把所有未写盘日期落到磁盘。"""
        if self._diary is None or not self._diary.enabled():
            return False
        now = time.time()
        if not force and now - self._diary_last_flush < self._diary_flush_seconds:
            return False
        try:
            root = self._diary_dir_path()
            written = await asyncio.to_thread(self._diary.flush_all_pending, root)
            self._diary_last_flush = now
            if written:
                day = datetime.now().strftime("%Y-%m-%d")
                await self.store.set(_STORE_DIARY_DAY_KEY, day)
            return bool(written)
        except Exception as exc:
            self.logger.debug("diary flush failed: {}", exc)
            return False

    def _require_operable_window(self) -> tuple[int, dict[str, Any]]:
        """解析注入目标，返回 (hwnd, window)。未满足安全边界时抛 SdkError。"""
        if not _is_windows():
            raise SdkError("仅支持 Windows 平台")

        if self._target is not None:
            pid = int(self._target.get("pid") or 0)
            if pid <= 0:
                raise SdkError("目标窗口 pid 无效")
            window = win32.find_window_for_pid(pid)
            if window is None:
                raise SdkError(f"找不到 pid={pid} 的可见窗口，目标可能已关闭")
            hwnd = int(window.get("hwnd") or 0)
            if hwnd <= 0:
                raise SdkError("无法解析目标窗口句柄")
        elif self._allow_unguided:
            foreground = self._foreground_window()
            if foreground is None:
                raise SdkError("没有可操作的前台窗口")
            window = foreground
            hwnd = int(window.get("hwnd") or 0)
        else:
            raise SdkError("尚未设置目标窗口，请先调用 set_target")

        block = win32.input_safety_block_reason(
            pid=int(window.get("pid") or 0),
            hwnd=hwnd,
            process_name=str(window.get("process_name") or ""),
            window_title=str(window.get("title") or ""),
        )
        if block:
            raise SdkError(f"安全策略拒绝注入：{block}")

        return hwnd, window

    def _foreground_window(self) -> Optional[dict[str, Any]]:
        if not _is_windows():
            return None
        return win32.foreground_window()

    def _focus_or_raise(self, hwnd: int) -> None:
        focused = win32.focus_window(hwnd, attempts=self._focus_retries, retry_delay=0.25)
        if not focused:
            raise SdkError("无法聚焦目标窗口（前台窗口未切换到目标），为避免误输入已取消注入")

    def _normalize_sequence(self, steps: Any) -> list[dict[str, Any]]:
        if not isinstance(steps, list) or not steps:
            raise SdkError("sequence 必须是包含按键步骤的数组")
        normalized: list[dict[str, Any]] = []
        for step in steps:
            if isinstance(step, str):
                normalized.append({"keys": step})
            elif isinstance(step, dict):
                keys = step.get("keys")
                has_text = step.get("text") is not None
                action = str(step.get("action") or "").strip().lower()
                has_mouse = action in ("click", "move", "drag", "wheel")
                if not keys and not has_text and not has_mouse:
                    raise SdkError("sequence 步骤需要 keys、text 或 action(click/move/drag/wheel) 之一")
                item: dict[str, Any] = {}
                if keys:
                    item["keys"] = keys
                if step.get("count") is not None:
                    item["count"] = int(step["count"])
                if step.get("delay") is not None:
                    item["delay"] = float(step["delay"])
                if has_text:
                    item["text"] = str(step["text"])
                if has_mouse:
                    item["action"] = action
                    if step.get("x") is not None:
                        item["x"] = int(step["x"])
                    if step.get("y") is not None:
                        item["y"] = int(step["y"])
                    if step.get("x2") is not None:
                        item["x2"] = int(step["x2"])
                    if step.get("y2") is not None:
                        item["y2"] = int(step["y2"])
                    if step.get("button") is not None:
                        item["button"] = str(step["button"])
                    if step.get("delta") is not None:
                        item["delta"] = int(step["delta"])
                    if step.get("steps") is not None:
                        item["steps"] = int(step["steps"])
                normalized.append(item)
            else:
                raise SdkError(f"不支持的 sequence 步骤类型: {type(step).__name__}")
        return normalized

    # ── 窗口定位 ───────────────────────────────────────────────────────

    @llm_tool(
        name="keyboard_find_windows",
        description=(
            "按窗口标题或进程名关键字搜索电脑上可见的窗口，返回 pid、标题、进程名。"
            "用于给按键控制定位目标窗口（配合 keyboard_set_target）。query 留空则列出全部。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "窗口标题/进程名关键字，可留空"},
            },
        },
        timeout=15.0,
    )
    @ui.action(
        label=tr("actions.findWindows.label", default="Find windows"),
        icon="F",
        group="target",
        order=10,
        refresh_context=False,
    )
    @plugin_entry(
        id="find_windows",
        name=tr("entries.findWindows.name", default="查找窗口"),
        description="按窗口标题或进程名关键字搜索可见窗口，返回 pid、标题、进程名。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "标题/进程名关键字，留空则列出全部可见窗口",
                },
            },
            "required": ["query"],
        },
    )
    async def find_windows(self, query: str = "", **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows 平台"))
        windows = await asyncio.to_thread(win32.enumerate_windows)
        needle = str(query or "").strip().lower()
        if needle:
            windows = [
                w for w in windows
                if needle in str(w.get("title") or "").lower()
                or needle in str(w.get("process_name") or "").lower()
            ]
        windows = sorted(windows, key=lambda w: (
            (w.get("rect") or {}).get("right", 0) - (w.get("rect") or {}).get("left", 0),
            -(w.get("rect") or {}).get("top", 0),
        ), reverse=True)
        limited = windows[:50]
        return Ok({
            "count": len(limited),
            "total": len(windows),
            "windows": [
                {
                    "pid": int(w.get("pid") or 0),
                    "title": str(w.get("title") or ""),
                    "process_name": str(w.get("process_name") or ""),
                    "hwnd": int(w.get("hwnd") or 0),
                }
                for w in limited
            ],
        })

    @llm_tool(
        name="keyboard_set_target",
        description=(
            "锁定一个目标窗口，之后所有按键/鼠标操作和窗口截图都注入到它。"
            "可用 find_windows 先查到 pid，再传 pid；或直接给窗口标题/进程名关键字自动搜索第一个匹配项。"
            "注入前必须先用本工具设定目标窗口。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "pid": {"type": "integer", "description": "目标进程的 pid（优先）"},
                "query": {"type": "string", "description": "窗口标题/进程名关键字（pid 缺失时用）"},
            },
        },
        timeout=20.0,
    )
    @ui.action(
        label=tr("actions.setTarget.label", default="Set target"),
        icon="T",
        group="target",
        order=20,
        refresh_context=True,
    )
    @plugin_entry(
        id="set_target",
        name=tr("entries.setTarget.name", default="设置目标窗口"),
        description="锁定一个目标窗口，之后所有按键/鼠标操作和窗口截图都注入到它。按 pid 或窗口标题关键字定位。",
        input_schema={
            "type": "object",
            "properties": {
                "pid": {
                    "type": "integer",
                    "description": "目标进程的 pid（优先）",
                },
                "query": {
                    "type": "string",
                    "description": "窗口标题/进程名关键字；pid 未提供时用关键字搜索第一个匹配项",
                },
            },
        },
    )
    async def set_target(self, pid: int | None = None, query: str = "", **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows 平台"))
        if pid and int(pid) > 0:
            window = win32.find_window_for_pid(int(pid))
            if window is None:
                return Err(SdkError(f"找不到 pid={pid} 的可见窗口"))
        else:
            needle = str(query or "").strip()
            if not needle:
                return Err(SdkError("请提供 pid 或窗口关键字"))
            window = await self._auto_find_target(needle)
            if window is None:
                return Err(SdkError(f"找不到匹配 {needle!r} 的窗口"))
        target = {
            "pid": int(window.get("pid") or 0),
            "title": str(window.get("title") or ""),
            "process_name": str(window.get("process_name") or ""),
            "hwnd": int(window.get("hwnd") or 0),
        }
        block = win32.input_safety_block_reason(
            pid=target["pid"],
            hwnd=target["hwnd"],
            process_name=target["process_name"],
            window_title=target["title"],
        )
        if block:
            return Err(SdkError(f"安全策略拒绝设置该目标：{block}"))
        self._target = target
        await self._persist_target(target)
        self._diary_record("target", f"设置目标窗口：{target['title']}（pid {target['pid']}）")
        self.logger.info("target set: pid={} title={}", target["pid"], target["title"])
        return Ok({"target": target, "message": f"目标窗口已设为：{target['title']}（pid {target['pid']}）"})

    @llm_tool(
        name="keyboard_get_target",
        description="返回当前锁定的目标窗口信息（pid、标题、进程名），以及该窗口当前是否在前台。",
        parameters={"type": "object", "properties": {}},
        timeout=10.0,
    )
    @plugin_entry(
        id="get_target",
        name=tr("entries.getTarget.name", default="查询目标窗口"),
        description="返回当前锁定的目标窗口信息（pid、标题、进程名），以及前台窗口是否匹配。",
    )
    async def get_target(self, **_) -> Any:
        if self._target is None:
            return Ok({"target": None, "message": "尚未设置目标窗口"})
        target = dict(self._target)
        focused = False
        if _is_windows():
            window = win32.find_window_for_pid(int(target.get("pid") or 0))
            if window is not None:
                focused = win32.foreground_matches(
                    int(window.get("hwnd") or 0),
                    int(target.get("pid") or 0),
                )
        return Ok({
            "target": {
                "pid": target.get("pid"),
                "title": target.get("title"),
                "process_name": target.get("process_name"),
            },
            "focused": focused,
            "message": f"当前目标：{target.get('title')}（pid {target.get('pid')}）",
        })

    @llm_tool(
        name="keyboard_clear_target",
        description="解除当前锁定的目标窗口。",
        parameters={"type": "object", "properties": {}},
        timeout=10.0,
    )
    @ui.action(
        label=tr("actions.clearTarget.label", default="Clear target"),
        icon="C",
        group="target",
        order=30,
        confirm=tr("actions.clearTarget.confirm", default="清除当前目标窗口？"),
        refresh_context=True,
    )
    @plugin_entry(
        id="clear_target",
        name=tr("entries.clearTarget.name", default="清除目标窗口"),
        description="解除当前锁定的目标窗口。",
    )
    async def clear_target(self, **_) -> Any:
        self._target = None
        await self._persist_target(None)
        self._diary_record("target", "清除目标窗口")
        return Ok({"target": None, "message": "目标窗口已清除"})

    # ── 键盘注入 ───────────────────────────────────────────────────────

    @llm_tool(
        name="keyboard_press_keys",
        description=(
            "向目标窗口注入按键或组合键（须先 keyboard_set_target）。"
            "单键或组合键，组合键用 '+' 连接：'space'、'enter'、'ctrl+c'、'alt+f4'、'win+d'、'shift+F5'。"
            "注入前会自动把目标窗口切到前台；若聚焦失败则不会注入。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "keys": {"type": "string", "description": "按键或组合键，如 'space' / 'ctrl+c' / 'alt+f4'"},
                "count": {"type": "integer", "description": "重复次数（默认 1）"},
            },
            "required": ["keys"],
        },
        timeout=30.0,
    )
    @ui.action(
        label=tr("actions.pressKeys.label", default="Press keys"),
        icon="K",
        group="input",
        order=10,
        refresh_context=False,
    )
    @plugin_entry(
        id="press_keys",
        name=tr("entries.pressKeys.name", default="按按键/组合键"),
        description=(
            "向目标窗口注入按键或组合键。支持单键与组合键，组合键用 '+' 连接，"
            "例如：'space'、'enter'、'ctrl+c'、'alt+f4'、'win+d'、'shift+F5'。"
            "按键前会自动把目标窗口切到前台；若聚焦失败则不会注入。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "keys": {
                    "type": "string",
                    "description": "按键或组合键，如 'space' / 'ctrl+c' / 'alt+f4'",
                },
                "count": {
                    "type": "integer",
                    "description": "重复次数（默认 1）",
                },
            },
            "required": ["keys"],
        },
        llm_result_fields=["pressed", "keys", "message"],
    )
    async def press_keys(self, keys: str, count: int = 1, **_) -> Any:
        hwnd, window = self._require_operable_window()
        try:
            modifiers, main_vk = parse_combo(keys)
        except KeySpecError as exc:
            return Err(SdkError(str(exc)))
        self._focus_or_raise(hwnd)

        def _inject():
            for _ in range(max(1, int(count))):
                win32.press_combo(modifiers, main_vk, delay=self._input_delay)

        await asyncio.to_thread(_inject)
        self._diary_record(
            "input",
            f"按键 {keys} ×{max(1, int(count))} → {window.get('title')}",
        )
        return Ok({
            "pressed": True,
            "keys": str(keys),
            "count": max(1, int(count)),
            "target": str(window.get("title") or ""),
            "message": f"已向 {window.get('title')} 注入 {keys}",
        })

    @llm_tool(
        name="keyboard_hold_key",
        description=(
            "按下并按住一个按键/组合键持续 seconds 秒后松开（须先 keyboard_set_target）。"
            "用于长按操作：游戏中持续加速、按住移动、重复触发等。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "keys": {
                    "type": "string",
                    "description": "按键或组合键，如 'w' / 'ctrl' / 'shift'",
                },
                "seconds": {
                    "type": "number",
                    "default": 1.0,
                    "description": "按住时长（秒）",
                },
            },
            "required": ["keys"],
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="hold_key",
        name=tr("entries.holdKey.name", default="长按按键"),
        description="按下并按住按键/组合键持续指定秒数后松开。",
        input_schema={
            "type": "object",
            "properties": {
                "keys": {"type": "string", "description": "按键或组合键"},
                "seconds": {"type": "number", "default": 1.0, "description": "按住时长（秒）"},
            },
            "required": ["keys"],
        },
        llm_result_fields=["held", "keys", "seconds", "message"],
    )
    async def hold_key(self, keys: str, seconds: float = 1.0, **_) -> Any:
        hwnd, window = self._require_operable_window()
        try:
            parse_combo(keys)
        except KeySpecError as exc:
            return Err(SdkError(str(exc)))
        self._focus_or_raise(hwnd)
        await asyncio.to_thread(win32.hold_key, str(keys), seconds=max(0.05, float(seconds or 1.0)))
        self._diary_record(
            "input",
            f"长按 {keys} {max(0.05, float(seconds or 1.0))} 秒 → {window.get('title')}",
        )
        return Ok({
            "held": True,
            "keys": str(keys),
            "seconds": round(max(0.05, float(seconds or 1.0)), 2),
            "target": str(window.get("title") or ""),
            "message": f"已向 {window.get('title')} 长按 {keys} {seconds} 秒",
        })

    @llm_tool(
        name="keyboard_type_text",
        description=(
            "向目标窗口输入一段文本（Unicode，支持中文）。须先 keyboard_set_target。"
            "短文本用 SendInput 逐字输入；长文本（>=80 字符）自动改用剪贴板粘贴（Ctrl+V），更快更稳。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要输入的文本"},
            },
            "required": ["text"],
        },
        timeout=30.0,
    )
    @ui.action(
        label=tr("actions.typeText.label", default="Type text"),
        icon="W",
        group="input",
        order=20,
        refresh_context=False,
    )
    @plugin_entry(
        id="type_text",
        name=tr("entries.typeText.name", default="输入文本"),
        description="向目标窗口输入一段文本（Unicode，支持中文；长文本自动用剪贴板粘贴）。",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要输入的文本"},
            },
            "required": ["text"],
        },
        llm_result_fields=["typed", "length", "used_clipboard", "message"],
    )
    async def type_text(self, text: str, **_) -> Any:
        hwnd, window = self._require_operable_window()
        payload = str(text or "")
        if not payload:
            return Err(SdkError("文本为空"))
        self._focus_or_raise(hwnd)
        used_clipboard = len(payload) >= 80
        await asyncio.to_thread(
            win32.type_text,
            payload,
            char_delay=self._type_delay,
            use_clipboard=True,
        )
        self._diary_record(
            "input",
            f"输入文本 {len(payload)} 字符{'（剪贴板粘贴）' if used_clipboard else ''} → {window.get('title')}",
        )
        return Ok({
            "typed": True,
            "length": len(payload),
            "used_clipboard": used_clipboard,
            "target": str(window.get("title") or ""),
            "message": f"已向 {window.get('title')} 输入 {len(payload)} 个字符"
                       + ("（长文本，经剪贴板粘贴）" if used_clipboard else ""),
        })

    @llm_tool(
        name="keyboard_press_sequence",
        description=(
            "向目标窗口依次执行一串操作（须先 keyboard_set_target）。"
            "每步是 {'keys': 组合键} + 可选 'count'/'delay'，或直接给字符串；"
            "也可以用 {'text': ...} 输入文本；或 {'action': 'click'|'move'|'drag'|'wheel', ...} 做鼠标动作。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "sequence": {
                    "type": "array",
                    "description": "操作步骤列表，如 [{\"keys\": \"ctrl+c\"}, {\"keys\": \"enter\"}] 或鼠标动作",
                    "items": {
                        "anyOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "keys": {"type": "string"},
                                    "count": {"type": "integer"},
                                    "delay": {"type": "number"},
                                    "text": {"type": "string"},
                                    "action": {"type": "string", "enum": ["click", "move", "drag", "wheel"]},
                                    "x": {"type": "integer"},
                                    "y": {"type": "integer"},
                                    "x2": {"type": "integer"},
                                    "y2": {"type": "integer"},
                                    "button": {"type": "string"},
                                    "delta": {"type": "integer"},
                                    "steps": {"type": "integer"},
                                },
                            },
                        ]
                    },
                },
            },
            "required": ["sequence"],
        },
        timeout=60.0,
    )
    @plugin_entry(
        id="press_sequence",
        name=tr("entries.pressSequence.name", default="按键序列"),
        description=(
            "依次执行一串操作步骤。每步可为 'keys' 组合键（+可选 count/delay）、'text' 输入文本，"
            "或鼠标动作 {\"action\": \"click\"|\"move\"|\"drag\"|\"wheel\", \"x\"..\"y\"..}。"
            "示例：[{\"keys\":\"ctrl+c\"}, {\"text\":\"hi\"}, {\"action\":\"click\",\"x\":100,\"y\":200}]"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "sequence": {
                    "type": "array",
                    "description": "操作步骤列表，如 [{\"keys\": \"ctrl+c\"}, {\"keys\": \"enter\"}] 或鼠标动作",
                    "items": {
                        "anyOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "keys": {"type": "string"},
                                    "count": {"type": "integer"},
                                    "delay": {"type": "number"},
                                    "text": {"type": "string"},
                                    "action": {"type": "string", "enum": ["click", "move", "drag", "wheel"]},
                                    "x": {"type": "integer"},
                                    "y": {"type": "integer"},
                                    "x2": {"type": "integer"},
                                    "y2": {"type": "integer"},
                                    "button": {"type": "string"},
                                    "delta": {"type": "integer"},
                                    "steps": {"type": "integer"},
                                },
                            },
                        ]
                    },
                },
            },
            "required": ["sequence"],
        },
        llm_result_fields=["executed", "steps", "message"],
    )
    async def press_sequence(self, sequence: Any, **_) -> Any:
        hwnd, window = self._require_operable_window()
        try:
            steps = self._normalize_sequence(sequence)
        except SdkError as exc:
            return Err(exc)
        self._focus_or_raise(hwnd)

        parsed: list[dict[str, Any]] = []
        for step in steps:
            keys = step.get("keys")
            item: dict[str, Any] = {
                "count": max(1, int(step.get("count") or 1)),
                "delay": float(step.get("delay") or self._input_delay),
            }
            if step.get("text") is not None:
                item["text"] = str(step["text"])
            elif step.get("action") is not None:
                item["action"] = str(step["action"])
                for field in ("x", "y", "x2", "y2", "delta", "steps"):
                    if step.get(field) is not None:
                        item[field] = int(step[field])
                item["button"] = str(step.get("button") or "left")
            elif keys is not None:
                try:
                    modifiers, main_vk = parse_combo(str(keys))
                except KeySpecError as exc:
                    return Err(SdkError(f"序列第 {len(parsed) + 1} 步无效：{exc}"))
                item["modifiers"] = modifiers
                item["main_vk"] = main_vk
            else:
                return Err(SdkError(f"序列第 {len(parsed) + 1} 步缺少 keys/text/action"))
            parsed.append(item)

        def _run():
            for item in parsed:
                if "text" in item:
                    win32.type_text(item["text"], char_delay=self._type_delay)
                elif "action" in item:
                    action = item["action"]
                    if action == "click":
                        win32.mouse_click(
                            int(item.get("x") or 0), int(item.get("y") or 0),
                            button=item["button"], clicks=item["count"],
                        )
                    elif action == "move":
                        win32.mouse_move(int(item.get("x") or 0), int(item.get("y") or 0))
                    elif action == "drag":
                        win32.mouse_drag(
                            int(item.get("x") or 0), int(item.get("y") or 0),
                            int(item.get("x2") or 0), int(item.get("y2") or 0),
                            button=item["button"], steps=int(item.get("steps") or 20),
                        )
                    elif action == "wheel":
                        win32.mouse_wheel(
                            int(item.get("x") or 0), int(item.get("y") or 0),
                            delta=int(item.get("delta") or 120),
                        )
                else:
                    for _ in range(item["count"]):
                        win32.press_combo(item["modifiers"], item["main_vk"], delay=item["delay"])

        await asyncio.to_thread(_run)
        self._diary_record("input", f"按键序列 {len(parsed)} 步 → {window.get('title')}")
        return Ok({
            "executed": True,
            "steps": len(parsed),
            "target": str(window.get("title") or ""),
            "message": f"已向 {window.get('title')} 执行 {len(parsed)} 步按键序列",
        })

    @plugin_entry(
        id="list_supported_keys",
        name=tr("entries.listKeys.name", default="支持的键名"),
        description="列出 press_keys / press_sequence 支持的所有键名。",
    )
    async def list_supported_keys(self, **_) -> Any:
        names = supported_key_names()
        return Ok({"count": len(names), "keys": names})

    # ── 鼠标注入 ───────────────────────────────────────────────────────

    @llm_tool(
        name="keyboard_mouse_move",
        description="把鼠标移动到屏幕绝对坐标 (x, y)。",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "屏幕 x 坐标"},
                "y": {"type": "integer", "description": "屏幕 y 坐标"},
            },
            "required": ["x", "y"],
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="mouse_move",
        name=tr("entries.mouseMove.name", default="移动鼠标"),
        description="把鼠标移动到屏幕绝对坐标 (x, y)。",
        input_schema={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "屏幕 x 坐标"},
                "y": {"type": "integer", "description": "屏幕 y 坐标"},
            },
            "required": ["x", "y"],
        },
        llm_result_fields=["moved", "x", "y"],
    )
    async def mouse_move(self, x: int, y: int, **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows 平台"))
        await asyncio.to_thread(win32.mouse_move, int(x), int(y))
        return Ok({"moved": True, "x": int(x), "y": int(y), "message": f"鼠标已移动到 ({x}, {y})"})

    @llm_tool(
        name="keyboard_mouse_click",
        description=(
            "在屏幕绝对坐标 (x, y) 处单击（须先 keyboard_set_target，目标窗口会先聚焦）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "屏幕 x 坐标"},
                "y": {"type": "integer", "description": "屏幕 y 坐标"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "鼠标键（默认 left）"},
                "clicks": {"type": "integer", "description": "点击次数（默认 1，2=双击）"},
            },
            "required": ["x", "y"],
        },
        timeout=20.0,
    )
    @ui.action(
        label=tr("actions.mouseClick.label", default="Click"),
        icon="M",
        group="input",
        order=30,
        refresh_context=False,
    )
    @plugin_entry(
        id="mouse_click",
        name=tr("entries.mouseClick.name", default="鼠标点击"),
        description="在屏幕绝对坐标 (x, y) 处点击（默认单击；clicks=2 双击）。",
        input_schema={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "屏幕 x 坐标"},
                "y": {"type": "integer", "description": "屏幕 y 坐标"},
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "default": "left",
                    "description": "鼠标键",
                },
                "clicks": {
                    "type": "integer",
                    "default": 1,
                    "description": "点击次数（1=单击，2=双击）",
                },
            },
            "required": ["x", "y"],
        },
        llm_result_fields=["clicked", "x", "y", "clicks"],
    )
    async def mouse_click(self, x: int, y: int, button: str = "left", clicks: int = 1, **_) -> Any:
        hwnd, window = self._require_operable_window()
        self._focus_or_raise(hwnd)
        clicks = max(1, int(clicks or 1))
        await asyncio.to_thread(
            win32.mouse_click,
            int(x), int(y),
            button=str(button or "left"),
            clicks=clicks,
        )
        self._diary_record(
            "input",
            f"鼠标{str(button or 'left')}键点击 ({x}, {y}) ×{clicks}",
        )
        return Ok({
            "clicked": True,
            "x": int(x),
            "y": int(y),
            "button": str(button or "left"),
            "clicks": clicks,
            "message": f"已在 ({x}, {y}) {'双击' if clicks >= 2 else '点击'}",
        })

    @llm_tool(
        name="keyboard_mouse_drag",
        description=(
            "从屏幕绝对坐标 (x1, y1) 按住鼠标拖到 (x2, y2) 再松开（须先 keyboard_set_target）。"
            "用于拖拽滑动条、移动文件、画线等手势。steps 是插值步数（默认 20）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "x1": {"type": "integer", "description": "起点 x"},
                "y1": {"type": "integer", "description": "起点 y"},
                "x2": {"type": "integer", "description": "终点 x"},
                "y2": {"type": "integer", "description": "终点 y"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left", "description": "鼠标键"},
                "steps": {"type": "integer", "description": "移动插值步数（默认 20）"},
            },
            "required": ["x1", "y1", "x2", "y2"],
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="mouse_drag",
        name=tr("entries.mouseDrag.name", default="鼠标拖拽"),
        description="从 (x1,y1) 按住并拖到 (x2,y2) 再松开。",
        input_schema={
            "type": "object",
            "properties": {
                "x1": {"type": "integer", "description": "起点 x"},
                "y1": {"type": "integer", "description": "起点 y"},
                "x2": {"type": "integer", "description": "终点 x"},
                "y2": {"type": "integer", "description": "终点 y"},
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "default": "left",
                    "description": "鼠标键",
                },
                "steps": {"type": "integer", "default": 20, "description": "插值步数"},
            },
            "required": ["x1", "y1", "x2", "y2"],
        },
        llm_result_fields=["dragged", "from", "to"],
    )
    async def mouse_drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "left", steps: int = 20, **_) -> Any:
        hwnd, window = self._require_operable_window()
        self._focus_or_raise(hwnd)
        await asyncio.to_thread(
            win32.mouse_drag,
            int(x1), int(y1), int(x2), int(y2),
            button=str(button or "left"),
            steps=int(steps or 20),
        )
        self._diary_record(
            "input",
            f"鼠标拖拽 ({x1}, {y1}) → ({x2}, {y2})",
        )
        return Ok({
            "dragged": True,
            "from": [int(x1), int(y1)],
            "to": [int(x2), int(y2)],
            "message": f"已从 ({x1}, {y1}) 拖到 ({x2}, {y2})",
        })

    @llm_tool(
        name="keyboard_mouse_wheel",
        description=(
            "在屏幕坐标 (x, y) 处滚动鼠标滚轮。delta 正数向上滚、负数向下滚，"
            "一格约 120（240/360 更快）。用于滚动页面/列表。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "屏幕 x 坐标"},
                "y": {"type": "integer", "description": "屏幕 y 坐标"},
                "delta": {"type": "integer", "default": 120, "description": "滚动量，正=上，负=下"},
            },
            "required": ["x", "y"],
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="mouse_wheel",
        name=tr("entries.mouseWheel.name", default="鼠标滚轮"),
        description="在 (x, y) 处滚动鼠标滚轮（delta 正上负下）。",
        input_schema={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "屏幕 x 坐标"},
                "y": {"type": "integer", "description": "屏幕 y 坐标"},
                "delta": {"type": "integer", "default": 120, "description": "滚动量，正=上，负=下"},
            },
            "required": ["x", "y"],
        },
        llm_result_fields=["scrolled", "x", "y", "delta"],
    )
    async def mouse_wheel(self, x: int, y: int, delta: int = 120, **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows 平台"))
        await asyncio.to_thread(win32.mouse_wheel, int(x), int(y), delta=int(delta or 120))
        self._diary_record(
            "input",
            f"滚轮滚动 ({x}, {y}) delta={int(delta or 120)}",
        )
        return Ok({
            "scrolled": True,
            "x": int(x),
            "y": int(y),
            "delta": int(delta or 120),
            "message": f"已在 ({x}, {y}) 滚动滚轮",
        })

    @llm_tool(
        name="keyboard_get_window_rect",
        description=(
            "获取目标窗口在屏幕上的位置与大小（left/top/right/bottom/width/height），"
            "以及客户区原点坐标。窗口移动后用它重新定位，配合 keyboard_click_in_window "
            "用窗口内相对坐标操作，不会因窗口位置变化而点错。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=15.0,
    )
    @ui.action(
        label=tr("actions.getWindowRect.label", default="Get window rect"),
        icon="W",
        group="target",
        order=40,
        refresh_context=False,
    )
    @plugin_entry(
        id="get_window_rect",
        name=tr("entries.getWindowRect.name", default="获取窗口坐标"),
        description="获取目标窗口的屏幕坐标与客户区信息。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["ok", "window_rect", "client_rect", "message"],
    )
    async def get_window_rect(self, **_) -> Any:
        hwnd, window = self._require_operable_window()
        wrect = await asyncio.to_thread(win32.window_rect, hwnd)
        crect = await asyncio.to_thread(win32.window_client_rect, hwnd)
        if wrect is None:
            return Err(SdkError("无法获取窗口坐标，目标可能已关闭"))
        self._diary_record(
            "window",
            f"获取窗口坐标 {wrect.get('width', wrect['right']-wrect['left'])}x{wrect.get('height', wrect['bottom']-wrect['top'])} @({wrect['left']}, {wrect['top']})",
        )
        return Ok({
            "ok": True,
            "window_rect": wrect,
            "client_rect": crect,
            "title": str(window.get("title") or ""),
            "message": f"窗口位于 ({wrect['left']}, {wrect['top']})，{wrect.get('width', wrect['right']-wrect['left'])}x{wrect.get('height', wrect['bottom']-wrect['top'])}",
        })

    @llm_tool(
        name="keyboard_click_in_window",
        description=(
            "在目标窗口**内部相对坐标** (x, y) 处点击（0,0 = 窗口客户区左上角），"
            "自动换算为屏幕绝对坐标。窗口移动或拖到别处也不会点错。"
            "先 keyboard_get_window_rect 了解窗口大小再给坐标。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "窗口内 x（相对客户区左上角）"},
                "y": {"type": "integer", "description": "窗口内 y（相对客户区左上角）"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left", "description": "鼠标键"},
                "clicks": {"type": "integer", "default": 1, "description": "点击次数（1=单击，2=双击）"},
            },
            "required": ["x", "y"],
        },
        timeout=20.0,
    )
    @ui.action(
        label=tr("actions.clickInWindow.label", default="Click in window"),
        icon="C",
        group="input",
        order=35,
        refresh_context=False,
    )
    @plugin_entry(
        id="click_in_window",
        name=tr("entries.clickInWindow.name", default="窗口内点击"),
        description="在目标窗口内部相对坐标处点击（自动换算屏幕坐标）。",
        input_schema={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "窗口内 x"},
                "y": {"type": "integer", "description": "窗口内 y"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "clicks": {"type": "integer", "default": 1},
            },
            "required": ["x", "y"],
        },
        llm_result_fields=["clicked", "screen_x", "screen_y", "message"],
    )
    async def click_in_window(self, x: int, y: int, button: str = "left", clicks: int = 1, **_) -> Any:
        hwnd, window = self._require_operable_window()
        self._focus_or_raise(hwnd)
        converted = await asyncio.to_thread(win32.client_to_screen, hwnd, int(x), int(y))
        if converted is None:
            return Err(SdkError("无法换算窗口坐标，目标可能已关闭"))
        sx, sy = converted
        clicks = max(1, int(clicks or 1))
        await asyncio.to_thread(
            win32.mouse_click, sx, sy, button=str(button or "left"), clicks=clicks,
        )
        self._diary_record(
            "input",
            f"窗口内{str(button or 'left')}键点击 ({x}, {y}) ×{clicks}",
        )
        return Ok({
            "clicked": True,
            "screen_x": sx,
            "screen_y": sy,
            "clicks": clicks,
            "message": f"已点击窗口内 ({x}, {y})（屏幕 {sx}, {sy}）",
        })

    @llm_tool(
        name="keyboard_find_image",
        description=(
            "在屏幕上查找与给定图片（模板）匹配的位置，返回中心坐标，用于点击 OCR 认不出的图标/按钮。"
            "template_path 是模板图片的绝对路径或工作区相对路径（PNG/JPG，先在本地找图再调用）。"
            "mode='target' 在目标窗口内找（坐标是窗口内相对坐标，配合 keyboard_click_in_window）；"
            "mode='fullscreen' 在整屏找（坐标是屏幕绝对坐标）。"
            "min_score 是匹配阈值（0~1，默认 0.75，越高越严格）。返回坐标 + 置信度，无匹配返回空列表。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "template_path": {"type": "string", "description": "模板图片路径（绝对或工作区相对）"},
                "mode": {"type": "string", "enum": ["target", "fullscreen"], "default": "target", "description": "查找范围"},
                "min_score": {"type": "number", "default": 0.75, "description": "匹配阈值"},
                "max_results": {"type": "integer", "default": 5, "description": "最多返回几个匹配"},
            },
            "required": ["template_path"],
        },
        timeout=60.0,
    )
    @ui.action(
        label=tr("actions.findImage.label", default="Find image"),
        icon="I",
        group="capture",
        order=30,
        refresh_context=False,
    )
    @plugin_entry(
        id="find_image",
        name=tr("entries.findImage.name", default="查找图片"),
        description="在屏幕/窗口内查找与模板图片匹配的位置，返回中心坐标。",
        input_schema={
            "type": "object",
            "properties": {
                "template_path": {"type": "string", "description": "模板图片路径"},
                "mode": {"type": "string", "enum": ["target", "fullscreen"], "default": "target"},
                "min_score": {"type": "number", "default": 0.75},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["template_path"],
        },
        llm_result_fields=["ok", "matches", "mode", "message"],
    )
    async def find_image(self, template_path: str, mode: str = "target", min_score: float = 0.75, max_results: int = 5, **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows 平台"))
        if not template_match.is_available():
            return Err(SdkError("numpy 不可用，无法进行模板匹配"))
        path = str(template_path or "").strip()
        if not path:
            return Err(SdkError("template_path 不能为空"))
        template_file = os.path.abspath(path)
        if not os.path.isfile(template_file):
            ws_path = self._workspace_path()
            alt = os.path.join(ws_path, path)
            if os.path.isfile(alt):
                template_file = alt
            else:
                return Err(SdkError(f"找不到模板图片：{path}"))
        try:
            from PIL import Image
            template_image = await asyncio.to_thread(Image.open, template_file)
            template_image = await asyncio.to_thread(template_image.convert, "RGB")
        except Exception as exc:
            return Err(SdkError(f"无法加载模板图片：{exc}"))

        mode = str(mode or "target").strip().lower()
        if mode not in ("target", "fullscreen"):
            return Err(SdkError("mode 必须是 'target' 或 'fullscreen'"))
        try:
            if mode == "target":
                if self._target is None:
                    return Err(SdkError("尚未设置目标窗口，请先调用 set_target（或改用 mode='fullscreen'）"))
                pid = int(self._target.get("pid") or 0)
                window = await asyncio.to_thread(capture.target_window_for_capture, pid)
                if window is None:
                    return Err(SdkError(f"找不到 pid={pid} 的可见窗口，目标可能已关闭"))
                frame_image = await asyncio.to_thread(capture.capture_window, window)
            else:
                frame_image = await asyncio.to_thread(capture.capture_fullscreen)
        except Exception as exc:
            return Err(SdkError(f"截图失败：{exc}"))

        matches = await asyncio.to_thread(
            template_match.find_template,
            frame_image, template_image,
            min_score=float(min_score or 0.75),
            top_k=int(max_results or 5),
        )
        return Ok({
            "ok": True,
            "matches": matches,
            "mode": mode,
            "count": len(matches),
            "message": f"找到 {len(matches)} 处匹配"
                       + ("（target 模式的坐标是窗口内相对坐标，可用 keyboard_click_in_window 点击）" if mode == "target" else "（全屏坐标，可直接 mouse_click）"),
        })

    # ── 截图 + OCR（供非视觉模型读屏） ───────────────────────────────

    @llm_tool(
        name="keyboard_capture",
        description=(
            "截图并 OCR 识别文字，让非视觉模型也能读取屏幕内容。"
            "mode='target' 截取已设置的目标窗口（须先 keyboard_set_target）；"
            "mode='fullscreen' 截取整个屏幕。返回识别出的文本和图像信息。"
            "include_boxes=true 时额外返回每个文字块的坐标（会增大返回量）；"
            "更省 token 的取坐标方式是 keyboard_find_text(query=...) 只搜目标文字。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["target", "fullscreen"],
                    "default": "target",
                    "description": "'target' 截目标窗口（需已设 target）；'fullscreen' 截全屏",
                },
                "include_boxes": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否返回每个文字块的坐标（默认 false，省 token）",
                },
            },
        },
        timeout=60.0,
    )
    @ui.action(
        label=tr("actions.capture.label", default="Capture + OCR"),
        icon="S",
        group="capture",
        order=10,
        refresh_context=False,
    )
    @plugin_entry(
        id="capture_screen",
        name=tr("entries.capture.name", default="截图并 OCR"),
        description=(
            "截图并 OCR 识别文字，让非视觉模型也能读屏。mode='target' 截已设置的目标窗口；"
            "mode='fullscreen' 截全屏。返回识别文本与图像信息。include_boxes=true 时附加文字块坐标。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["target", "fullscreen"],
                    "default": "target",
                    "description": "'target' 截目标窗口（需已设 target）；'fullscreen' 截全屏",
                },
                "include_boxes": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否返回每个文字块的坐标",
                },
            },
        },
        llm_result_fields=["text", "status", "boxes", "width", "height", "image_path", "message"],
    )
    async def capture_screen(self, mode: str = "target", include_boxes: bool = False, **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows 平台"))
        mode = str(mode or "target").strip().lower()
        if mode not in ("target", "fullscreen"):
            return Err(SdkError("mode 必须是 'target' 或 'fullscreen'"))

        try:
            if mode == "target":
                if self._target is None:
                    return Err(SdkError("尚未设置目标窗口，请先调用 set_target（或改用 mode='fullscreen'）"))
                pid = int(self._target.get("pid") or 0)
                window = await asyncio.to_thread(capture.target_window_for_capture, pid)
                if window is None:
                    return Err(SdkError(f"找不到 pid={pid} 的可见窗口，目标可能已关闭"))
                image = await asyncio.to_thread(capture.capture_window, window)
                title = str(window.get("title") or "")
            else:
                image = await asyncio.to_thread(capture.capture_fullscreen)
                title = ""
        except Exception as exc:
            return Err(SdkError(f"截图失败：{exc}"))

        width, height = image.size
        boxes: list[dict[str, Any]] = []
        if bool(include_boxes):
            text, boxes, ocr_status = await asyncio.to_thread(capture.ocr_image_with_boxes, image)
        else:
            text, ocr_status = await asyncio.to_thread(capture.ocr_image, image)

        image_path = ""
        if self._save_screenshots:
            try:
                import time

                filename = f"capture_{int(time.time())}.png"
                image_path = str(await asyncio.to_thread(capture.save_png, image, self.data_path("screenshots", filename)))
            except Exception as exc:
                self.logger.debug("capture screenshot save skipped: {}", exc)
                image_path = ""

        message = f"截图完成 {width}x{height}"
        if ocr_status == "ok":
            message += f"，识别到 {len(text)} 个字符"
        elif ocr_status == "empty":
            message += "，未识别到文字"
        else:
            message += f"，OCR 不可用或失败（status={ocr_status}）"

        self._diary_record(
            "capture",
            f"{mode} 截图 {width}x{height}，OCR {len(text)} 字符",
            ok=ocr_status == "ok",
        )

        return Ok({
            "mode": mode,
            "text": text,
            "status": ocr_status,
            "boxes": boxes,
            "width": width,
            "height": height,
            "title": title,
            "image_path": image_path,
            "image_base64": capture.encode_jpeg_base64(image) if ocr_status != "ok" or self._save_screenshots else "",
            "message": message,
        })

    @llm_tool(
        name="keyboard_find_text",
        description=(
            "在屏幕上查找指定文字并返回其坐标（供点击/定位用），让非视觉模型\"看着屏幕点\"。"
            "mode 与 keyboard_capture 相同：'target' 截目标窗口（须先 keyboard_set_target），'fullscreen' 截全屏。"
            "query 是要查找的文字（大小写不敏感的子串匹配）。只返回匹配的文字块（text + 左上/右下坐标 + 置信度），"
            "返回量小、省 token；想取中心点坐标用 (left+right)/2 和 (top+bottom)/2。"
            "找不到返回 status='no_match'，可在返回的文本里确认是否识别有偏差再重试。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要查找的文字（子串匹配）",
                },
                "mode": {
                    "type": "string",
                    "enum": ["target", "fullscreen"],
                    "default": "target",
                    "description": "截取范围",
                },
                "max_results": {
                    "type": "integer",
                    "default": 5,
                    "description": "最多返回几个匹配块（默认 5）",
                },
            },
            "required": ["query"],
        },
        timeout=60.0,
    )
    @plugin_entry(
        id="find_text",
        name=tr("entries.findText.name", default="查找屏幕文字坐标"),
        description="在屏幕上查找指定文字并返回坐标，供点击定位用。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要查找的文字"},
                "mode": {
                    "type": "string",
                    "enum": ["target", "fullscreen"],
                    "default": "target",
                    "description": "截取范围",
                },
                "max_results": {"type": "integer", "default": 5, "description": "最多返回几个匹配块"},
            },
            "required": ["query"],
        },
        llm_result_fields=["status", "matches", "mode", "message"],
    )
    async def find_text(self, query: str, mode: str = "target", max_results: int = 5, **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows 平台"))
        query = str(query or "").strip()
        if not query:
            return Err(SdkError("query 不能为空"))
        mode = str(mode or "target").strip().lower()
        if mode not in ("target", "fullscreen"):
            return Err(SdkError("mode 必须是 'target' 或 'fullscreen'"))

        try:
            if mode == "target":
                if self._target is None:
                    return Err(SdkError("尚未设置目标窗口，请先调用 set_target（或改用 mode='fullscreen'）"))
                pid = int(self._target.get("pid") or 0)
                window = await asyncio.to_thread(capture.target_window_for_capture, pid)
                if window is None:
                    return Err(SdkError(f"找不到 pid={pid} 的可见窗口，目标可能已关闭"))
                image = await asyncio.to_thread(capture.capture_window, window)
            else:
                image = await asyncio.to_thread(capture.capture_fullscreen)
        except Exception as exc:
            return Err(SdkError(f"截图失败：{exc}"))

        text, matches, status = await asyncio.to_thread(
            capture.ocr_image_with_boxes, image, max_boxes=int(max_results or 5), query=query,
        )
        if status == "no_match":
            self._diary_record("text", f"查找「{query}」未找到（{mode}）")
            return Ok({
                "status": "no_match",
                "matches": [],
                "query": query,
                "mode": mode,
                "message": f"屏幕上没有找到包含「{query}」的文字。可以先用 keyboard_capture 看看实际识别到了什么。",
            })
        if status != "ok":
            return Err(SdkError(f"OCR 不可用或失败（status={status}）"))

        self._diary_record(
            "text",
            f"查找「{query}」找到 {len(matches)} 处（{mode}）",
        )
        return Ok({
            "status": "ok",
            "matches": matches,
            "query": query,
            "mode": mode,
            "width": image.size[0],
            "height": image.size[1],
            "message": f"找到 {len(matches)} 处包含「{query}」的文字",
        })

    @ui.action(
        label=tr("actions.saveShot.label", default="Save screenshot"),
        icon="P",
        group="capture",
        order=20,
        refresh_context=False,
    )
    @plugin_entry(
        id="save_screenshot",
        name=tr("entries.saveShot.name", default="保存截图"),
        description="截取目标窗口（或全屏）并保存 PNG 到插件 data 目录，返回文件路径。供视觉模型或人工查看。",
        input_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["target", "fullscreen"],
                    "default": "target",
                    "description": "'target' 截目标窗口（需已设 target）；'fullscreen' 截全屏",
                },
            },
        },
        llm_result_fields=["saved", "path", "width", "height", "message"],
    )
    async def save_screenshot(self, mode: str = "target", **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows 平台"))
        mode = str(mode or "target").strip().lower()
        if mode not in ("target", "fullscreen"):
            return Err(SdkError("mode 必须是 'target' 或 'fullscreen'"))
        try:
            if mode == "target":
                if self._target is None:
                    return Err(SdkError("尚未设置目标窗口，请先调用 set_target（或改用 mode='fullscreen'）"))
                pid = int(self._target.get("pid") or 0)
                window = await asyncio.to_thread(capture.target_window_for_capture, pid)
                if window is None:
                    return Err(SdkError(f"找不到 pid={pid} 的可见窗口"))
                image = await asyncio.to_thread(capture.capture_window, window)
            else:
                image = await asyncio.to_thread(capture.capture_fullscreen)
        except Exception as exc:
            return Err(SdkError(f"截图失败：{exc}"))
        import time

        filename = f"shot_{int(time.time())}.png"
        path = str(await asyncio.to_thread(capture.save_png, image, self.data_path("screenshots", filename)))
        width, height = image.size
        return Ok({
            "saved": True,
            "path": path,
            "width": width,
            "height": height,
            "message": f"截图已保存到 {path}",
        })

    @plugin_entry(
        id="capture_status",
        name=tr("entries.captureStatus.name", default="截图/OCR 状态"),
        description="返回截图与 OCR 可用性（Windows 支持、OCR 是否安装、mss 是否可用）。",
    )
    async def capture_status(self, **_) -> Any:
        status = await asyncio.to_thread(capture.describe_capture)
        return Ok({**status, "save_screenshots": bool(self._save_screenshots)})

    # ── Shell 命令执行（供非视觉模型驱动自动化） ───────────────────

    @llm_tool(
        name="keyboard_run_command",
        description=(
            "在电脑上执行一条 shell 命令并返回输出（含错误输出）。"
            "用于非视觉模型驱动简单自动化：查目录 (dir/ls)、读文件 (type/cat)、"
            "git 状态、安装工具 (pip install) 等。shell 可选 'cmd' 或 'powershell'"
            "（Windows 默认 cmd）。命令有超时保护（默认 30s）且输出会截断。"
            "当确认模式开启时，命令不会立即执行：请告知用户到「按键控制」面板确认后执行。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令",
                },
                "shell": {
                    "type": "string",
                    "enum": ["auto", "cmd", "powershell"],
                    "default": "auto",
                    "description": "使用的 shell（Windows 默认 cmd.exe）",
                },
            },
            "required": ["command"],
        },
        timeout=60.0,
    )
    @plugin_entry(
        id="run_command",
        name=tr("entries.runCommand.name", default="执行命令"),
        description="在电脑上执行一条 shell 命令并返回输出。用于自动化操作（查询/安装/管理文件）。确认模式开启时先入队等待用户确认。",
        input_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令",
                },
                "shell": {
                    "type": "string",
                    "enum": ["auto", "cmd", "powershell"],
                    "default": "auto",
                    "description": "使用的 shell（Windows 默认 cmd.exe）",
                },
            },
            "required": ["command"],
        },
        llm_result_fields=["status", "token", "success", "returncode", "output", "timed_out"],
    )
    async def run_command(self, command: str, shell: str = "auto", **_) -> Any:
        command = str(command or "").strip()
        if not command:
            return Err(SdkError("命令不能为空"))
        shell = str(shell or "auto")

        if self._command_require_confirmation:
            token = uuid.uuid4().hex[:12]
            pending = {
                "token": token,
                "command": command,
                "shell": shell,
                "created_at": time.time(),
                "status": "pending",
                "output": "",
                "returncode": None,
                "timed_out": False,
            }
            async with self._pending_lock:
                self._expire_pending_locked(now=time.time())
                if len(self._pending_commands) >= _PENDING_MAX:
                    return Err(SdkError(f"待确认命令队列已满（>{_PENDING_MAX} 条），请先在面板处理。"))
                self._pending_commands.append(pending)
            return Ok({
                "status": "awaiting_confirmation",
                "token": token,
                "command": command,
                "shell": shell,
                "message": f"命令已加入待确认队列（token={token}）。请告知用户在「按键控制」面板确认后执行。",
            })

        result = await self._execute_command(command, shell)
        self._diary_record(
            "command",
            f"执行命令（{shell}）：{command[:80]}",
            ok=bool(result.get("success")),
        )
        if not result.get("success") and result.get("timed_out"):
            return Err(SdkError(result.get("output", "命令执行超时")))
        return Ok(result)

    def _expire_pending_locked(self, *, now: float) -> None:
        keep: list[dict[str, Any]] = []
        for item in self._pending_commands:
            status = item.get("status")
            if status in ("done", "failed", "rejected"):
                continue
            if status == "running":
                keep.append(item)
                continue
            if now - float(item.get("created_at") or 0) > _PENDING_TTL_SECONDS:
                continue
            keep.append(item)
        self._pending_commands = keep

    async def _execute_command(self, command: str, shell: str) -> dict[str, Any]:
        return await asyncio.to_thread(
            command_exec.run_command,
            command,
            shell=shell,
            timeout=self._command_timeout,
            max_output_chars=self._command_max_output,
        )

    @plugin_entry(
        id="list_pending_commands",
        name=tr("entries.listPending.name", default="待确认命令"),
        description="列出等待用户确认执行的 shell 命令（含 token、命令与状态）。",
    )
    async def list_pending_commands(self, **_) -> Any:
        async with self._pending_lock:
            self._expire_pending_locked(now=time.time())
            items = [
                {
                    "token": p.get("token"),
                    "command": p.get("command"),
                    "shell": p.get("shell"),
                    "status": p.get("status"),
                    "created_at": p.get("created_at"),
                    "output": p.get("output", "") if p.get("status") == "done" else "",
                }
                for p in self._pending_commands
            ]
        return Ok({"items": items, "count": len(items), "require_confirmation": bool(self._command_require_confirmation)})

    @ui.action(
        label=tr("actions.setCommandConfirm.label", default="命令确认开关"),
        icon="K",
        group="command",
        order=5,
        refresh_context=True,
    )
    @plugin_entry(
        id="set_command_confirmation",
        name=tr("entries.setCommandConfirm.name", default="切换命令确认"),
        description="开启/关闭 shell 命令执行前的用户确认（写入 store，重启保持）。",
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "true=开启确认，false=关闭确认",
                },
            },
            "required": ["enabled"],
        },
        llm_result_fields=["enabled", "message"],
    )
    async def set_command_confirmation(self, enabled: bool, **_) -> Any:
        value = bool(enabled)
        self._command_require_confirmation = value
        await self.store.set(_STORE_CONFIRM_KEY, value)
        return Ok({
            "enabled": value,
            "message": f"命令执行确认已{'开启' if value else '关闭'}",
        })

    @ui.action(
        label=tr("actions.confirmCommand.label", default="确认执行"),
        icon="Y",
        group="command",
        order=10,
        refresh_context=True,
    )
    @plugin_entry(
        id="confirm_command",
        name=tr("entries.confirmCommand.name", default="确认执行命令"),
        description="确认并执行一条待确认的 shell 命令（按 token 匹配）。确认后命令才会真正执行。",
        input_schema={
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "待确认命令的 token"},
            },
            "required": ["token"],
        },
        llm_result_fields=["status", "token", "success", "returncode", "output"],
    )
    async def confirm_command(self, token: str, **_) -> Any:
        token = str(token or "").strip()
        async with self._pending_lock:
            self._expire_pending_locked(now=time.time())
            target = next((p for p in self._pending_commands if p.get("token") == token and p.get("status") == "pending"), None)
            if target is None:
                return Err(SdkError(f"没有找到待确认的命令（token={token}），可能已确认、已拒绝或已过期。"))
            if target.get("status") == "running":
                return Err(SdkError(f"命令（token={token}）正在执行中，请稍候。"))
            target["status"] = "running"
            command = str(target.get("command") or "")
            shell = str(target.get("shell") or "auto")
            output = str(target.get("output") or "")
            returncode = target.get("returncode")
            timed_out = bool(target.get("timed_out"))

        result = await self._execute_command(command, shell)
        success = bool(result.get("success"))
        returncode = result.get("returncode")
        output = str(result.get("output") or "")
        timed_out = bool(result.get("timed_out"))
        self._diary_record(
            "command",
            f"执行命令（{shell}）：{command[:80]}",
            ok=success,
        )
        async with self._pending_lock:
            for p in self._pending_commands:
                if p.get("token") == token:
                    p["status"] = "done" if success else "failed"
                    p["returncode"] = returncode
                    p["output"] = output
                    p["timed_out"] = timed_out
                    break

        self._push_command_result(token, command, success, output)
        if timed_out:
            return Err(SdkError(output or "命令执行超时"))
        return Ok({
            "status": "done" if success else "failed",
            "token": token,
            "success": success,
            "returncode": returncode,
            "output": output,
            "message": f"命令已执行（token={token}），退出码 {returncode}",
        })

    @ui.action(
        label=tr("actions.rejectCommand.label", default="拒绝"),
        icon="N",
        group="command",
        order=20,
        refresh_context=True,
    )
    @plugin_entry(
        id="reject_command",
        name=tr("entries.rejectCommand.name", default="拒绝命令"),
        description="拒绝一条待确认的 shell 命令（按 token 匹配），不执行。",
        input_schema={
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "待确认命令的 token"},
            },
            "required": ["token"],
        },
    )
    async def reject_command(self, token: str, **_) -> Any:
        token = str(token or "").strip()
        async with self._pending_lock:
            self._expire_pending_locked(now=time.time())
            target = next((p for p in self._pending_commands if p.get("token") == token), None)
            if target is None:
                return Err(SdkError(f"没有找到待确认的命令（token={token}）。"))
            if target.get("status") == "running":
                return Err(SdkError(f"命令（token={token}）正在执行中，无法拒绝。"))
            target["status"] = "rejected"
        self.logger.info("command rejected: token={}", token)
        return Ok({"status": "rejected", "token": token, "message": f"已拒绝命令（token={token}）。"})

    def _push_command_result(self, token: str, command: str, success: bool, output: str) -> None:
        try:
            self.push_message(
                visibility=["chat"],
                ai_behavior="respond",
                parts=[
                    {
                        "type": "text",
                        "text": (
                            f"命令执行结果（token={token}）:\n> {command}\n\n"
                            f"{'[成功]' if success else '[失败]'}\n"
                            f"```\n{output}\n```"
                        ),
                    }
                ],
            )
        except Exception as exc:
            self.logger.debug("push command result failed: {}", exc)

    # ── 工作区文件读写（供非视觉模型 vibe-coding） ─────────────────

    def _workspace_path(self) -> str:
        root = str(getattr(self, "_workspace_root", "") or "").strip()
        if not root:
            root = os.path.expandvars(r"%USERPROFILE%\Documents")
        return os.path.abspath(os.path.expanduser(root))
    @llm_tool(
        name="keyboard_list_files",
        description=(
            "列出工作区（默认 C:\\Users\\<用户名>\\Documents）下某个目录的内容，"
            "返回文件名、类型（dir/file）与大小。path 相对工作区或为工作区内绝对路径，"
            "留空则列工作区根目录。用于探索代码结构。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要列出的目录路径（相对工作区，可省略）",
                },
            },
        },
        timeout=20.0,
    )
    @plugin_entry(
        id="list_files",
        name=tr("entries.listFiles.name", default="列出目录"),
        description="列出工作区内目录的文件与子目录。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要列出的目录路径（相对工作区，可省略）"},
            },
        },
        llm_result_fields=["ok", "path", "total", "entries", "error"],
    )
    async def list_files(self, path: str = "", **_) -> Any:
        result = await asyncio.to_thread(file_ops.list_dir, self._workspace_path(), path or ".")
        return Ok(result) if result.get("ok") else Err(SdkError(result.get("error", "列出失败")))

    @llm_tool(
        name="keyboard_read_file",
        description=(
            "读取工作区内一个文本文件的内容，返回原文（自动识别 utf-8/gbk）。"
            "大文件可用 start_line/line_count 分段读取（从 start_line 行开始读 line_count 行）。"
            "用于查看代码、配置、报错文件。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径（相对工作区或工作区内绝对路径）",
                },
                "start_line": {
                    "type": "integer",
                    "description": "从第几行开始读（默认 1）",
                },
                "line_count": {
                    "type": "integer",
                    "description": "读取多少行（0=读到文件末尾）",
                },
            },
            "required": ["path"],
        },
        timeout=20.0,
    )
    @plugin_entry(
        id="read_file",
        name=tr("entries.readFile.name", default="读取文件"),
        description="读取工作区内文本文件（支持分段）。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "start_line": {"type": "integer", "description": "起始行（默认 1）"},
                "line_count": {"type": "integer", "description": "读取行数（0=到末尾）"},
            },
            "required": ["path"],
        },
        llm_result_fields=["ok", "size", "content", "truncated_lines", "line_info", "error"],
    )
    async def read_file(self, path: str, start_line: int = 1, line_count: int = 0, **_) -> Any:
        result = await asyncio.to_thread(
            file_ops.read_file,
            self._workspace_path(),
            path,
            start_line=int(start_line or 1),
            line_count=int(line_count or 0),
        )
        return Ok(result) if result.get("ok") else Err(SdkError(result.get("error", "读取失败")))

    @llm_tool(
        name="keyboard_write_file",
        description=(
            "在工作区内写入（或追加）文本到文件，用于修改/创建代码、配置等。"
            "append=true 时在文件末尾追加，否则覆盖。路径相对工作区。"
            "写入后建议用 keyboard_run_command 运行/检查结果，实现 vibe-coding 迭代。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径（相对工作区，不存在则创建）",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的完整文本内容",
                },
                "append": {
                    "type": "boolean",
                    "description": "是否追加到末尾（默认 false=覆盖）",
                },
            },
            "required": ["path", "content"],
        },
        timeout=20.0,
    )
    @plugin_entry(
        id="write_file",
        name=tr("entries.writeFile.name", default="写入文件"),
        description="在工作区内写入/追加文本到文件。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "内容"},
                "append": {"type": "boolean", "description": "是否追加"},
            },
            "required": ["path", "content"],
        },
        llm_result_fields=["ok", "path", "created", "bytes_written", "message", "error"],
    )
    async def write_file(self, path: str, content: str = "", append: bool = False, **_) -> Any:
        result = await asyncio.to_thread(
            file_ops.write_file,
            self._workspace_path(),
            path,
            content or "",
            append=bool(append),
        )
        return Ok(result) if result.get("ok") else Err(SdkError(result.get("error", "写入失败")))

    # ── 主机音频分析（供非视觉模型"听"电脑声音） ──────────────────

    @llm_tool(
        name="keyboard_analyze_audio",
        description=(
            "监听电脑当前正在播放的声音并分析频谱特征，让非视觉模型也能\"听到\"主机在响什么。"
            "返回音量(dB)、频谱质心、低频/中频/高频能量占比、能量最集中的频率、音调性，"
            "以及一段人类可读的解读（静音/音量大小/像人声/像音乐/像提示音等）。"
            "duration 是监听时长（秒），默认 4 秒，上限 15 秒。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "duration": {
                    "type": "number",
                    "description": "监听分析时长（秒），默认 4，上限 15",
                },
            },
        },
        timeout=30.0,
    )
    @ui.action(
        label=tr("actions.analyzeAudio.label", default="Analyze audio"),
        icon="A",
        group="audio",
        order=10,
        refresh_context=False,
    )
    @plugin_entry(
        id="analyze_audio",
        name=tr("entries.analyzeAudio.name", default="分析主机音频"),
        description="监听电脑当前播放的声音并返回频谱特征与解读。",
        input_schema={
            "type": "object",
            "properties": {
                "duration": {
                    "type": "number",
                    "description": "监听时长（秒），默认 4，上限 15",
                },
            },
        },
        llm_result_fields=[
            "available", "silence", "volume_db", "centroid_hz", "dominant_hz",
            "low_pct", "mid_pct", "high_pct", "interpretation", "error",
        ],
    )
    async def analyze_audio(self, duration: float = 0, **_) -> Any:
        seconds = float(duration or self._audio_capture_seconds)
        result = await asyncio.to_thread(audio_analysis.capture_and_analyze, seconds)
        if not result.get("available"):
            return Err(SdkError(result.get("error", "音频分析不可用")))
        self._diary_record(
            "audio",
            "分析主机音频：" + str(result.get("interpretation") or result.get("silence", "有声")),
        )
        return Ok(result)

    @plugin_entry(
        id="audio_status",
        name=tr("entries.audioStatus.name", default="音频分析状态"),
        description="返回主机音频分析可用性（Windows 支持、numpy 是否可用）。",
    )
    async def audio_status(self, **_) -> Any:
        status = await asyncio.to_thread(audio_analysis.describe_audio)
        return Ok({
            **status,
            "capture_seconds_default": float(self._audio_capture_seconds),
            "capture_seconds_max": float(audio_analysis._CAPTURE_SECONDS_MAX),
        })

    # ── 日记 ─────────────────────────────────────────────────────────

    @timer_interval(id="diary_auto_flush", seconds=3600, auto_start=True)
    async def diary_auto_flush(self, **_):
        await self._diary_flush_if_due()
        return Ok({"flushed": True})

    @llm_tool(
        name="diary_status",
        description=(
            "查看日记功能的当前状态：是否启用、今天的日记目录、今天记录了多少条事件、"
            "各类事件计数、是否已写盘。可用于回答\"今天做了什么\"的概览。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=10.0,
    )
    @ui.action(
        label=tr("actions.diaryStatus.label", default="Diary status"),
        icon="D",
        group="diary",
        order=10,
        refresh_context=False,
    )
    @plugin_entry(
        id="diary_status",
        name=tr("entries.diaryStatus.name", default="日记状态"),
        description="查看日记功能状态与今天的记录统计。",
    )
    async def diary_status(self, **_) -> Any:
        if self._diary is None:
            return Ok({"enabled": False, "message": "日记未初始化"})
        day = datetime.now().strftime("%Y-%m-%d")
        counts = self._diary.counts(day)
        return Ok({
            "enabled": self._diary.enabled(),
            "date": day,
            "dir": str(self._diary_dir_path()),
            "event_count": sum(counts.values()),
            "counts": counts,
            "summary": diary.summarize_counts(counts, locale=self._diary.locale()),
            "flushed": bool((self._diary_dir_path() / f"{day}.md").is_file()),
            "auto_flush_seconds": self._diary_flush_seconds,
            "max_events_per_day": self._diary.max_events_per_day(),
        })

    @llm_tool(
        name="diary_write_now",
        description=(
            "立即把今天已记录的操作整理成 Markdown 日记并写入 memories/YYYY-MM-DD.md。"
            "用于在一天结束、或用户要求\"写日记/总结今天\"时主动落盘。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=20.0,
    )
    @ui.action(
        label=tr("actions.diaryWrite.label", default="Write diary now"),
        icon="D",
        group="diary",
        order=20,
        refresh_context=True,
    )
    @plugin_entry(
        id="diary_write_now",
        name=tr("entries.diaryWrite.name", default="立即写日记"),
        description="把今天记录的操作整理成 Markdown 日记写入 memories/。",
    )
    async def diary_write_now(self, **_) -> Any:
        if self._diary is None:
            return Err(SdkError("日记未初始化"))
        day = datetime.now().strftime("%Y-%m-%d")
        written = await self._diary_flush_if_due(force=True)
        if not written:
            return Ok({"date": day, "written": False, "message": "今天还没有可写入的日记事件"})
        root = self._diary_dir_path()
        path = root / f"{day}.md"
        return Ok({
            "date": day,
            "written": True,
            "file": str(path),
            "message": f"日记已写入 {path}",
        })

    @llm_tool(
        name="diary_read",
        description=(
            "读取某一天的日记（Markdown 文本）。date 用 YYYY-MM-DD，留空读今天。"
            "用于回顾\"某天做了什么\"、或把日记内容讲给用户听。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "日期 YYYY-MM-DD，留空读今天",
                },
            },
        },
        timeout=10.0,
    )
    @ui.action(
        label=tr("actions.diaryRead.label", default="Read diary"),
        icon="D",
        group="diary",
        order=30,
        refresh_context=False,
    )
    @plugin_entry(
        id="diary_read",
        name=tr("entries.diaryRead.name", default="读取日记"),
        description="读取某一天的日记 Markdown 文本；date 留空读今天。",
        input_schema={
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "日期 YYYY-MM-DD，留空读今天",
                },
            },
        },
        llm_result_fields=["date", "event_count", "markdown", "message"],
    )
    async def diary_read(self, date: str = "", **_) -> Any:
        if self._diary is None:
            return Err(SdkError("日记未初始化"))
        day = str(date or "").strip() or datetime.now().strftime("%Y-%m-%d")
        data = self._diary.read_day(self._diary_dir_path(), day)
        if not data["markdown"]:
            return Ok({
                "date": day,
                "event_count": 0,
                "markdown": "",
                "message": f"{day} 没有日记记录",
            })
        return Ok({
            "date": day,
            "event_count": data["event_count"],
            "markdown": data["markdown"],
            "message": f"{day} 的日记（{data['event_count']} 条事件）",
        })

    @llm_tool(
        name="diary_note",
        description=(
            "往今天的日记里追加一条随笔/文字记录。detail 是正文。"
            "用于猫娘主动记下值得留存的感想、判断、约定等。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "detail": {"type": "string", "description": "随笔正文"},
            },
            "required": ["detail"],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="diary_note",
        name=tr("entries.diaryNote.name", default="日记随笔"),
        description="往今天的日记追加一条随笔记录。",
        input_schema={
            "type": "object",
            "properties": {
                "detail": {"type": "string", "description": "随笔正文"},
            },
            "required": ["detail"],
        },
        llm_result_fields=["added", "detail", "message"],
    )
    async def diary_note(self, detail: str = "", **_) -> Any:
        text = str(detail or "").strip()
        if not text:
            return Err(SdkError("随笔内容为空"))
        self._diary_record("note", text)
        return Ok({
            "added": True,
            "detail": text,
            "message": "已记入今天的日记",
        })

    @plugin_entry(
        id="set_diary_enabled",
        name=tr("entries.setDiaryEnabled.name", default="开关日记"),
        description="开启/关闭自动写日记（写入 store，重启保持）。",
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": "true=开启，false=关闭"},
            },
            "required": ["enabled"],
        },
        llm_result_fields=["enabled", "message"],
    )
    async def set_diary_enabled(self, enabled: bool, **_) -> Any:
        value = bool(enabled)
        self._diary_enabled = value
        if self._diary is not None:
            self._diary.set_enabled(value)
        await self.store.set(_STORE_DIARY_KEY, value)
        return Ok({
            "enabled": value,
            "message": f"自动写日记已{'开启' if value else '关闭'}",
        })

    # ── Hosted UI ───────────────────────────────────────────────────────

    @ui.context(id="dashboard", title=tr("panel.title", default="按键控制"))
    async def get_dashboard_ui_context(self) -> dict[str, Any]:
        target = None
        focused = False
        target_alive = False
        if self._target is not None:
            target = {
                "pid": self._target.get("pid"),
                "title": self._target.get("title"),
                "process_name": self._target.get("process_name"),
            }
        if _is_windows() and self._target is not None:
            window = await asyncio.to_thread(win32.find_window_for_pid, int(self._target.get("pid") or 0))
            if window is not None:
                target_alive = True
                focused = win32.foreground_matches(
                    int(window.get("hwnd") or 0),
                    int(self._target.get("pid") or 0),
                )
        if self._target is not None and _is_windows() and not target_alive:
            self._target = None
            await self._persist_target(None)
            target = None
            self.logger.info("target window gone, cleared stale target")
        capture_info = {}
        if _is_windows():
            capture_info = await asyncio.to_thread(capture.describe_capture)
        audio_info = {}
        if _is_windows():
            audio_info = await asyncio.to_thread(audio_analysis.describe_audio)
        async with self._pending_lock:
            self._expire_pending_locked(now=time.time())
            pending = [
                {
                    "token": p.get("token"),
                    "command": p.get("command"),
                    "shell": p.get("shell"),
                    "status": p.get("status"),
                    "created_at": p.get("created_at"),
                    "output": p.get("output", "") if p.get("status") == "done" else "",
                }
                for p in self._pending_commands
            ]
        diary_state = {
            "enabled": bool(self._diary_enabled),
            "dir": str(self._diary_dir_path()),
        }
        if self._diary is not None:
            day = datetime.now().strftime("%Y-%m-%d")
            counts = self._diary.counts(day)
            diary_state["date"] = day
            diary_state["event_count"] = sum(counts.values())
            diary_state["counts"] = counts
            diary_state["summary"] = diary.summarize_counts(counts, locale=self._diary.locale())
            diary_state["flushed"] = bool((self._diary_dir_path() / f"{day}.md").is_file())
        return {
            "platform": sys.platform,
            "windows_supported": _is_windows(),
            "target": target,
            "focused": focused,
            "allow_unguided": bool(getattr(self, "_allow_unguided", False)),
            "store_enabled": bool(self.store.enabled),
            "save_screenshots": bool(self._save_screenshots),
            "ocr_available": bool(capture_info.get("ocr_available", False)),
            "mss_available": bool(capture_info.get("mss_available", False)),
            "audio_available": bool(audio_info.get("available", False)),
            "command_require_confirmation": bool(self._command_require_confirmation),
            "pending_commands": pending,
            "diary": diary_state,
            "message": None,
        }
