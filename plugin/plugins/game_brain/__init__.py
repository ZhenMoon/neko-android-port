"""game_brain 插件入口。

「游戏大脑」——教猫娘打游戏的通用框架。整体架构：

    game_brain（本插件 = 大脑，自调 LLM）
       │  意图拆解 / 操作生成 / 攻略规划 / 反馈闭环
       ├── brain.learner    AI 搜索玩法 → 生成游戏接口配置草稿 → 生成操作库
       ├── brain.planner    目标 → 操作序列 → 分段执行 + 截图反馈闭环
       ├── brain.executor   把操作步骤翻译成 keyboard_controller 跨插件调用
       ├── brain.guide_search  多轮攻略搜索（跨插件 mcp_adapter → mcp-search-server）+ 正文抓取
       └── brain.models     游戏接口配置 / 操作库 / 会话的本地存储
    ├── keyboard_controller（跨插件：键鼠注入 / 截图+OCR / 窗口定位）
    └── mcp_adapter（跨插件：调用外部 mcp-search-server 多引擎攻略搜索 + 正文抓取）

LLM 配置在插件面板「LLM 设置」里由用户填写；未填写时回退到主系统 cloud 配置。
搜索依赖 mcp_adapter 配置的 mcp-search-server（见 plugin.toml 注释与 docs/guide.md）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    llm_tool,
    ui,
    Ok,
    Err,
    SdkError,
    tr,
)

from .brain.executor import Executor, ExecutorError
from .brain.guide_search import GuideSearch
from .brain.learner import Learner
from .brain.llm import LlmClient, LlmSettings
from .brain.models import GameStore
from .brain.planner import PlaySession, Planner

_CFG_SECTION = "game_brain"


@neko_plugin
class GameBrainPlugin(NekoPluginBase):
    """游戏大脑：训练猫娘玩游戏的通用框架。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self._cfg: Dict[str, Any] = {}
        self._store = GameStore(self.data_path())
        self._llm_settings = LlmSettings()
        self._llm: Optional[LlmClient] = None
        self._guide = GuideSearch(rounds=3)
        self._executor = Executor()
        self._learner: Optional[Learner] = None
        self._planner: Optional[Planner] = None
        self._py_operations_allowed = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = cfg.get(_CFG_SECTION) if isinstance(cfg.get(_CFG_SECTION), dict) else {}
        self._apply_config()
        return Ok({"status": "ready", "games": len(self._store.list_games())})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "shutdown"})

    @lifecycle(id="reload")
    async def reload(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = cfg.get(_CFG_SECTION) if isinstance(cfg.get(_CFG_SECTION), dict) else {}
        self._apply_config()
        return Ok({"status": "reloaded"})

    def _apply_config(self) -> None:
        self._llm_settings = LlmSettings.from_dict(self._cfg)
        self._llm = LlmClient(
            self._llm_settings,
            system_config_provider=self._read_system_config_for_llm,
        )
        mcp_server = str(self._cfg.get("search_mcp_server") or "search")
        engines_raw = self._cfg.get("search_engines")
        engines = [str(e).strip() for e in engines_raw if str(e).strip()] if isinstance(engines_raw, list) else None
        self._guide = GuideSearch(
            mcp_call=self._call_mcp,
            server_name=mcp_server,
            rounds=int(self._cfg.get("search_rounds") or 3),
            max_results_per_query=6,
            engines=engines,
        )
        self._executor = Executor(
            kb_call=self._call_kb,
            step_delay=float(self._cfg.get("step_delay_seconds") or 0.3),
            feedback_min_interval=float(self._cfg.get("feedback_min_interval_seconds") or 2.0),
            window_keywords=str(self._cfg.get("default_window_keywords") or ""),
            allow_unguided=bool(self._cfg.get("allow_unguided") or False),
        )
        self._py_operations_allowed = bool(self._cfg.get("allow_python_operations") or False)
        self._learner = Learner(
            store=self._store,
            llm=self._llm,
            guide=self._guide,
            allow_python=self._py_operations_allowed,
        )
        self._planner = Planner(
            llm=self._llm,
            executor=self._executor,
            store=self._store,
            guide=self._guide,
            segment_max_steps=int(self._cfg.get("segment_max_steps") or 6),
            session_max_steps=int(self._cfg.get("session_max_steps") or 200),
            allow_python=self._py_operations_allowed,
        )

    async def _read_system_config_for_llm(self) -> Dict[str, Any]:
        try:
            res = await self.system_info.get_system_config(timeout=5.0)
        except Exception as exc:
            self.logger.debug("system_info.get_system_config failed: %s", exc)
            return {}
        if isinstance(res, Ok):
            return res.value if isinstance(res.value, dict) else {}
        return {}

    async def _call_kb(self, entry_ref: str, params: dict) -> Any:
        return await self.plugins.call_entry(entry_ref, params, timeout=120.0)

    async def _call_mcp(self, server_name: str, tool_name: str, arguments: dict) -> Any:
        """跨插件调用 mcp_adapter:call_tool 调用 mcp-search-server 工具。"""
        res = await self.plugins.call_entry("mcp_adapter:call_tool", {
            "server_name": server_name,
            "tool_name": tool_name,
            "arguments": arguments,
        }, timeout=max(float(self._cfg.get("search_mcp_timeout_ms") or 25000) / 1000 + 5, 30.0))
        return res

    def _require_llm(self) -> LlmClient:
        if self._llm is None:
            raise SdkError("LLM 客户端未初始化，请先重载插件")
        return self._llm

    def _require_planner(self) -> Planner:
        if self._planner is None:
            raise SdkError("Planner 未初始化，请先重载插件")
        return self._planner

    # ------------------------------------------------------------------
    # UI context
    # ------------------------------------------------------------------

    @ui.context(id="dashboard")
    async def dashboard_context(self, **_):
        games = self._store.list_games()
        return {
            "llm": self._llm_settings.to_public() if self._llm_settings else {},
            "search_server": str(self._cfg.get("search_mcp_server") or "search"),
            "search_available": self._guide.is_available() if self._guide else False,
            "allow_python": bool(self._py_operations_allowed),
            "games": games,
            "sessions": self._planner.active_sessions() if self._planner else [],
            "keyboard_controller_ready": True,
        }

    # ------------------------------------------------------------------
    # LLM 设置
    # ------------------------------------------------------------------

    @ui.action(label=tr("actions.saveLlm.label", default="保存 LLM 设置"), tone="primary", refresh_context=True)
    @plugin_entry(
        id="save_llm_config",
        name=tr("entries.saveLlm.name", default="保存 LLM 设置"),
        description="保存文本/视觉 LLM 配置（url/api_key/model）到插件配置并立即生效。配置本地保存，不硬编码。",
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "llm_url": {"type": "string", "description": "文本 LLM 的 OpenAI 兼容 base_url"},
                "llm_api_key": {"type": "string", "description": "文本 LLM 的 API Key"},
                "llm_model": {"type": "string", "description": "文本 LLM 模型名"},
                "vision_url": {"type": "string", "description": "视觉 LLM 的 OpenAI 兼容 base_url"},
                "vision_api_key": {"type": "string", "description": "视觉 LLM 的 API Key"},
                "vision_model": {"type": "string", "description": "视觉 LLM 模型名"},
            },
        },
    )
    async def save_llm_config(self, **kwargs):
        patch: Dict[str, Any] = {}
        for field_name in (
            "llm_url", "llm_api_key", "llm_model",
            "vision_url", "vision_api_key", "vision_model",
        ):
            val = kwargs.get(field_name)
            if val is not None and val != "":
                patch[field_name] = str(val).strip()
        if patch:
            merged = {k: v for k, v in self._cfg.items()}
            merged.update(patch)
            await self.config.update({_CFG_SECTION: merged}, timeout=5.0)
            self._cfg = merged
            self._apply_config()
        return Ok({"summary": "LLM 配置已保存", "llm": self._llm_settings.to_public()})

    @plugin_entry(
        id="get_llm_config",
        name=tr("entries.getLlm.name", default="读取 LLM 设置"),
        description="读取当前 LLM 配置（key 打码），含文本/视觉是否就绪。",
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
    )
    async def get_llm_config(self, **_):
        return Ok({
            "summary": (
                f"文本: {self._llm_settings.llm_model or '未配置'} | "
                f"视觉: {self._llm_settings.vision_model or '未配置'}"
            ),
            "llm": self._llm_settings.to_public(),
        })

    @plugin_entry(
        id="test_llm_config",
        name=tr("entries.testLlm.name", default="测试 LLM 连接"),
        description="测试文本/视觉 LLM 连通性。",
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
    )
    async def test_llm_config(self, **_):
        llm = self._require_llm()
        try:
            text = await llm.complete_text(
                [{"role": "user", "content": "回复 OK 两个字"}], max_tokens=16
            )
            text_ok = bool(text.strip())
        except Exception as exc:
            text_ok = False
            text_err = f"{type(exc).__name__}: {exc}"
        vision_ok, vision_err = False, ""
        if self._llm_settings.vision_configured():
            try:
                await llm.complete_vision(prompt="回复 OK", image_jpeg_b64=_1px_jpeg())
                vision_ok = True
            except Exception as exc:
                vision_err = f"{type(exc).__name__}: {exc}"
        return Ok({
            "summary": f"文本={'OK' if text_ok else '失败'} 视觉={'OK' if vision_ok else '未测试/失败'}",
            "text": {"ok": text_ok, "error": text_err if not text_ok else ""},
            "vision": {"ok": vision_ok, "error": vision_err, "configured": bool(self._llm_settings.vision_configured())},
        })

    # ------------------------------------------------------------------
    # 学习一个游戏
    # ------------------------------------------------------------------

    @ui.action(label=tr("actions.learn.label", default="学习游戏"), tone="primary", refresh_context=True)
    @plugin_entry(
        id="learn_game",
        name=tr("entries.learn.name", default="学习游戏"),
        description=(
            "让 AI 学习一个新游戏：联网搜索该游戏的玩法/键位/攻略，提炼出游戏的"
            "接口配置草稿（输入操作 + 可观测信息）。生成草稿后需要用户在面板确认。"
            "参数 game_name 是游戏名，window_keywords 可选（窗口标题关键字）。"
        ),
        llm_result_fields=["summary", "game_id"],
        input_schema={
            "type": "object",
            "properties": {
                "game_name": {"type": "string", "description": "游戏名，如 原神"},
                "window_keywords": {"type": "string", "description": "可选：游戏窗口标题关键字"},
            },
            "required": ["game_name"],
        },
    )
    async def learn_game(self, game_name: str, window_keywords: str = "", **_):
        if not (game_name or "").strip():
            return Err(SdkError("game_name 不能为空"))
        learner = self._learner
        if learner is None:
            return Err(SdkError("Learner 未初始化，请先重载插件"))
        try:
            result = await learner.learn_game(
                game_name.strip(),
                extra_keywords=[window_keywords.strip()] if window_keywords.strip() else None,
                with_rounds=int(self._cfg.get("search_rounds") or 3),
            )
        except Exception as exc:
            return Err(SdkError(f"学习失败: {type(exc).__name__}: {exc}"))
        draft = result.get("draft") or {}
        game_id = result.get("game_id") or ""
        if window_keywords.strip() and draft.get("window_keywords"):
            draft["window_keywords"] = list(dict.fromkeys(
                list(draft["window_keywords"]) + [window_keywords.strip()]
            ))
            self._store.save_draft(game_id, draft)
        return Ok({
            "summary": f"已生成 {game_id} 的接口配置草稿（{len(draft.get('inputs') or [])} 个输入操作），请在面板确认",
            "game_id": game_id,
            "draft": draft,
            "guide_available": bool((result.get("guide") or {}).get("available")),
            "guide_count": len((result.get("guide") or {}).get("results") or []),
        })

    @plugin_entry(
        id="get_draft",
        name=tr("entries.getDraft.name", default="读取配置草稿"),
        description="读取某个游戏的接口配置草稿（LLM 生成、待确认）。",
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {"game_id": {"type": "string"}},
            "required": ["game_id"],
        },
    )
    async def get_draft(self, game_id: str, **_):
        draft = self._store.load_draft(game_id.strip())
        if draft is None:
            return Err(SdkError(f"没有 {game_id} 的草稿"))
        return Ok({"summary": f"{game_id} 草稿：{len(draft.get('inputs') or [])} 个输入", "draft": draft})

    @plugin_entry(
        id="update_draft",
        name=tr("entries.updateDraft.name", default="修改配置草稿"),
        description="在面板手动修改某游戏的接口配置草稿（inputs/observations/window_keywords 等）。",
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "game_id": {"type": "string"},
                "draft": {"type": "object", "description": "修改后的草稿全文"},
            },
            "required": ["game_id", "draft"],
        },
    )
    async def update_draft(self, game_id: str, draft: dict, **_):
        game_id = self._store.validate_game_id(game_id.strip())
        if not isinstance(draft, dict) or not draft:
            return Err(SdkError("draft 必须是 dict"))
        draft["game_id"] = game_id
        self._store.save_draft(game_id, draft)
        return Ok({"summary": f"{game_id} 草稿已更新", "game_id": game_id})

    @ui.action(label=tr("actions.confirmGame.label", default="确认接口配置"), tone="primary", refresh_context=True)
    @plugin_entry(
        id="confirm_game",
        name=tr("entries.confirmGame.name", default="确认游戏接口配置"),
        description=(
            "把某个游戏的配置草稿确认为正式接口配置（确认后才能生成操作库、开始游玩）。"
            "可传 draft 覆盖草稿（面板修改后确认）。"
        ),
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "game_id": {"type": "string"},
                "draft": {"type": "object", "description": "可选：确认前的最新草稿"},
            },
            "required": ["game_id"],
        },
    )
    async def confirm_game(self, game_id: str, draft: Optional[dict] = None, **_):
        learner = self._learner
        if learner is None:
            return Err(SdkError("Learner 未初始化"))
        try:
            profile = await learner.confirm_draft(game_id.strip(), draft=draft)
        except Exception as exc:
            return Err(SdkError(f"确认失败: {type(exc).__name__}: {exc}"))
        return Ok({
            "summary": f"{profile.name} 接口配置已确认（{len(profile.inputs)} 个输入操作）",
            "game_id": profile.game_id,
            "profile": profile.to_dict(),
        })

    @plugin_entry(
        id="list_games",
        name=tr("entries.listGames.name", default="列出已学习游戏"),
        description="列出所有已学习/已生成草稿的游戏及其状态。",
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
    )
    async def list_games(self, **_):
        games = self._store.list_games()
        return Ok({
            "summary": f"共 {len(games)} 个游戏",
            "games": games,
        })

    @plugin_entry(
        id="get_game_profile",
        name=tr("entries.getProfile.name", default="读取游戏接口配置"),
        description="读取某个游戏已确认的接口配置。",
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {"game_id": {"type": "string"}},
            "required": ["game_id"],
        },
    )
    async def get_game_profile(self, game_id: str, **_):
        profile = self._store.load_profile(game_id.strip())
        if profile is None:
            return Err(SdkError(f"游戏 {game_id} 还没有确认的 profile"))
        return Ok({
            "summary": f"{profile.name}：{len(profile.inputs)} 个输入操作",
            "profile": profile.to_dict(),
        })

    # ------------------------------------------------------------------
    # 操作库
    # ------------------------------------------------------------------

    @ui.action(label=tr("actions.genOps.label", default="生成操作库"), tone="primary", refresh_context=True)
    @plugin_entry(
        id="generate_operations",
        name=tr("entries.genOps.name", default="生成基础操作库"),
        description=(
            "基于某个已确认接口配置的游戏，让 LLM 生成基础操作库（可复用的命名操作，"
            "参数化 JSON 步骤）。操作库会保存在本地。"
        ),
        llm_result_fields=["summary", "count"],
        input_schema={
            "type": "object",
            "properties": {"game_id": {"type": "string"}},
            "required": ["game_id"],
        },
    )
    async def generate_operations(self, game_id: str, **_):
        learner = self._learner
        if learner is None:
            return Err(SdkError("Learner 未初始化"))
        try:
            ops = await learner.generate_operations(game_id.strip())
        except Exception as exc:
            return Err(SdkError(f"生成操作库失败: {type(exc).__name__}: {exc}"))
        return Ok({
            "summary": f"已生成 {len(ops)} 个基础操作",
            "count": len(ops),
            "operations": [o.to_dict() for o in ops],
        })

    @plugin_entry(
        id="list_operations",
        name=tr("entries.listOps.name", default="列出基础操作库"),
        description="列出某个游戏的基础操作库（命名操作）。",
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {"game_id": {"type": "string"}},
            "required": ["game_id"],
        },
    )
    async def list_operations(self, game_id: str, **_):
        ops = self._store.load_operations(game_id.strip())
        return Ok({
            "summary": f"{game_id} 操作库：{len(ops)} 个操作",
            "operations": [o.to_dict() for o in ops],
        })

    # ------------------------------------------------------------------
    # 游玩
    # ------------------------------------------------------------------

    @ui.action(label=tr("actions.play.label", default="开始游玩"), tone="primary", refresh_context=True)
    @plugin_entry(
        id="play",
        name=tr("entries.play.name", default="玩游戏（目标→操作序列→反馈闭环）"),
        description=(
            "让猫娘按给定目标玩一个已学习游戏：LLM 把目标拆成操作序列，分段执行并"
            "截图+OCR 反馈，根据游戏内反馈调整，直到目标达成/超时/手动停止。"
            "需要该游戏已确认接口配置；操作库非必需（没有时 LLM 会用接口配置直接规划）。"
        ),
        llm_result_fields=["summary", "session_id", "status"],
        input_schema={
            "type": "object",
            "properties": {
                "game_id": {"type": "string", "description": "已学习的游戏 ID"},
                "goal": {"type": "string", "description": "自然语言目标，如 去打那个 Boss"},
                "max_segments": {"type": "integer", "default": 60, "description": "最大规划轮次"},
            },
            "required": ["game_id", "goal"],
        },
    )
    async def play(self, game_id: str, goal: str, max_segments: int = 60, **_):
        if not (goal or "").strip():
            return Err(SdkError("goal 不能为空"))
        planner = self._require_planner()
        # 先返回 session_id，后台跑循环；会话进度通过 play_status 查询
        session = PlaySession(game_id=game_id.strip(), goal=goal.strip())
        task = asyncio.create_task(
            planner.run(game_id.strip(), goal.strip(), session=session, max_segments=max(int(max_segments), 1)),
            name=f"game_brain.play:{goal.strip()[:40]}",
        )
        task.add_done_callback(lambda t: self._on_play_done(t, session))
        return Ok({
            "summary": f"已开始游玩：{goal.strip()}",
            "session_id": session.session_id,
            "status": "running",
        })

    def _on_play_done(self, task: asyncio.Task, session: PlaySession) -> None:
        try:
            task.result()
        except Exception as exc:
            self.logger.warning("[play] session %s crashed: %s", session.session_id, exc)
        try:
            self.push_message(
                source="game_brain",
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": f"[游戏大脑] 对局 {session.goal[:40]} 已结束：{session.status}"}],
                priority=5,
            )
        except Exception as exc:
            self.logger.warning("[play] done cue push failed: %s", exc)

    @plugin_entry(
        id="play_status",
        name=tr("entries.playStatus.name", default="对局状态"),
        description="查询当前进行中的对局会话状态（目标/步数/最近画面/结果）。",
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
    )
    async def play_status(self, **_):
        sessions = self._planner.active_sessions() if self._planner else []
        summary = "无进行中对局" if not sessions else f"{len(sessions)} 个对局进行中"
        return Ok({"summary": summary, "sessions": sessions})

    @plugin_entry(
        id="stop_play",
        name=tr("entries.stopPlay.name", default="停止对局"),
        description="请求停止某个进行中的对局会话。",
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    )
    async def stop_play(self, session_id: str, **_):
        planner = self._require_planner()
        stopped = planner.stop_session(session_id.strip())
        if not stopped:
            return Err(SdkError(f"会话 {session_id} 不存在或已结束"))
        return Ok({"summary": f"已请求停止会话 {session_id}", "session_id": session_id})

    @plugin_entry(
        id="pause_play",
        name=tr("entries.pausePlay.name", default="暂停对局"),
        description="暂停某个进行中的对局会话（可恢复）。",
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    )
    async def pause_play(self, session_id: str, **_):
        planner = self._require_planner()
        paused = planner.pause_session(session_id.strip())
        if not paused:
            return Err(SdkError(f"会话 {session_id} 不存在或无法暂停"))
        return Ok({"summary": f"已请求暂停会话 {session_id}", "session_id": session_id})

    @plugin_entry(
        id="resume_play",
        name=tr("entries.resumePlay.name", default="恢复对局"),
        description="恢复一个已暂停的对局会话。",
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    )
    async def resume_play(self, session_id: str, **_):
        planner = self._require_planner()
        resumed = planner.resume_session(session_id.strip())
        if not resumed:
            return Err(SdkError(f"会话 {session_id} 不存在或不在暂停态"))
        return Ok({"summary": f"已恢复会话 {session_id}", "session_id": session_id})

    # ------------------------------------------------------------------
    # 诊断 / LLM 工具
    # ------------------------------------------------------------------

    @plugin_entry(
        id="game_brain_status",
        name=tr("entries.status.name", default="游戏大脑状态"),
        description=(
            "查询 game_brain 整体状态：LLM 配置是否就绪、已学习游戏列表、进行中对局数。"
            "适合对话里出现游戏自动化相关疑问、或刚开始学习/游玩前确认就绪时调用。"
        ),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
    )
    async def game_brain_status(self, **_):
        games = self._store.list_games()
        sessions = self._planner.active_sessions() if self._planner else []
        summary = (
            f"LLM={'就绪' if self._llm_settings.text_configured() else '未配置'} | "
            f"已学习 {len(games)} 游戏 | 对局 {len(sessions)}"
        )
        return Ok({
            "summary": summary,
            "llm": self._llm_settings.to_public(),
            "games": games,
            "active_sessions": sessions,
        })

    @llm_tool(
        name="game_brain_play",
        description=(
            "Dispatch a gaming goal to the game_brain plugin: the plugin's own LLM "
            "turns the natural-language goal into keyboard/mouse action sequences, "
            "executes them via keyboard_controller, and iterates with screenshot+OCR "
            "feedback. Returns immediately with a session id; progress is reported "
            "asynchronously. Requires a previously-learned game (see "
            "game_brain_learn)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "game_id": {"type": "string", "description": "Learned game id"},
                "goal": {"type": "string", "description": "Natural-language gaming goal"},
                "max_segments": {"type": "integer", "description": "Max planning rounds", "default": 60},
            },
            "required": ["game_id", "goal"],
        },
        timeout=30.0,
    )
    async def game_brain_play_tool(self, *, game_id: Any = None, goal: Any = None, max_segments: Any = 60, **_):
        if not isinstance(game_id, str) or not isinstance(goal, str) or not goal.strip():
            return {"summary": "参数错误：需要 game_id（字符串）和 goal（非空字符串）"}
        res = await self.play(game_id=game_id, goal=goal, max_segments=int(max_segments or 60))
        if isinstance(res, Err):
            return {"summary": f"启动失败: {res.error}"}
        return res.value if isinstance(res, Ok) else {}

    @llm_tool(
        name="game_brain_learn",
        description=(
            "Ask the game_brain plugin to learn a new game: it searches the web for "
            "gameplay/controls/walkthrough info, then generates a per-game interface "
            "config draft (input operations + observable screen info). The draft still "
            "needs user confirmation in the plugin panel before the game can be played."
        ),
        parameters={
            "type": "object",
            "properties": {
                "game_name": {"type": "string", "description": "Game name"},
                "window_keywords": {"type": "string", "description": "Optional window title keywords"},
            },
            "required": ["game_name"],
        },
        timeout=120.0,
    )
    async def game_brain_learn_tool(self, *, game_name: Any = None, window_keywords: Any = None, **_):
        if not isinstance(game_name, str) or not game_name.strip():
            return {"summary": "参数错误：需要 game_name（字符串）"}
        res = await self.learn_game(
            game_name=game_name,
            window_keywords=window_keywords if isinstance(window_keywords, str) else "",
        )
        if isinstance(res, Err):
            return {"summary": f"学习失败: {res.error}"}
        return res.value if isinstance(res, Ok) else {}

    @llm_tool(
        name="game_brain_status_tool",
        description=(
            "Query game_brain status: LLM readiness, learned games, active sessions. "
            "Use before playing to confirm a game is learned and the LLM is configured."
        ),
        parameters={"type": "object", "properties": {}},
        timeout=20.0,
    )
    async def game_brain_status_tool(self, **_):
        res = await self.game_brain_status()
        if isinstance(res, Ok):
            return res.value
        return {"summary": f"状态查询失败: {res.error if isinstance(res, Err) else res}"}


def _1px_jpeg() -> str:
    """一个 1x1 的极小 JPEG（base64），用于测试视觉模型连通性。"""
    import base64 as _b64

    # 最小合法 JPEG（灰点）
    return _b64.b64encode(bytes.fromhex(
        "FFD8FFE000104A46494600010101006000600000FFDB004300080606070605080707070909"
        "080A0C140D0C0B0B0C1912130F141D1A1F1E1D1A1C1C20242E2720222C231C1C2837292C"
        "30313434331F27393D38323C2E333432FFC0000B080001000101011100FFC4001F000001"
        "0501010101010100000000000000000102030405060708090A0BFFC400B5100002010303"
        "020403050504040400017D01020300041105122131410613516107227114328191A10823"
        "42B1C11552D1F02433627282090A161718191A25262728292A3435363738393A43444546"
        "4748494A535455565758595A636465666768696A737475767778797A838485868788898A"
        "92939495969798999AA2A3A4A5A6A7A8A9AAB2B3B4B5B6B7B8B9BAC2C3C4C5C6C7C8C9"
        "CAD2D3D4D5D6D7D8D9DAE1E2E3E4E5E6E7E8E9EAF1F2F3F4F5F6F7F8F9FAFFC4001411"
        "0100000000000000000000000000000000FFDA000C03010002110311003F001F00FFD9"
    )).decode("ascii")
