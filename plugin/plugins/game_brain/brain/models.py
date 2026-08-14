"""游戏接口配置与基础操作库的数据模型及本地存储。

文件布局（全部在插件 data/ 目录下，本地保存）：

    data/
    └── games/
        └── <game_id>/
            ├── profile.json          # 游戏接口配置（用户确认过）
            ├── profile.draft.json    # AI 搜索玩法后生成的草稿（待确认）
            ├── operations.json       # 基础操作库
            ├── sessions/             # 对局记录
            │   └── <ts>.json
            └── guide_cache/          # 攻略搜索结果缓存（供生成操作库参考）
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

GAME_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ---------------------------------------------------------------------------
# 游戏接口配置（GameProfile）
# ---------------------------------------------------------------------------

@dataclass
class InputPrimitive:
    """一个可用输入原语：游戏中的一次基础输入。

    kind: keyboard | mouse | text
      keyboard: keys="w" / "shift" / "ctrl+c"（与 keyboard_controller 键名一致）
      mouse:    button=left|right|middle, action=click|move|drag|wheel
      text:     text="内容"
    """
    id: str
    name: str
    description: str = ""
    kind: str = "keyboard"
    keys: str = ""
    button: str = "left"
    action: str = "click"
    text: str = ""
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "keys": self.keys,
            "button": self.button,
            "action": self.action,
            "text": self.text,
            "params": dict(self.params),
        }


@dataclass
class ObservationSource:
    """一个可观测信息源：游戏里能读到什么。

    kind: fullscreen_ocr | region_ocr
      fullscreen_ocr: 整窗截图+OCR（文本）
      region_ocr:     窗口内相对区域截图+OCR
    region: [x, y, w, h] 归一化相对坐标（0~1），相对目标窗口客户区。
    """
    id: str
    name: str
    description: str = ""
    kind: str = "fullscreen_ocr"
    region: Optional[list] = None
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "region": list(self.region) if self.region else None,
            "params": dict(self.params),
        }


@dataclass
class GameProfile:
    """一个游戏的接口配置（用户确认过的版本）。"""

    game_id: str
    name: str
    window_keywords: list = field(default_factory=list)
    inputs: list = field(default_factory=list)          # list[InputPrimitive]
    observations: list = field(default_factory=list)    # list[ObservationSource]
    notes: str = ""
    source: str = ""            # 生成来源：ai / manual
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "name": self.name,
            "window_keywords": list(self.window_keywords),
            "inputs": [i.to_dict() for i in self.inputs],
            "observations": [o.to_dict() for o in self.observations],
            "notes": self.notes,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GameProfile":
        inputs = []
        for i in data.get("inputs") or []:
            inputs.append(InputPrimitive(**{k: i.get(k) for k in (
                "id", "name", "description", "kind", "keys",
                "button", "action", "text", "params",
            )}))
        observations = []
        for o in data.get("observations") or []:
            observations.append(ObservationSource(**{k: o.get(k) for k in (
                "id", "name", "description", "kind", "region", "params",
            )}))
        return cls(
            game_id=str(data.get("game_id") or ""),
            name=str(data.get("name") or ""),
            window_keywords=list(data.get("window_keywords") or []),
            inputs=inputs,
            observations=observations,
            notes=str(data.get("notes") or ""),
            source=str(data.get("source") or ""),
            created_at=float(data.get("created_at") or 0),
            updated_at=float(data.get("updated_at") or 0),
        )


# ---------------------------------------------------------------------------
# 基础操作库（Operation）
# ---------------------------------------------------------------------------

@dataclass
class Operation:
    """一个可复用的命名操作。

    执行形态二选一：
      steps: 参数化 JSON 步骤（推荐，安全）。每步形如
             {"action": "press_sequence_item", ...} 或简化为操作原语。
      python: 受限 Python 源码（仅当 allow_python_operations=true）。
    """
    id: str
    name: str
    description: str = ""
    steps: list = field(default_factory=list)   # list[dict]
    python: str = ""
    params: dict = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "steps": self.steps,
            "python": self.python,
            "params": dict(self.params),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Operation":
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
            steps=list(data.get("steps") or []),
            python=str(data.get("python") or ""),
            params=dict(data.get("params") or {}),
            created_at=float(data.get("created_at") or 0),
            updated_at=float(data.get("updated_at") or 0),
        )


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------

class GameStore:
    """游戏接口配置 + 操作库 + 会话记录的本地存储。"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.games_dir = self.data_dir / "games"
        self.games_dir.mkdir(parents=True, exist_ok=True)

    # ---- 路径 ----

    def game_dir(self, game_id: str) -> Path:
        d = self.games_dir / game_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def profile_path(self, game_id: str) -> Path:
        return self.game_dir(game_id) / "profile.json"

    def draft_path(self, game_id: str) -> Path:
        return self.game_dir(game_id) / "profile.draft.json"

    def operations_path(self, game_id: str) -> Path:
        return self.game_dir(game_id) / "operations.json"

    def sessions_dir(self, game_id: str) -> Path:
        d = self.game_dir(game_id) / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def guide_cache_dir(self, game_id: str) -> Path:
        d = self.game_dir(game_id) / "guide_cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def validate_game_id(game_id: str) -> str:
        game_id = (game_id or "").strip()
        if not GAME_ID_RE.fullmatch(game_id):
            raise ValueError(
                f"无效的游戏 ID: {game_id!r}（需匹配 {GAME_ID_RE.pattern}）"
            )
        return game_id

    # ---- Profile ----

    def save_profile(self, profile: GameProfile) -> GameProfile:
        profile.game_id = self.validate_game_id(profile.game_id)
        profile.updated_at = time.time()
        if not profile.created_at:
            profile.created_at = profile.updated_at
        path = self.profile_path(profile.game_id)
        _write_json(path, profile.to_dict())
        return profile

    def load_profile(self, game_id: str) -> Optional[GameProfile]:
        path = self.profile_path(self.validate_game_id(game_id))
        if not path.exists():
            return None
        return GameProfile.from_dict(_read_json(path))

    def save_draft(self, game_id: str, draft: dict) -> None:
        _write_json(self.draft_path(self.validate_game_id(game_id)), draft)

    def load_draft(self, game_id: str) -> Optional[dict]:
        path = self.draft_path(self.validate_game_id(game_id))
        if not path.exists():
            return None
        return _read_json(path)

    def list_games(self) -> list[dict]:
        out = []
        for d in sorted(self.games_dir.iterdir()):
            if not d.is_dir():
                continue
            profile = self.load_profile(d.name)
            ops = self.load_operations(d.name)
            out.append({
                "game_id": d.name,
                "name": profile.name if profile else d.name,
                "has_profile": profile is not None,
                "has_draft": (d / "profile.draft.json").exists(),
                "operation_count": len(ops),
            })
        return out

    # ---- Operations ----

    def save_operations(self, game_id: str, ops: list[Operation]) -> None:
        path = self.operations_path(self.validate_game_id(game_id))
        _write_json(path, [o.to_dict() for o in ops])

    def load_operations(self, game_id: str) -> list[Operation]:
        path = self.operations_path(self.validate_game_id(game_id))
        if not path.exists():
            return []
        return [Operation.from_dict(o) for o in _read_json(path)]

    # ---- Sessions ----

    def save_session(self, game_id: str, session: dict) -> str:
        session_id = str(session.get("session_id") or uuid.uuid4().hex[:12])
        session["session_id"] = session_id
        path = self.sessions_dir(self.validate_game_id(game_id)) / f"{session_id}.json"
        _write_json(path, session)
        return session_id

    def load_session(self, game_id: str, session_id: str) -> Optional[dict]:
        path = self.sessions_dir(self.validate_game_id(game_id)) / f"{session_id}.json"
        if not path.exists():
            return None
        return _read_json(path)

    def list_sessions(self, game_id: str, limit: int = 20) -> list[dict]:
        d = self.sessions_dir(self.validate_game_id(game_id))
        files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        out = []
        for p in files[:limit]:
            try:
                data = _read_json(p)
            except Exception:
                continue
            out.append({
                "session_id": data.get("session_id", p.stem),
                "goal": data.get("goal", ""),
                "status": data.get("status", "unknown"),
                "steps": data.get("steps", 0),
                "created_at": data.get("created_at", 0),
                "updated_at": data.get("updated_at", 0),
            })
        return out


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
