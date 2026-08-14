"""执行器：把基础操作/操作序列翻译成 keyboard_controller 跨插件调用，并提供
截图 + OCR 反馈（读取游戏内状态）。

依赖（跨插件，需已启用）：
  - keyboard_controller: press_sequence / press_keys / mouse_* / capture_screen
    / find_text / set_target / find_windows / get_window_rect / get_target
  - 反馈闭环读取用 capture_screen（OCR 文本 + image_base64）

操作步骤形态（参数化 JSON，安全；只解释不执行代码）：
  {"action": "press_sequence_item", "step": { ... 传给 keyboard_controller
   press_sequence 的单个 step 字段 ... }}
  {"action": "press_keys", "keys": "w", "count": 1}
  {"action": "hold_key", "keys": "w", "seconds": 1.5}
  {"action": "mouse_click", "x": 100, "y": 200, "button": "left"}
  {"action": "mouse_move", "x": 100, "y": 200}
  {"action": "mouse_drag", "x1":..,"y1":..,"x2":..,"y2":..,"button":"left","steps":20}
  {"action": "mouse_wheel", "x":..,"y":..,"delta":120}
  {"action": "type_text", "text": "..."}
  {"action": "wait", "seconds": 1.0}
  {"action": "find_text", "query": "...", "mode": "target"}   # 反馈用，不执行输入
  {"action": "capture", "mode": "target"}                      # 反馈用，返回 OCR 文本
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# 跨插件调用 keyboard_controller 的函数签名
KbCallable = Callable[[str, dict], Awaitable[Any]]

# 支持的动作（映射到 keyboard_controller 的 entry id）
_SUPPORTED_ACTIONS = {
    "press_keys": {"keys", "count"},
    "hold_key": {"keys", "seconds"},
    "type_text": {"text"},
    "mouse_click": {"x", "y", "button", "clicks"},
    "mouse_move": {"x", "y"},
    "mouse_drag": {"x1", "y1", "x2", "y2", "button", "steps"},
    "mouse_wheel": {"x", "y", "delta"},
    "press_sequence_item": {"step"},
}

# 反馈类动作：不注入输入，只读取信息
_FEEDBACK_ACTIONS = {"find_text", "capture"}


class ExecutorError(Exception):
    pass


class Executor:
    """跨插件执行器。"""

    def __init__(
        self,
        *,
        kb_call: Optional[KbCallable] = None,
        step_delay: float = 0.3,
        feedback_min_interval: float = 2.0,
        window_keywords: str = "",
        allow_unguided: bool = False,
    ):
        self._kb_call = kb_call
        self.step_delay = step_delay
        self.feedback_min_interval = feedback_min_interval
        self.window_keywords = window_keywords
        self.allow_unguided = allow_unguided
        self._last_feedback_at = 0.0

    def set_kb_call(self, kb_call: KbCallable) -> None:
        self._kb_call = kb_call

    def set_window(self, *, keywords: str, allow_unguided: bool) -> None:
        self.window_keywords = keywords
        self.allow_unguided = allow_unguided

    # ------------------------------------------------------------------
    # 目标窗口
    # ------------------------------------------------------------------

    async def ensure_target_window(self) -> dict:
        """确认/设置目标窗口。返回目标信息 dict。

        若已有目标且匹配关键字 → 复用；否则用 find_windows + set_target 设置。
        """
        if not self._kb_call:
            raise ExecutorError("keyboard_controller 调用不可用（插件未启用？）")
        current = await self._kb_call("keyboard_controller:get_target", {})
        target = _ok_value(current) or {}
        title = str(target.get("title") or "")
        keywords = (self.window_keywords or "").strip()
        if title and (not keywords or keywords.lower() in title.lower()):
            return target

        if not keywords:
            if self.allow_unguided:
                return target or {"pid": 0, "title": ""}
            raise ExecutorError("未设置目标窗口，且未配置 window_keywords")

        found = await self._kb_call("keyboard_controller:find_windows", {"query": keywords})
        found_list = _ok_value(found) or {}
        windows = found_list.get("windows") if isinstance(found_list, dict) else None
        if not isinstance(windows, list) or not windows:
            raise ExecutorError(f"找不到匹配「{keywords}」的游戏窗口")
        win = windows[0]
        pid = int(win.get("pid") or 0)
        if not pid:
            raise ExecutorError("找到的窗口没有 pid")
        res = await self._kb_call("keyboard_controller:set_target", {"pid": pid, "query": keywords})
        if not _is_ok(res):
            raise ExecutorError(f"设置目标窗口失败: {_err_text(res)}")
        return {"pid": pid, "title": str(win.get("title") or "")}

    # ------------------------------------------------------------------
    # 步骤执行
    # ------------------------------------------------------------------

    async def execute_sequence(self, steps: list[dict], *, max_steps: int = 100) -> dict:
        """执行一串参数化步骤。

        Returns:
            {
              "ok": bool, "executed": int, "error": str|None,
              "feedback": {...}|None,   # 最后一次反馈
            }
        """
        if not self._kb_call:
            raise ExecutorError("keyboard_controller 调用不可用（插件未启用？）")
        if not isinstance(steps, list):
            raise ExecutorError("步骤必须是列表")
        steps = [s for s in steps if isinstance(s, dict)]
        if len(steps) > max_steps:
            steps = steps[:max_steps]

        executed = 0
        last_feedback: Optional[dict] = None
        for step in steps:
            action = str(step.get("action") or "").strip()
            if not action:
                continue
            if action == "wait":
                seconds = float(step.get("seconds") or 0)
                if seconds > 0:
                    await _asyncio_sleep(seconds)
                executed += 1
                continue
            if action in _SUPPORTED_ACTIONS:
                entry = _action_to_entry(action)
                params = _build_params(action, step)
                res = await self._kb_call(f"keyboard_controller:{entry}", params)
                if not _is_ok(res):
                    return {
                        "ok": False,
                        "executed": executed,
                        "error": f"步骤 {action} 失败: {_err_text(res)}",
                        "feedback": last_feedback,
                    }
                executed += 1
            elif action in _FEEDBACK_ACTIONS:
                fb = await self._read_feedback(action, step)
                last_feedback = fb
            else:
                logger.warning("跳过未知动作: %s", action)
            if self.step_delay > 0 and action not in _FEEDBACK_ACTIONS:
                await _asyncio_sleep(self.step_delay)

        return {"ok": True, "executed": executed, "error": None, "feedback": last_feedback}

    # ------------------------------------------------------------------
    # 反馈读取
    # ------------------------------------------------------------------

    async def read_screen(self, *, mode: str = "target", include_boxes: bool = False) -> dict:
        """截图 + OCR，返回文本与（可选）image_base64。"""
        if not self._kb_call:
            raise ExecutorError("keyboard_controller 调用不可用（插件未启用？）")
        res = await self._kb_call("keyboard_controller:capture_screen", {
            "mode": mode,
            "include_boxes": include_boxes,
        })
        if not _is_ok(res):
            return {"ok": False, "error": _err_text(res)}
        data = _ok_value(res) or {}
        return {
            "ok": True,
            "text": str(data.get("text") or ""),
            "image_base64": str(data.get("image_base64") or ""),
            "status": str(data.get("status") or ""),
            "width": data.get("width"),
            "height": data.get("height"),
        }

    async def _read_feedback(self, action: str, step: dict) -> dict:
        # 反馈节流
        now = time.monotonic()
        if now - self._last_feedback_at < self.feedback_min_interval:
            return {"ok": True, "throttled": True}
        self._last_feedback_at = now

        if action == "capture":
            screen = await self.read_screen(mode=str(step.get("mode") or "target"))
            return {
                "ok": screen.get("ok", False),
                "type": "capture",
                "text": screen.get("text", ""),
                "image_base64": screen.get("image_base64", ""),
                "error": screen.get("error"),
            }
        if action == "find_text":
            res = await self._kb_call("keyboard_controller:find_text", {
                "query": str(step.get("query") or ""),
                "mode": str(step.get("mode") or "target"),
                "max_results": int(step.get("max_results") or 5),
            })
            if not _is_ok(res):
                return {"ok": False, "type": "find_text", "error": _err_text(res)}
            data = _ok_value(res) or {}
            return {"ok": True, "type": "find_text", "matches": data.get("matches") or []}
        return {"ok": False, "type": action, "error": f"未知反馈动作 {action}"}


def _action_to_entry(action: str) -> str:
    if action == "press_sequence_item":
        return "press_sequence"
    return action


def _build_params(action: str, step: dict) -> dict:
    """把步骤字段映射成 keyboard_controller entry 的参数。"""
    if action == "press_sequence_item":
        return {"sequence": [dict(step.get("step") or {})]}
    if action == "press_keys":
        return {"keys": str(step.get("keys") or ""), "count": int(step.get("count") or 1)}
    if action == "hold_key":
        return {"keys": str(step.get("keys") or ""), "seconds": float(step.get("seconds") or 1.0)}
    if action == "type_text":
        return {"text": str(step.get("text") or "")}
    if action == "mouse_click":
        return {
            "x": int(step.get("x") or 0),
            "y": int(step.get("y") or 0),
            "button": str(step.get("button") or "left"),
            "clicks": int(step.get("clicks") or 1),
        }
    if action == "mouse_move":
        return {"x": int(step.get("x") or 0), "y": int(step.get("y") or 0)}
    if action == "mouse_drag":
        return {
            "x1": int(step.get("x1") or 0), "y1": int(step.get("y1") or 0),
            "x2": int(step.get("x2") or 0), "y2": int(step.get("y2") or 0),
            "button": str(step.get("button") or "left"),
            "steps": int(step.get("steps") or 20),
        }
    if action == "mouse_wheel":
        return {
            "x": int(step.get("x") or 0),
            "y": int(step.get("y") or 0),
            "delta": int(step.get("delta") or 120),
        }
    return {}


# ---------------------------------------------------------------------------
# Result 工具
# ---------------------------------------------------------------------------

def _is_ok(result: Any) -> bool:
    if result is None:
        return False
    cls = type(result)
    return cls.__name__ == "Ok" or (
        hasattr(result, "is_ok") and bool(result.is_ok())
    )


def _ok_value(result: Any) -> Any:
    if hasattr(result, "value"):
        return result.value
    if isinstance(result, dict):
        return result.get("result") if "result" in result else result
    return result


def _err_text(result: Any) -> str:
    if hasattr(result, "error"):
        return str(result.error)
    return str(result)


async def _asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
