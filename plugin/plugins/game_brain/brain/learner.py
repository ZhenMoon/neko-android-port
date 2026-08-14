"""学习者：AI 搜索玩法 → 生成游戏接口配置草稿 → 生成基础操作库。

流程（v1）：
  1. guide_search.search_game() 多轮搜索攻略（标题+摘要，可选正文）。
  2. LLM 基于攻略 + 通用游戏常识，生成 GameProfile 草稿（inputs + observations）。
  3. 草稿存 draft.json，用户面板确认/修改 → save_profile。
  4. LLM 基于确认后的 profile 生成基础操作库（参数化 JSON 步骤）。
  5. 操作库存 operations.json。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .guide_search import GuideSearch
from .llm import LlmClient
from .models import (
    GameProfile,
    GameStore,
    ObservationSource,
    InputPrimitive,
    Operation,
)

logger = logging.getLogger(__name__)

# 生成 profile 草稿的 system prompt
_PROFILE_SYSTEM = (
    "你是一个游戏玩法分析助手。给定一个游戏和它的网络攻略摘要，"
    "你要拆解出：\n"
    "1) 该游戏的主要『输入操作』（键盘/鼠标），用于后续自动操作。\n"
    "2) 该游戏值得『观测的信息源』（画面里可以读取的状态：血条/对话/菜单/数值等），"
    "用于反馈闭环。\n"
    "只输出 JSON。"
)

_PROFILE_USER_TEMPLATE = """游戏：{game_name}

## 网络攻略摘要
{guide_text}

请生成该游戏的接口配置草稿，JSON 格式如下（不要输出额外说明）：
{{
  "name": "{game_name}",
  "window_keywords": ["窗口标题可能包含的关键字，如 '原神' 'Genshin'"],
  "inputs": [
    {{
      "id": "move_forward",
      "name": "前进",
      "description": "按住前进键",
      "kind": "keyboard",
      "keys": "w"
    }}
  ],
  "observations": [
    {{
      "id": "screen",
      "name": "整屏画面",
      "description": "整窗截图+OCR，用于读取绝大多数游戏内状态",
      "kind": "fullscreen_ocr"
    }}
  ],
  "notes": "对这个游戏自动操作的关键注意事项"
}}

约束：
- inputs 覆盖核心操作即可（5~15 个），id 用小写蛇形。
- keys 使用 keyboard_controller 支持的键名（如 w/a/s/d/space/shift/ctrl/1~9/回车 等，组合键用 + 连接）。
- observations 至少包含一个 fullscreen_ocr；有明确 HUD 区域时可加 region_ocr。
- 你无法真正读取游戏画面，未知的信息源宁可少写。"""

# 生成操作库的 system prompt
_OPS_SYSTEM = (
    "你是一个游戏自动操作编排助手。给定一个游戏的接口配置，你要为这个游戏生成"
    "一份『基础操作库』——可复用的命名操作。每个操作是一串参数化 JSON 步骤，"
    "步骤在运行时由安全执行器解释（不执行任意代码）。只输出 JSON。"
)

_OPS_USER_TEMPLATE = """游戏：{game_name}

## 游戏接口配置
{profile_json}

## 可选：攻略参考（用于补充操作细节）
{guide_text}

请生成基础操作库，JSON 格式如下（不要输出额外说明）：
{{
  "operations": [
    {{
      "id": "move_forward",
      "name": "前进",
      "description": "按住前进键一段时间",
      "steps": [
        {{"action": "hold_key", "keys": "w", "seconds": 1.0}}
      ]
    }}
  ]
}}

步骤动作支持以下集合（与 keyboard_controller 对应）：
- {{"action": "press_keys", "keys": "w", "count": 1}}
- {{"action": "hold_key", "keys": "w", "seconds": 1.5}}
- {{"action": "type_text", "text": "..."}}
- {{"action": "mouse_click", "x": <屏幕绝对x>, "y": <屏幕绝对y>, "button": "left", "clicks": 1}}
- {{"action": "mouse_move", "x": ..., "y": ...}}
- {{"action": "mouse_drag", "x1":.., "y1":.., "x2":.., "y2":.., "button": "left", "steps": 20}}
- {{"action": "mouse_wheel", "x":..., "y":..., "delta": 120}}
- {{"action": "wait", "seconds": 1.0}}
- {{"action": "capture", "mode": "target"}}
- {{"action": "find_text", "query": "文字", "mode": "target"}}

约束：
- 生成 8~20 个常用操作，覆盖移动/交互/菜单/战斗等场景。
- 鼠标坐标若是未知/动态位置，优先用 capture+find_text 找坐标再点击，或写成参数
  （如 {{"action": "mouse_click", "x": "{{param.x}}", "y": "{{param.y}}", ...}}，
  参数会从调用方注入）。
- steps 里含 {{param.name}} 的操作视为带参数操作，在 params 里声明。
- id 用小写蛇形，每个操作有清晰 name/description。"""


class Learner:
    """AI 学习一个游戏：搜索 → 生成 profile 草稿 → 生成操作库。"""

    def __init__(
        self,
        *,
        store: GameStore,
        llm: LlmClient,
        guide: GuideSearch,
        allow_python: bool = False,
    ):
        self.store = store
        self.llm = llm
        self.guide = guide
        self.allow_python = allow_python

    # ------------------------------------------------------------------
    # 学习一个游戏（搜索 + 生成草稿）
    # ------------------------------------------------------------------

    async def learn_game(
        self,
        game_name: str,
        *,
        extra_keywords: Optional[list[str]] = None,
        with_rounds: Optional[int] = None,
    ) -> dict:
        """搜索攻略并生成 profile 草稿。

        Returns:
            {"game_id": ..., "draft": {...}, "guide": {...}, "available": bool}
        """
        game_id = self.store.validate_game_id(game_name.lower().replace(" ", "_"))
        guide = await self.guide.search_game(
            game_name,
            extra_keywords=extra_keywords,
            with_rounds=with_rounds,
        )
        guide_text = _format_guide_for_llm(guide)

        user = _PROFILE_USER_TEMPLATE.format(game_name=game_name, guide_text=guide_text)
        raw = await self.llm.complete_json(
            [{"role": "system", "content": _PROFILE_SYSTEM}, {"role": "user", "content": user}],
            max_tokens=4096,
        )

        draft = _normalize_profile_draft(game_id, game_name, raw)
        self.store.save_draft(game_id, draft)
        return {"game_id": game_id, "draft": draft, "guide": guide}

    async def confirm_draft(self, game_id: str, draft: Optional[dict] = None) -> GameProfile:
        """把草稿（或用户修改过的 dict）确认为正式 profile。"""
        game_id = self.store.validate_game_id(game_id)
        if draft is None:
            draft = self.store.load_draft(game_id)
        if not draft:
            raise ValueError(f"没有 {game_id} 的草稿，请先 learn_game")
        profile = _draft_to_profile(game_id, draft)
        self.store.save_profile(profile)
        return profile

    # ------------------------------------------------------------------
    # 生成操作库
    # ------------------------------------------------------------------

    async def generate_operations(
        self,
        game_id: str,
        *,
        extra_keywords: Optional[list[str]] = None,
    ) -> list[Operation]:
        """基于已确认的 profile 生成基础操作库。"""
        game_id = self.store.validate_game_id(game_id)
        profile = self.store.load_profile(game_id)
        if profile is None:
            raise ValueError(f"游戏 {game_id} 还没有确认的 profile，请先确认草稿")
        guide = await self.guide.search_game(
            profile.name,
            extra_keywords=extra_keywords,
            with_rounds=1,
        )
        guide_text = _format_guide_for_llm(guide, limit_chars=4000)
        profile_json = _json(profile.to_dict())
        user = _OPS_USER_TEMPLATE.format(
            game_name=profile.name,
            profile_json=profile_json,
            guide_text=guide_text,
        )
        raw = await self.llm.complete_json(
            [{"role": "system", "content": _OPS_SYSTEM}, {"role": "user", "content": user}],
            max_tokens=8192,
        )
        ops_raw = raw.get("operations") if isinstance(raw, dict) else None
        if not isinstance(ops_raw, list):
            raise ValueError("LLM 没有返回 operations 数组")
        ops = []
        for item in ops_raw:
            if not isinstance(item, dict):
                continue
            op_id = str(item.get("id") or "").strip()
            if not op_id:
                continue
            ops.append(Operation(
                id=op_id,
                name=str(item.get("name") or op_id),
                description=str(item.get("description") or ""),
                steps=list(item.get("steps") or []),
                python=str(item.get("python") or "") if self.allow_python else "",
            ))
        self.store.save_operations(game_id, ops)
        return ops

    # ------------------------------------------------------------------

    async def learn_full(
        self,
        game_name: str,
        *,
        extra_keywords: Optional[list[str]] = None,
        with_rounds: Optional[int] = None,
    ) -> dict:
        """一步到位：搜索 → 生成草稿。返回后等待用户确认。"""
        result = await self.learn_game(
            game_name, extra_keywords=extra_keywords, with_rounds=with_rounds
        )
        return result


def _format_guide_for_llm(guide: dict, *, limit_chars: int = 6000) -> str:
    results = guide.get("results") or []
    if not results:
        return "(未搜索到攻略)"
    lines = []
    for i, r in enumerate(results[:10], 1):
        lines.append(f"{i}. {r.get('title', '')} | {r.get('url', '')}\n   {r.get('snippet', '')}")
    text = "\n".join(lines)
    for body in (guide.get("body_snippets") or [])[:3]:
        text += f"\n\n[正文] {body.get('url', '')}\n{body.get('text', '')}"
    return text[:limit_chars]


def _normalize_profile_draft(game_id: str, game_name: str, raw: dict) -> dict:
    """清洗 LLM 输出的 profile 草稿。"""
    name = str(raw.get("name") or game_name)
    window_keywords = [str(k).strip() for k in (raw.get("window_keywords") or []) if str(k).strip()]
    if not window_keywords:
        window_keywords = [game_name]

    inputs = []
    for i in raw.get("inputs") or []:
        if not isinstance(i, dict):
            continue
        pid = str(i.get("id") or "").strip()
        if not pid:
            continue
        inputs.append(InputPrimitive(
            id=pid,
            name=str(i.get("name") or pid),
            description=str(i.get("description") or ""),
            kind=str(i.get("kind") or "keyboard"),
            keys=str(i.get("keys") or ""),
            button=str(i.get("button") or "left"),
            action=str(i.get("action") or "click"),
            text=str(i.get("text") or ""),
            params=dict(i.get("params") or {}),
        ).to_dict())

    observations = []
    for o in raw.get("observations") or []:
        if not isinstance(o, dict):
            continue
        oid = str(o.get("id") or "").strip()
        if not oid:
            continue
        observations.append(ObservationSource(
            id=oid,
            name=str(o.get("name") or oid),
            description=str(o.get("description") or ""),
            kind=str(o.get("kind") or "fullscreen_ocr"),
            region=o.get("region") or None,
            params=dict(o.get("params") or {}),
        ).to_dict())

    return {
        "game_id": game_id,
        "name": name,
        "window_keywords": window_keywords,
        "inputs": inputs,
        "observations": observations,
        "notes": str(raw.get("notes") or ""),
        "source": "ai",
    }


def _draft_to_profile(game_id: str, draft: dict) -> GameProfile:
    draft["game_id"] = game_id
    return GameProfile.from_dict(draft)


def _json(data: Any) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, indent=2)
