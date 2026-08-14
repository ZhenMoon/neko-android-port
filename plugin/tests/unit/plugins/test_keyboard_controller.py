from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from plugin.sdk.plugin import Err, Ok

pytestmark = pytest.mark.plugin_unit

PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins" / "keyboard_controller"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_plugin_module_imports() -> None:
    import plugin.plugins.keyboard_controller as module

    assert module.KeyboardControllerPlugin is not None


def test_plugin_entries_are_collected(tmp_path: Path) -> None:
    from plugin.plugins.keyboard_controller import KeyboardControllerPlugin

    plugin = KeyboardControllerPlugin(_make_ctx(tmp_path))
    ids = {meta.id for meta in plugin.collect_entries().values()}
    expected = {
        "find_windows",
        "set_target",
        "get_target",
        "clear_target",
        "press_keys",
        "type_text",
        "press_sequence",
        "list_supported_keys",
        "mouse_move",
        "mouse_click",
    }
    assert expected <= ids, f"missing entries: {expected - ids}"


def test_plugin_get_target_before_set(tmp_path: Path) -> None:
    from plugin.plugins.keyboard_controller import KeyboardControllerPlugin

    plugin = KeyboardControllerPlugin(_make_ctx(tmp_path))
    result = _run(plugin.get_target())
    assert isinstance(result, Ok)
    assert result.value["target"] is None


def test_plugin_press_keys_without_target_errors(tmp_path: Path) -> None:
    from plugin.plugins.keyboard_controller import KeyboardControllerPlugin

    plugin = KeyboardControllerPlugin(_make_ctx(tmp_path))
    with pytest.raises(Exception):
        _run(plugin.press_keys(keys="space"))


def test_plugin_press_keys_invalid_combo_returns_err(tmp_path: Path) -> None:
    from plugin.plugins.keyboard_controller import KeyboardControllerPlugin

    plugin = KeyboardControllerPlugin(_make_ctx(tmp_path))
    result = _run(plugin.press_keys(keys="ctrl+bogus"))
    assert isinstance(result, Err)
    assert "bogus" in str(result.error)


def test_plugin_clear_target_without_target_ok(tmp_path: Path) -> None:
    from plugin.plugins.keyboard_controller import KeyboardControllerPlugin

    plugin = KeyboardControllerPlugin(_make_ctx(tmp_path))
    result = _run(plugin.clear_target())
    assert isinstance(result, Ok)
    assert result.value["target"] is None


class _Logger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


class _Store:
    enabled = True

    def __init__(self):
        self._data: dict[str, object] = {}

    async def get(self, key: str, default=None):
        return Ok(self._data.get(key, default))

    async def set(self, key: str, value):
        self._data[key] = value
        return Ok(None)

    async def delete(self, key: str):
        self._data.pop(key, None)
        return Ok(True)


class _Ctx:
    plugin_id = "keyboard_controller"
    metadata = {}
    bus = None

    def __init__(self, plugin_dir: Path):
        self.config_path = plugin_dir / "plugin.toml"
        self.config_path.write_text(
            "[plugin]\nid='keyboard_controller'\n", encoding="utf-8"
        )
        self.logger = _Logger()
        self._config = {
            "plugin": {"store": {"enabled": True}},
            "keyboard_controller": {},
        }
        self._effective_config = {
            "plugin": {"store": {"enabled": True}, "database": {"enabled": False}},
            "plugin_state": {"backend": "memory"},
        }
        self.store = _Store()

    async def get_own_config(self, timeout=5.0):
        return {"config": self._config}

    async def get_own_base_config(self, timeout=5.0):
        return {"config": self._config}

    async def get_own_profiles_state(self, timeout=5.0):
        return {"profiles": [], "active": None}

    async def get_own_profile_config(self, profile_name, timeout=5.0):
        return {"profile_name": profile_name, "config": self._config}

    async def get_own_effective_config(self, profile_name=None, timeout=5.0):
        return {"config": self._config}

    async def update_own_config(self, updates, timeout=10.0):
        self._config = {**self._config, **dict(updates or {})}
        return {"config": self._config}

    async def query_plugins(self, filters, timeout=5.0):
        return {"plugins": []}

    async def trigger_plugin_event(self, **kwargs):
        return {}

    async def get_system_config(self, timeout=5.0):
        return {}

    async def query_memory(self, bucket_id, query, timeout=5.0):
        return {"items": []}

    async def run_update(self, **kwargs):
        return {"ok": True}

    async def run_update_async(self, **kwargs):
        return {"ok": True}

    async def export_push(self, **kwargs):
        return {"ok": True}

    async def finish(self, **kwargs):
        return {"ok": True}

    def push_message(self, **kwargs):
        return {"ok": True}

    def update_status(self, status):
        return None


def _make_ctx(tmp_path: Path) -> _Ctx:
    return _Ctx(tmp_path)
