"""game_brain 插件冒烟测试。"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from plugin.plugins.game_brain.brain.executor import Executor, ExecutorError
from plugin.plugins.game_brain.brain.models import (
    GameProfile,
    GameStore,
    InputPrimitive,
    ObservationSource,
    Operation,
)


def _make_store() -> GameStore:
    tmp = tempfile.mkdtemp(prefix="game_brain_test_")
    return GameStore(Path(tmp))


def test_profile_roundtrip():
    store = _make_store()
    profile = GameProfile(
        game_id="genshin",
        name="原神",
        window_keywords=["原神", "Genshin"],
        inputs=[InputPrimitive(id="move_forward", name="前进", kind="keyboard", keys="w")],
        observations=[ObservationSource(id="screen", name="整屏", kind="fullscreen_ocr")],
    )
    store.save_profile(profile)
    loaded = store.load_profile("genshin")
    assert loaded is not None
    assert loaded.game_id == "genshin"
    assert loaded.inputs[0].keys == "w"
    assert loaded.observations[0].kind == "fullscreen_ocr"


def test_operations_roundtrip():
    store = _make_store()
    ops = [Operation(id="jump", name="跳跃", steps=[{"action": "press_keys", "keys": "space"}])]
    store.save_operations("genshin", ops)
    loaded = store.load_operations("genshin")
    assert len(loaded) == 1
    assert loaded[0].steps[0]["keys"] == "space"


def test_validate_game_id():
    store = _make_store()
    try:
        store.validate_game_id("bad id!")
        raise AssertionError("should have raised")
    except ValueError:
        pass


def test_executor_action_mapping():
    from plugin.plugins.game_brain.brain import executor as exec_mod

    assert exec_mod._build_params("press_keys", {"keys": "w", "count": 2}) == {"keys": "w", "count": 2}
    assert exec_mod._build_params("mouse_click", {"x": 1, "y": 2})["button"] == "left"
    assert exec_mod._build_params("press_sequence_item", {"step": {"keys": "w"}}) == {
        "sequence": [{"keys": "w"}]
    }


def test_guide_query_build():
    from plugin.plugins.game_brain.brain.guide_search import _build_queries

    queries = _build_queries("原神", extra_keywords=["深渊"], rounds=3)
    assert queries[0].startswith("原神")
    assert any("深渊" in q for q in queries)
    assert len(queries) <= 3


def test_parse_json():
    from plugin.plugins.game_brain.brain.llm import parse_json_object

    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('前缀 {"a": 1} 后缀') == {"a": 1}


def test_mcp_search_json_parsing():
    from plugin.plugins.game_brain.brain.guide_search import _parse_search_json, _extract_mcp_text

    text = (
        "【会话ID】s1\n【引擎状态】baidu(12) bing(5)\n\n"
        "1. 原神攻略站\n   URL: https://www.taptap.cn/x\n   摘要: 攻略\n   来源: baidu\n\n"
        "---\n"
        '[{"title": "原神攻略站", "url": "https://www.taptap.cn/x", '
        '"description": "攻略", "engine": "baidu", "domain": "taptap.cn", '
        '"publishedDate": null, "score": 0.85}]'
    )
    results = _parse_search_json(text)
    assert len(results) == 1
    assert results[0]["url"] == "https://www.taptap.cn/x"
    assert results[0]["snippet"] == "攻略"

    payload = {"result": {"content": [{"type": "text", "text": text}], }, "summary": "x"}
    assert _parse_search_json(_extract_mcp_text(payload))[0]["engine"] == "baidu"


def test_mcp_fetch_body_parsing():
    from plugin.plugins.game_brain.brain.guide_search import _parse_fetch_body

    text = "标题: 原神攻略\nURL: https://x\n字数: 100\n\n这是攻略正文内容"
    assert "正文内容" in _parse_fetch_body(text)


def test_planner_param_substitution():
    from plugin.plugins.game_brain.brain.planner import _expand_operation, _substitute_params

    op = Operation(
        id="click_xy",
        name="点击坐标",
        steps=[{"action": "mouse_click", "x": "{{param.x}}", "y": "{{param.y}}"}],
    )
    steps = _expand_operation(op, {"x": 100, "y": 200})
    assert steps[0]["x"] == "100"
    assert steps[0]["y"] == "200"


async def _test_executor_rejects_unknown_action():
    calls = []

    async def fake_kb(entry, params):
        calls.append((entry, params))
        class Ok:
            value = {"ok": True}
        return Ok()

    executor = Executor(kb_call=fake_kb, step_delay=0)
    result = await executor.execute_sequence([{"action": "nonsense", "x": 1}])
    assert result["ok"] is True
    assert result["executed"] == 0


def test_executor_unknown_action():
    asyncio.run(_test_executor_rejects_unknown_action())


if __name__ == "__main__":
    test_profile_roundtrip()
    test_operations_roundtrip()
    test_validate_game_id()
    test_executor_action_mapping()
    test_guide_query_build()
    test_parse_json()
    test_mcp_search_json_parsing()
    test_mcp_fetch_body_parsing()
    test_planner_param_substitution()
    test_executor_unknown_action()
    print("OK: all smoke tests passed")
