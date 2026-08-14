from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_diary_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "_diary.py"
    spec = importlib.util.spec_from_file_location("_diary", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_manifest_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "keyboard_controller"' in text
    assert 'entry = "plugin.plugins.keyboard_controller:KeyboardControllerPlugin"' in text
    assert "diary_enabled = true" in text


def test_diary_records_and_renders() -> None:
    _diary = _load_diary_module()
    log = _diary.DiaryLog(enabled=True, max_events_per_day=10, locale="zh-CN")
    log.record("input", "按键 ctrl+c")
    log.record("capture", "截图 1920x1080")
    day = _diary._day_key()
    markdown = log.render_markdown(day)
    assert "按键 ctrl+c" in markdown
    assert "截图 1920x1080" in markdown
    assert log.counts(day) == {"input": 1, "capture": 1}


def test_diary_flush_and_readback(tmp_path: Path) -> None:
    _diary = _load_diary_module()
    log = _diary.DiaryLog(enabled=True, locale="zh-CN")
    log.record("note", "今天天气不错")
    day = _diary._day_key()
    path = log.flush_day(tmp_path, day)
    assert path is not None
    assert path.name == f"{day}.md"
    assert path.is_file()
    data = log.read_day(tmp_path, day)
    assert data["event_count"] == 1
    assert "今天天气不错" in data["markdown"]


def test_diary_disabled_does_not_record() -> None:
    _diary = _load_diary_module()
    log = _diary.DiaryLog(enabled=False, locale="zh-CN")
    log.record("note", "should be dropped")
    assert log.total_today() == 0


def test_diary_respects_max_events() -> None:
    _diary = _load_diary_module()
    log = _diary.DiaryLog(enabled=True, max_events_per_day=2, locale="zh-CN")
    log.record("note", "1")
    log.record("note", "2")
    log.record("note", "3")
    day = _diary._day_key()
    assert log.counts(day)["note"] == 2
    assert log.dropped(day) == 1


def test_flush_all_pending_writes_cross_day_buckets(tmp_path: Path) -> None:
    _diary = _load_diary_module()
    log = _diary.DiaryLog(enabled=True, locale="zh-CN")
    # 模拟昨晚 23:59 与今天 00:05 的事件落入不同日期桶
    log._events.setdefault("2026-08-09", []).append(
        {"ts": 0, "kind": "input", "detail": "昨晚的按键", "ok": True}
    )
    log._events.setdefault("2026-08-10", []).append(
        {"ts": 0, "kind": "input", "detail": "今天的按键", "ok": True}
    )
    written = log.flush_all_pending(tmp_path)
    names = {p.name for p in written}
    assert "2026-08-09.md" in names
    assert "2026-08-10.md" in names
    # 昨天及更早的桶写盘后清空，避免内存无限增长
    assert log.events("2026-08-09") == []
    assert (tmp_path / "2026-08-09.md").is_file()
    assert "昨晚的按键" in (tmp_path / "2026-08-09.md").read_text(encoding="utf-8")


def test_read_day_counts_events_from_disk(tmp_path: Path) -> None:
    _diary = _load_diary_module()
    log = _diary.DiaryLog(enabled=True, locale="zh-CN")
    log.record("note", "a")
    log.record("input", "b")
    day = _diary._day_key()
    log.flush_day(tmp_path, day)
    log.clear_day(day)
    data = log.read_day(tmp_path, day)
    assert data["event_count"] == 2
    assert data["markdown"] != ""
