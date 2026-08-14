"""自动写日记：把插件一天内发生的操作整理成 Markdown 日记。

每天把按键/鼠标注入、截图 OCR、命令执行、音频分析、目标窗口切换等事件
按时间顺序整理成一篇 Markdown 日记，写入 ``memories/YYYY-MM-DD.md``。
该命名与 N.E.K.O memory 系统的 daily journal 约定一致，memory 会自动把
日记导入并提炼 facts。

设计要点：
- 线程安全：事件缓冲用锁保护，任何入口（async/sync、任意线程）都能安全追加。
- 按天分桶：跨天自动切换文件；当天的缓冲保存在内存 + store。
- 惰性写盘：默认由定时器定期 flush；也可由用户/AI 立即 write_now。
- 篇幅控制：每天最多记录 ``max_events_per_day`` 条，超出丢弃并计数。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_MAX_EVENTS_PER_DAY = 300
DEFAULT_AUTO_FLUSH_SECONDS = 3600
MAX_DETAIL_CHARS = 400

# 事件种类 → 日记分组标题（zh 为主，en 作为 fallback 由 i18n 覆盖）
_KIND_LABELS_ZH = {
    "target": "目标窗口",
    "input": "按键 / 鼠标输入",
    "capture": "截图 / OCR",
    "text": "查找文字",
    "command": "Shell 命令",
    "audio": "音频分析",
    "window": "窗口操作",
    "file": "文件操作",
    "note": "随笔",
}

_KIND_LABELS_EN = {
    "target": "Target Window",
    "input": "Keyboard / Mouse",
    "capture": "Screenshot / OCR",
    "text": "Find Text",
    "command": "Shell Command",
    "audio": "Audio",
    "window": "Window",
    "file": "File",
    "note": "Notes",
}

_SECTION_ORDER = [
    "target",
    "input",
    "capture",
    "text",
    "command",
    "audio",
    "window",
    "file",
    "note",
]


def _day_key(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time()).strftime("%Y-%m-%d")


def _fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def kind_label(kind: str, *, locale: str = "zh-CN") -> str:
    table = _KIND_LABELS_EN if locale == "en" else _KIND_LABELS_ZH
    return table.get(kind, kind)


class DiaryLog:
    """线程安全、按天分桶的事件缓冲 + Markdown 渲染。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_events_per_day: int = DEFAULT_MAX_EVENTS_PER_DAY,
        locale: str = "zh-CN",
    ) -> None:
        self._enabled = bool(enabled)
        self._max_events_per_day = max(1, int(max_events_per_day))
        self._locale = str(locale or "zh-CN")
        self._lock = threading.Lock()
        self._events: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._dropped: dict[str, int] = {}

    # ── 配置 ─────────────────────────────────────────────────────────

    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    def set_locale(self, locale: str) -> None:
        self._locale = str(locale or "zh-CN")

    def set_max_events_per_day(self, value: int) -> None:
        self._max_events_per_day = max(1, int(value))

    def locale(self) -> str:
        return self._locale

    def max_events_per_day(self) -> int:
        return self._max_events_per_day

    # ── 追加 ─────────────────────────────────────────────────────────

    def record(self, kind: str, detail: str, *, ok: bool = True) -> None:
        """追加一条今天的事件。失败/异常也记录（ok=False）。"""
        if not self._enabled:
            return
        ts = time.time()
        day = _day_key(ts)
        event = {
            "ts": ts,
            "kind": str(kind or "note"),
            "detail": str(detail or "")[:MAX_DETAIL_CHARS],
            "ok": bool(ok),
        }
        with self._lock:
            bucket = self._events.setdefault(day, [])
            if len(bucket) >= self._max_events_per_day:
                self._dropped[day] = self._dropped.get(day, 0) + 1
                return
            bucket.append(event)

    # ── 读取 ─────────────────────────────────────────────────────────

    def events(self, day: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._events.get(day, [])]

    def counts(self, day: str) -> dict[str, int]:
        result: dict[str, int] = {}
        with self._lock:
            for e in self._events.get(day, []):
                kind = e.get("kind", "note")
                result[kind] = result.get(kind, 0) + 1
        return result

    def dropped(self, day: str) -> int:
        with self._lock:
            return int(self._dropped.get(day, 0))

    def day_keys(self) -> list[str]:
        with self._lock:
            return list(self._events.keys())

    def total_today(self) -> int:
        return len(self.events(_day_key()))

    def clear_day(self, day: str) -> None:
        with self._lock:
            self._events.pop(day, None)
            self._dropped.pop(day, None)

    # ── 渲染 ─────────────────────────────────────────────────────────

    def render_markdown(self, day: str, *, title: str | None = None) -> str:
        """把某天的事件渲染成 Markdown 日记正文。无事件时返回空串。"""
        events = self.events(day)
        if not events:
            return ""
        heading = title or f"# {day}"
        lines: list[str] = []
        if heading:
            lines.append(heading)
            lines.append("")

        grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        for e in events:
            kind = e.get("kind", "note")
            grouped.setdefault(kind, []).append(e)

        for kind in _SECTION_ORDER:
            bucket = grouped.get(kind)
            if not bucket:
                continue
            lines.append(f"## {kind_label(kind, locale=self._locale)}")
            lines.append("")
            for e in bucket:
                ts = float(e.get("ts") or 0)
                detail = str(e.get("detail") or "")
                ok = bool(e.get("ok", True))
                marker = "" if ok else " ⚠️"
                lines.append(f"- `{_fmt_time(ts)}`{marker} {detail}")
            lines.append("")

        dropped = self.dropped(day)
        if dropped > 0:
            lines.append(f"> 已截断 {dropped} 条事件（当天超出上限）。")
            lines.append("")
        return "\n".join(lines)

    # ── 写盘 ─────────────────────────────────────────────────────────

    def flush_day(self, root: Path | str, day: str, *, title: str | None = None) -> Path | None:
        """把某天事件写入 ``root/YYYY-MM-DD.md``。无事件返回 None。

        写盘后不自动清空内存桶，因为插件可能在同一天继续追加事件（后续
        flush 会整体重写同一文件，保证内容完整不重复）。
        """
        if not self._enabled:
            return None
        markdown = self.render_markdown(day, title=title)
        if not markdown:
            return None
        root_path = Path(root)
        try:
            root_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        path = root_path / f"{day}.md"
        try:
            path.write_text(markdown, encoding="utf-8", newline="\n")
        except OSError:
            return None
        return path

    def flush_all_pending(self, root: Path | str) -> list[Path]:
        """把**所有**未写盘的日期都落盘，返回写出的文件列表。

        修复跨天丢事件：定时器可能跨过午夜才触发，此时 ``_day_key()``
        已指向新的一天；若只 flush 当天，昨天 23:00 后记录但尚未写盘的
        事件会一直留在内存，插件重启即丢。此方法遍历全部已分桶日期，
        并清除已成功写盘的旧桶（昨天及更早），避免内存无限增长。
        """
        if not self._enabled:
            return []
        root_path = Path(root)
        written: list[Path] = []
        today = _day_key()
        with self._lock:
            days = list(self._events.keys())
        for day in days:
            path = self.flush_day(root_path, day)
            if path is not None:
                written.append(path)
                if day != today:
                    self.clear_day(day)
        return written

    def read_day(self, root: Path | str, day: str) -> dict[str, Any]:
        """读回某天已落盘的日记；没有则返回事件缓冲摘要。"""
        root_path = Path(root)
        path = root_path / f"{day}.md"
        text = ""
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                text = ""
        events = self.events(day)
        return {
            "date": day,
            "file": str(path),
            "exists": bool(text),
            "markdown": text or self.render_markdown(day),
            "event_count": len(events) if events else self._count_events_in_markdown(text),
            "counts": self.counts(day),
            "dropped": self.dropped(day),
        }

    @staticmethod
    def _count_events_in_markdown(text: str) -> int:
        if not text:
            return 0
        count = 0
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- `") and "`" in stripped[3:]:
                count += 1
        return count


def summarize_counts(counts: dict[str, int], *, locale: str = "zh-CN") -> str:
    """把统计 map 渲染成人类可读的摘要，如「按键/鼠标输入 3 次、截图/OCR 1 次」。"""
    if not counts:
        return ""
    parts = []
    for kind in _SECTION_ORDER:
        n = counts.get(kind)
        if n:
            parts.append(f"{kind_label(kind, locale=locale)} {n}")
    if not parts:
        return ""
    return "、".join(parts)
