# -*- coding: utf-8 -*-
"""Memory Transfer Router

Cross-device memory migration: export the whole ``memory/`` tree (all
character memories + global state) as a single ``.tar.gz`` archive, and
import such an archive back (replacing the matching character memories).

Design notes:
- Vectors are embedded inside the per-character JSON files (facts.json,
  persona.json, ...), so no separate vector index needs to travel.
- ``time_indexed.db`` is snapshotted via the sqlite3 backup API so a live
  writer in the memory server cannot produce a torn archive entry.
- Import replaces character memory directories that exist in the archive;
  characters not present in the archive are left untouched. Global top-level
  files (speaker_trust.json, ...) are overwritten.
- Before overwriting a character's files the memory server is asked to
  release that character (drop its SQLite engine handles), and after the
  import the memory server is reloaded so the new memory is live.

URL convention: routes declared WITHOUT trailing slash. See
``main_routers/characters_router.py`` docstring for the rationale.
"""

import io
import logging
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from main_routers.characters_router.notify import (
    notify_memory_server_reload,
    release_memory_server_character,
)
from utils.config_manager import get_config_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["memory-transfer"])

_ARCHIVE_SUFFIX = ".tar.gz"
_EXCLUDED_DB_SIDE_FILES = ("time_indexed.db-wal", "time_indexed.db-shm")


def _memory_dir() -> Path:
    cm = get_config_manager()
    d = Path(cm.memory_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _snapshot_into(root: Path, dest: Path) -> None:
    """Copy the memory tree into ``dest`` with consistent DB snapshots.

    ``time_indexed.db`` is snapshotted through the SQLite backup API so the
    archive entry is a consistent point-in-time copy even while the memory
    server holds a live writer. WAL/SHM side files are dropped on purpose.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for entry in root.iterdir():
        if entry.is_dir():
            shutil.copytree(
                entry,
                dest / entry.name,
                dirs_exist_ok=True,
                copy_function=shutil.copy2,
                ignore=shutil.ignore_patterns(*_EXCLUDED_DB_SIDE_FILES),
            )
            db_src = entry / "time_indexed.db"
            db_dst = dest / entry.name / "time_indexed.db"
            if db_src.is_file():
                try:
                    uri_path = os.path.abspath(db_src).replace("\\", "/")
                    if os.name == "nt" and not uri_path.startswith("/"):
                        uri_path = "/" + uri_path
                    src = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
                    dst = sqlite3.connect(db_dst)
                    try:
                        with dst:
                            src.backup(dst)
                    finally:
                        dst.close()
                        src.close()
                except Exception:  # noqa: BLE001
                    logger.warning("memory transfer: sqlite backup failed for %s, falling back to copy", db_src)
                    shutil.copy2(db_src, db_dst)
        elif entry.is_file():
            shutil.copy2(entry, dest / entry.name)


@router.get("/export")
async def export_memory_backup():
    """Export the whole memory tree as a single ``.tar.gz`` download."""
    memory_dir = _memory_dir()
    tmp_root = Path(tempfile.mkdtemp(prefix="neko-mem-export-"))
    try:
        snap = tmp_root / "memory"
        _snapshot_into(memory_dir, snap)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for entry in sorted(snap.iterdir()):
                tar.add(entry, arcname=entry.name, recursive=True)
        buf.seek(0)
        filename = "neko-memory-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S") + _ARCHIVE_SUFFIX
        return StreamingResponse(
            buf,
            media_type="application/gzip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _extract_member_safe(tar: tarfile.TarFile, member: tarfile.TarInfo, dest: Path) -> None:
    """Extract a single archive member into ``dest`` without path escapes."""
    base = dest.resolve()
    target = (base / member.name).resolve()
    if target != base and not str(target).startswith(str(base) + os.sep):
        raise HTTPException(status_code=400, detail="invalid archive member path")
    if member.issym() or member.islnk():
        raise HTTPException(status_code=400, detail="symlinks are not allowed in memory archive")
    if member.isdir():
        target.mkdir(parents=True, exist_ok=True)
        return
    if not member.isfile():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    source = tar.extractfile(member)
    if source is None:
        return
    with target.open("wb") as out:
        shutil.copyfileobj(source, out)


@router.post("/import")
async def import_memory_backup(file: UploadFile = File(...)):
    """Import a memory backup archive, replacing matching character memories."""
    memory_dir = _memory_dir()
    tmp_root = Path(tempfile.mkdtemp(prefix="neko-mem-import-"))
    try:
        uploaded = tmp_root / ("upload" + _ARCHIVE_SUFFIX)
        with uploaded.open("wb") as out:
            shutil.copyfileobj(file.file, out)

        stage = tmp_root / "stage"
        stage.mkdir()
        try:
            with tarfile.open(uploaded, "r:gz") as tar:
                for member in tar.getmembers():
                    _extract_member_safe(tar, member, stage)
        except tarfile.TarError as exc:
            raise HTTPException(status_code=400, detail=f"invalid memory archive: {exc}") from exc

        character_dirs = sorted(d for d in stage.iterdir() if d.is_dir())
        global_files = sorted(f for f in stage.iterdir() if f.is_file())

        for character_dir in character_dirs:
            name = character_dir.name
            try:
                await release_memory_server_character(name, reason="memory import")
            except Exception:  # noqa: BLE001
                logger.warning("memory import: release_memory_server_character(%s) failed", name)
            target = memory_dir / name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(character_dir, target)

        for global_file in global_files:
            shutil.copy2(global_file, memory_dir / global_file.name)

        reloaded = await notify_memory_server_reload(reason="memory import")
        return JSONResponse(
            {
                "ok": True,
                "characters": [d.name for d in character_dirs],
                "global_files": [f.name for f in global_files],
                "memory_server_reloaded": reloaded,
            }
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
