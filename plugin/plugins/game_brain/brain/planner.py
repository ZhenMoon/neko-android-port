"""规划器：把用户自然语言目标拆成操作序列，分段执行并反馈闭环。

流程（v1）：
  1. 收集游戏 profile + 操作库 + 最近的画面反馈（截图 OCR）。
  2. LLM 把目标规划成一段操作序列（steps 使用 executor 支持的动作，或引用
     操作库中的命名操作）。
  3. 执行器执行这一段；执行后强制截图反馈一次。
  4. 反馈喂回 LLM → 决定继续 / 调整 / 停止。循环直到目标达成、超步数或用户停止。

会话状态保存在内存 + 会话记录落盘（GameStore.save_session）。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .executor import Executor, ExecutorError
from .guide_search import GuideSearch
from .llm import LlmClient
from .models import GameProfile, GameStore, Operation

logger = logging.getLogger(__name__)

_PLAN_SYSTEM = (
    "你是一个游戏自动操作编排助手。用户会给一个自然语言目标，以及游戏的接口配置"
    "和基础操作库。你要规划出『一段可执行的操作序列』。"
    "动作只允许 executor 支持的动作集合或操作库中的命名操作引用。只输出 JSON。"
)

_PLAN_USER_TEMPLATE = """游戏：{game_name}

## 接口配置
{profile_json}

## 基础操作库
{operations_json}

## 当前画面（最近一次截图 OCR，可能是空的）
{recent_ocr}

## 之前的操作记录（最近几步，用于避免重复）
{history}

## 目标
{goal}

请规划下一步要执行的**一段**操作序列（这一步执行完会截图反馈，然后你再看画面决定下一步）。
JSON 格式如下（不要输出额外说明）：
{{
  "plan": [
    {{"action": "operation", "operation_id": "move_forward", "params": {{}}}},
    {{"action": "hold_key", "keys": "w", "seconds": 1.0}},
    {{"action": "capture", "mode": "target"}}
  ],
  "reasoning": "为什么这样做",
  "done": false
}}

动作支持：
1) 操作库引用：{{"action": "operation", "operation_id": "<操作库id>", "params": {<操作参数>}}}
2) 直接动作（同 executor）：
   - {{"action": "press_keys", "keys": "w", "count": 1}}
   - {{"action": "hold_key", "keys": "w", "seconds": 1.5}}
   - {{"action": "type_text", "text": "..."}}
   - {{"action": "mouse_click", "x": 100, "y": 200, "button": "left", "clicks": 1}}
   - {{"action": "mouse_move", "x": 100, "y": 200}}
   - {{"action": "mouse_drag", "x1":.., "y1":.., "x2":.., "y2":.., "button": "left", "steps": 20}}
   - {{"action": "mouse_wheel", "x":..., "y":..., "delta": 120}}
   - {{"action": "wait", "seconds": 1.0}}
   - {{"action": "capture", "mode": "target"}}（截图+OCR，放这段最后用来收集反馈）
   - {{"action": "find_text", "query": "...", "mode": "target"}}

约束：
- 每段 3~6 步为宜，不要试图一步完成整个目标。
- 使用坐标时考虑分辨率；优先用操作库操作。
- 目标已达成时 done=true 且 plan 为空数组或只有收尾动作。
- 不要把判断当动作；capture 放在段尾让反馈收集最新画面。"""


@dataclass
class PlaySession:
    """一次对局会话（内存态 + 落盘）。"""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    game_id: str = ""
    game_name: str = ""
    goal: str = ""
    status: str = "running"          # running | done | stopped | error | paused
    steps: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    history: list = field(default_factory=list)       # 最近动作摘要
    plan_log: list = field(default_factory=list)      # 每段规划记录
    last_ocr: str = ""
    last_image_b64: str = ""
    error: str = ""
    result: dict = field(default_factory=dict)
    _stop_requested: bool = False
    _pause_requested: bool = False

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "game_id": self.game_id,
            "game_name": self.game_name,
            "goal": self.goal,
            "status": self.status,
            "steps": self.steps,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "history": self.history[-30:],
            "last_ocr": self.last_ocr[-800:],
            "error": self.error,
            "result": self.result,
        }

    def touch(self) -> None:
        self.updated_at = time.time()

    def request_stop(self) -> None:
        self._stop_requested = True
        self.status = "stopping"

    def request_pause(self) -> None:
        self._pause_requested = True
        self.status = "pausing"


class Planner:
    """目标 → 操作序列 → 分段执行 + 反馈闭环。"""

    def __init__(
        self,
        *,
        llm: LlmClient,
        executor: Executor,
        store: GameStore,
        guide: Optional[GuideSearch] = None,
        segment_max_steps: int = 6,
        session_max_steps: int = 200,
        allow_python: bool = False,
    ):
        self.llm = llm
        self.executor = executor
        self.store = store
        self.guide = guide
        self.segment_max_steps = segment_max_steps
        self.session_max_steps = session_max_steps
        self.allow_python = allow_python
        self._active: dict[str, PlaySession] = {}

    # ------------------------------------------------------------------
    # 会话管理
    # ------------------------------------------------------------------

    def active_sessions(self) -> list[dict]:
        return [s.to_dict() for s in self._active.values()]

    def get_session(self, session_id: str) -> Optional[PlaySession]:
        return self._active.get(session_id)

    def stop_session(self, session_id: str) -> bool:
        s = self._active.get(session_id)
        if s:
            s.request_stop()
            return True
        return False

    def pause_session(self, session_id: str) -> bool:
        s = self._active.get(session_id)
        if s:
            s.request_pause()
            return True
        return False

    def resume_session(self, session_id: str) -> bool:
        s = self._active.get(session_id)
        if s and s.status in ("pausing", "paused"):
            s._pause_requested = False
            s.status = "running"
            s.touch()
            return True
        return False

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def run(
        self,
        game_id: str,
        goal: str,
        *,
        session: Optional[PlaySession] = None,
        max_segments: int = 60,
    ) -> PlaySession:
        """执行一个目标的完整游玩循环（直到 done/stop/超步数/错误）。"""
        game_id = self.store.validate_game_id(game_id)
        profile = self.store.load_profile(game_id)
        if profile is None:
            raise ExecutorError(f"游戏 {game_id} 还没有 profile，请先学习")

        if session is None:
            session = PlaySession(game_id=game_id, game_name=profile.name, goal=goal)
        self._active[session.session_id] = session
        ops = self.store.load_operations(game_id)

        # 先确保目标窗口
        try:
            await self.executor.ensure_target_window()
        except ExecutorError as exc:
            session.status = "error"
            session.error = str(exc)
            session.result = {"error": str(exc)}
            self.store.save_session(game_id, session.to_dict())
            return session

        segments = 0
        try:
            while session.status == "running":
                if session._stop_requested:
                    session.status = "stopped"
                    break
                if session._pause_requested:
                    session.status = "paused"
                    # 等待恢复
                    while session._pause_requested:
                        await asyncio.sleep(0.3)
                    if session._stop_requested:
                        session.status = "stopped"
                        break
                    session.status = "running"
                if session.steps >= self.session_max_steps:
                    session.status = "done"
                    session.result["summary"] = f"达到最大步数 {self.session_max_steps}"
                    break
                if segments >= max_segments:
                    session.status = "done"
                    session.result["summary"] = f"达到最大规划轮次 {max_segments}"
                    break

                segments += 1
                plan = await self._plan_segment(session, profile, ops, goal)
                session.plan_log.append({"segment": segments, "plan": plan})
                session.touch()
                if not plan or plan.get("done"):
                    session.status = "done"
                    session.result["summary"] = plan.get("reasoning", "目标已达成") if plan else "无操作"
                    break

                # 解析并执行这一段
                steps = _flatten_plan(plan, ops)
                if not steps:
                    # 只有操作库引用全部无效时，当作 done 以免死循环
                    if _plan_only_operations(plan):
                        session.status = "done"
                        session.result["summary"] = "规划了无效操作，停止"
                        break
                    continue
                steps = steps[: self.segment_max_steps]
                result = await self.executor.execute_sequence(steps)
                session.steps += result.get("executed", 0)
                _record_history(session, steps, result)

                # 段末反馈：截图 + OCR
                fb = await self._collect_feedback(session)
                if fb:
                    session.last_ocr = str(fb.get("text") or session.last_ocr)
                    if fb.get("image_base64"):
                        session.last_image_b64 = fb["image_base64"]
                session.touch()
        except asyncio.CancelledError:
            session.status = "stopped"
            session.error = "cancelled"
            raise
        except Exception as exc:
            logger.exception("play loop error")
            session.status = "error"
            session.error = f"{type(exc).__name__}: {exc}"
            session.result["error"] = str(exc)
        finally:
            session.touch()
            self.store.save_session(game_id, session.to_dict())

        if session.session_id in self._active:
            self._active[session.session_id] = session
        return session

    # ------------------------------------------------------------------
    # 内部：规划一段
    # ------------------------------------------------------------------

    async def _plan_segment(
        self,
        session: PlaySession,
        profile: GameProfile,
        ops: list[Operation],
        goal: str,
    ) -> Optional[dict]:
        ops_json = _json([
            {
                "id": o.id,
                "name": o.name,
                "description": o.description,
                "params": o.params,
            }
            for o in ops
        ])
        user = _PLAN_USER_TEMPLATE.format(
            game_name=profile.name,
            profile_json=_json(profile.to_dict()),
            operations_json=ops_json,
            recent_ocr=session.last_ocr[-2000:] or "(暂无截图，第一次规划)",
            history=_json(session.history[-6:]),
            goal=goal,
        )
        try:
            raw = await self.llm.complete_json(
                [{"role": "system", "content": _PLAN_SYSTEM}, {"role": "user", "content": user}],
                max_tokens=4096,
            )
        except Exception as exc:
            logger.warning("plan failed: %s", exc)
            return {"plan": [], "reasoning": f"规划失败: {exc}", "done": True}
        return raw if isinstance(raw, dict) else None

    async def _collect_feedback(self, session: PlaySession) -> Optional[dict]:
        try:
            return await self.executor.read_screen(mode="target", include_boxes=False)
        except ExecutorError as exc:
            logger.debug("feedback collect failed: %s", exc)
            return None


def _flatten_plan(plan: dict, ops: list[Operation]) -> list[dict]:
    """把规划输出展开成 executor 步骤列表（解析 operation 引用）。"""
    raw_steps = plan.get("plan")
    if not isinstance(raw_steps, list):
        return []
    ops_by_id = {o.id: o for o in ops}
    steps: list[dict] = []
    for step in raw_steps:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action") or "").strip()
        if action == "operation":
            op_id = str(step.get("operation_id") or "").strip()
            op = ops_by_id.get(op_id)
            if op is None:
                logger.warning("plan referenced missing operation: %s", op_id)
                continue
            params = dict(step.get("params") or {})
            steps.extend(_expand_operation(op, params))
        else:
            steps.append(step)
    return steps


def _expand_operation(op: Operation, params: dict) -> list[dict]:
    """展开操作库操作（支持 {param.x} 模板替换）。"""
    out: list[dict] = []
    for step in op.steps:
        if not isinstance(step, dict):
            continue
        out.append(_substitute_params(step, params))
    return out


def _substitute_params(step: dict, params: dict) -> dict:
    def repl(v: Any) -> Any:
        if isinstance(v, str):
            s = v
            for k, val in params.items():
                s = s.replace("{{param.%s}}" % k, str(val))
            return s
        if isinstance(v, dict):
            return {kk: repl(vv) for kk, vv in v.items()}
        if isinstance(v, list):
            return [repl(i) for i in v]
        return v

    return repl(step)


def _plan_only_operations(plan: dict) -> bool:
    for step in plan.get("plan") or []:
        if isinstance(step, dict) and str(step.get("action") or "") != "operation":
            return False
    return True


def _record_history(session: PlaySession, steps: list[dict], result: dict) -> None:
    executed = result.get("executed", 0)
    if executed:
        session.history.append({
            "ts": time.time(),
            "steps": executed,
            "actions": [str(s.get("action") or s.get("operation_id") or "?") for s in steps[:8]],
            "error": result.get("error"),
        })


def _json(data: Any) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, indent=2)
