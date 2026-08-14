# -*- coding: utf-8 -*-
"""N.E.K.O. Android entry point (Chaquopy).

Boots the memory server and main server in a single process (Android has no
multiprocessing.sem_open), serving the existing web frontend on 127.0.0.1.
"""

import asyncio
import os
import sys
import time

_HOME = os.environ.get("HOME", "/data/data/com.neko.android/files")
_ROOT = os.path.dirname(os.path.abspath(__file__))


def _setup_environment() -> None:
    os.environ.setdefault("HOME", _HOME)
    os.environ.setdefault("NEKO_STORAGE_SELECTED_ROOT", os.path.join(_HOME, "N.E.K.O"))
    os.environ.setdefault("NEKO_STORAGE_ANCHOR_ROOT", os.path.join(_HOME, "N.E.K.O"))
    os.environ.setdefault("NEKO_APP_ROOT", _ROOT)
    os.environ.setdefault("NEKO_PROJECT_ROOT", _ROOT)
    # Android has no System V IPC: never spawn separate server processes.
    os.environ["NEKO_MERGED"] = "1"
    # OpenBLAS / OMP / MKL thread-pool init is a known hang source on
    # Android emulators; pin to 1 thread to keep numpy import deterministic.
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    try:
        os.makedirs(os.path.join(_HOME, "N.E.K.O"), exist_ok=True)
        os.chdir(_ROOT)
    except OSError:
        pass


def _install_debug_dump() -> None:
    """Self-diagnostics: dump all python thread stacks to a file a bit after
    startup so a silent import hang can be located from the log files."""
    import faulthandler
    import threading

    def _dump() -> None:
        time.sleep(45)
        try:
            with open(os.path.join(_HOME, "python_dump.log"), "w") as f:
                faulthandler.dump_traceback(file=f)
            with open(os.path.join(_HOME, "python_mark.log"), "a") as f:
                f.write("dump done\n")
        except Exception:
            pass

    threading.Thread(target=_dump, daemon=True).start()

    _tracer = open(os.path.join(_HOME, "python_imports.log"), "w")

    def _trace_import(name, *args, **kwargs):
        _tracer.write(f"{time.strftime('%H:%M:%S')} import {name}\n")
        _tracer.flush()
        try:
            return _real_import(name, *args, **kwargs)
        except BaseException as e:
            _tracer.write(
                f"  !! import FAILED {name}: {type(e).__name__}: {e}\n"
            )
            _tracer.flush()
            raise

    import builtins
    _real_import = builtins.__import__
    builtins.__import__ = _trace_import


def _bootstrap_storage_policy() -> None:
    """Android has one internal storage root; pre-write the storage policy so
    the first-run storage-selection flow is skipped and both servers boot in
    normal (non-limited) mode instead of waiting on the web picker.

    The anchor must resolve exactly like the servers do
    (utils.storage.location_bootstrap._get_configured_anchor_root):
    prefer ``config_manager.anchor_root``, else fall back to
    ``compute_anchor_root``.  Using ``compute_anchor_root`` directly would
    target the XDG data dir (.local/share) instead of the env-resolved root."""
    try:
        from pathlib import Path

        from utils.config_manager import get_config_manager
        from utils.storage.policy import (
            compute_anchor_root,
            load_storage_policy,
            normalize_runtime_root,
            save_storage_policy,
        )

        cm = get_config_manager()
        current = normalize_runtime_root(cm.app_docs_dir)
        configured_anchor = getattr(cm, "anchor_root", None)
        if configured_anchor:
            anchor = Path(configured_anchor).expanduser().resolve(strict=False)
        else:
            anchor = compute_anchor_root(cm, current_root=current)
        policy = load_storage_policy(cm, anchor_root=anchor)
        if isinstance(policy, dict) and bool(policy.get("first_run_completed")):
            return
        save_storage_policy(
            cm,
            selected_root=current,
            selection_source="default",
            anchor_root=anchor,
        )
        print(f"[Android] storage policy initialized: current={current} anchor={anchor}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[Android] storage policy bootstrap skipped: {e}", flush=True)


def main() -> None:
    # Shims first: PIL / yaml / av / pyautogui / ... have no Android wheel.
    import neko_shims
    neko_shims.install()

    _setup_environment()
    _install_debug_dump()

    def _mark(msg: str) -> None:
        with open(os.path.join(_HOME, "python_mark.log"), "a") as _f:
            _f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")

    _mark("start")
    print("[Android] Importing N.E.K.O. servers...", flush=True)

    _mark("importing memory_server")
    from app import memory_server
    _mark("memory_server imported")

    _mark("importing numpy")
    import numpy  # noqa: F401
    _mark("numpy imported")

    _mark("importing soxr")
    import soxr  # noqa: F401
    _mark("soxr imported")
    try:
        _mark(f"soxr ResampleStream.clear present: {hasattr(soxr.ResampleStream, 'clear')}")
    except Exception:
        pass

    _mark("openai patch check: begin")
    try:
        import asyncio as _asyncio
        from openai.resources.chat.completions import AsyncCompletions as _AC

        class _FakeTransport:
            async def _post(self, path, *, body, cast_to, options, files=None, stream=None, stream_cls=None):
                return None

        async def _probe():
            return await _AC.create(
                _FakeTransport(),
                model="m",
                messages=[{"role": "user", "content": "hi"}],
                max_completion_tokens=64,
            )

        _asyncio.run(_probe())
        _mark("openai patch check: OK (max_completion_tokens accepted)")
    except Exception as _e:
        _mark(f"openai patch check: FAIL {type(_e).__name__}: {_e}")

    _mark("importing ast standalone")
    import ast  # noqa: F401
    _mark("ast imported standalone")

    _mark("router agent_router")
    from main_routers.agent_router import router as _r  # noqa: F401
    _mark("router agent_router ok")
    _mark("router avatar_drop_router")
    from main_routers.avatar_drop_router import router as _r  # noqa: F401
    _mark("router avatar_drop_router ok")
    _mark("router card_assist_router")
    from main_routers.card_assist_router import router as _r  # noqa: F401
    _mark("router card_assist_router ok")
    _mark("router capture_router")
    from main_routers.capture_router import router as _r  # noqa: F401
    _mark("router capture_router ok")
    _mark("router characters_router")
    from main_routers.characters_router import router as _r  # noqa: F401
    _mark("router characters_router ok")
    _mark("router cloudsave_router")
    from main_routers.cloudsave_router import router as _r  # noqa: F401
    _mark("router cloudsave_router ok")
    _mark("router config_router")
    from main_routers.config_router import router as _r  # noqa: F401
    _mark("router config_router ok")
    _mark("router proactive_router")
    from main_routers.proactive_router import router as _r  # noqa: F401
    _mark("router proactive_router ok")
    _mark("router galgame_router")
    from main_routers.galgame_router import router as _r  # noqa: F401
    _mark("router galgame_router ok")
    _mark("router widget_mode_router")
    from main_routers.widget_mode_router import router as _r  # noqa: F401
    _mark("router widget_mode_router ok")
    _mark("router icebreaker_router")
    from main_routers.icebreaker_router import router as _r  # noqa: F401
    _mark("router icebreaker_router ok")

    _mark("importing main_server")
    from app import main_server
    _mark("main_server imported")

    _mark("importing agent_server")
    from app import agent_server
    _mark("agent_server imported")

    _mark("bootstrap storage policy")
    _bootstrap_storage_policy()
    _mark("storage policy done")

    print("[Android] Servers imported. Starting merged runner...", flush=True)

    from config import MAIN_SERVER_PORT, MEMORY_SERVER_PORT, TOOL_SERVER_PORT

    main_server.set_start_config(
        {
            "browser_mode_enabled": False,
            "browser_page": "",
            "shutdown_memory_server_on_exit": False,
            "server": None,
        }
    )

    import uvicorn

    apps = [
        (memory_server.app, MEMORY_SERVER_PORT, "Memory"),
        (agent_server.app, TOOL_SERVER_PORT, "Agent"),
        (main_server.app, MAIN_SERVER_PORT, "Main"),
    ]

    servers = []
    for _app, _port, _name in apps:
        cfg = uvicorn.Config(
            app=_app,
            host="127.0.0.1",
            port=_port,
            log_level="info",
            ws_ping_interval=20.0,
            ws_ping_timeout=60.0,
        )
        servers.append(uvicorn.Server(cfg))

    for s in servers:
        for attr in ("install_signal_handlers",):
            if hasattr(s, attr):
                setattr(s, attr, lambda: None)

    async def _run_server(s, name: str) -> None:
        try:
            await s.serve()
        except Exception as e:  # noqa: BLE001
            print(f"[Android] {name} server exited: {e}", file=sys.stderr, flush=True)

    async def run_all() -> None:
        await asyncio.gather(*(_run_server(s, n) for s, n in zip(servers, [_n for _a, _p, _n in apps])))

    # 通知 Java 层 main server 已就绪（WebView 轮询 /health）。
    def _wait_ready() -> None:
        import httpx
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{MAIN_SERVER_PORT}/health", timeout=1.0).status_code == 200:
                    print(f"[Android] Main server ready at 127.0.0.1:{MAIN_SERVER_PORT}", flush=True)
                    return
            except Exception:
                pass
            time.sleep(0.5)

    import threading
    threading.Thread(target=_wait_ready, daemon=True).start()

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"[Android] Server loop failed: {e}", file=sys.stderr, flush=True)
        raise
