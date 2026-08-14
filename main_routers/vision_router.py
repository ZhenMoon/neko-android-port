# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Vision ingest: YUI sees the world through the Android camera.

The floating pet widget captures a camera frame and POSTs it here; the backend
injects the frame into the vision pipeline (``prompt_ephemeral(images=[...])``,
which switches to the vision model) and YUI proactively comments — triggered
manually by the user or periodically on auto glance.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request

from main_routers.shared_state import get_session_manager
from main_routers.system_router._shared import _validate_local_mutation_request

router = APIRouter(prefix="/api/vision", tags=["vision"])

# 单帧 JPEG base64 上限（约 2MB，720p JPEG 远小于此）；防御异常大帧。
_MAX_FRAME_B64_LEN = 4_000_000


def _resolve_lanlan_name(requested: Any) -> str | None:
    """Pick the character to speak as.

    Prefer the requested name; otherwise fall back to the first active
    session in the manager (the floating widget does not always know the
    character name, e.g. periodic auto-glance frames).
    """
    if isinstance(requested, str) and requested.strip():
        return requested.strip()
    try:
        session_manager = get_session_manager()
        for name, mgr in session_manager.items():
            if getattr(mgr, "is_active", False) and getattr(mgr, "is_active"):
                return name
    except Exception:
        pass
    if session_manager:
        return next(iter(session_manager))
    return None


@router.post("/camera-frame")
async def ingest_camera_frame(request: Request):
    """Accept one camera frame (JPEG base64) and schedule YUI to comment.

    Request body: ``{"b64": "...", "trigger": "manual"|"auto",
    "lanlan_name": "YUI" (optional)}``.

    ``manual`` comments immediately (15s throttle); ``auto`` only speaks when
    the vision model judges the scene worth it (90s throttle). The reply
    streams back to the connected WebSocket as a normal proactive message.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    validation_error = _validate_local_mutation_request(
        request,
        payload=payload,
        error_defaults={"success": False},
    )
    if validation_error is not None:
        return validation_error

    b64 = payload.get("b64")
    if not isinstance(b64, str) or not b64.strip():
        return {"success": False, "error": "缺少摄像头画面帧 b64"}
    if len(b64) > _MAX_FRAME_B64_LEN:
        return {"success": False, "error": "画面帧过大"}
    trigger_mode = str(payload.get("trigger", "manual")).strip().lower()
    if trigger_mode not in ("manual", "auto"):
        trigger_mode = "manual"

    lanlan_name = _resolve_lanlan_name(payload.get("lanlan_name"))
    if not lanlan_name:
        return {"success": False, "error": "没有可用的角色会话"}

    session_manager = get_session_manager()
    mgr = session_manager.get(lanlan_name)
    if mgr is None:
        return {"success": False, "error": f"角色不存在: {lanlan_name}"}

    trigger = getattr(mgr, "trigger_camera_comment", None)
    if not callable(trigger):
        return {"success": False, "error": "当前角色不支持摄像头搭话"}

    asyncio.get_running_loop().create_task(trigger(b64, trigger_mode=trigger_mode))
    return {"success": True, "scheduled": True, "trigger": trigger_mode}
