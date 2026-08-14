"""Workspace file operations for keyboard_controller.

Gives a text-only LLM the ability to read, write and list files inside a
bounded workspace root, so it can "vibe-code": inspect source, edit files,
then run commands (keyboard_run_command) and iterate on errors.

Safety:
- All paths are resolved and checked against ``root``; anything outside is
  rejected (prevents path traversal via ``..``, symlinks, absolute paths).
- Read size and write size are bounded.
"""

from __future__ import annotations

import os
from typing import Any, Optional

DEFAULT_MAX_READ_BYTES = 64 * 1024
DEFAULT_MAX_WRITE_BYTES = 256 * 1024
DEFAULT_MAX_LIST_ENTRIES = 500

_ENCODINGS = ("utf-8", "gbk", "utf-16")


def _is_within(root: str, path: str) -> bool:
    root_real = os.path.realpath(root)
    path_real = os.path.realpath(path)
    try:
        return os.path.commonpath([root_real, path_real]) == root_real
    except Exception:
        return False


def resolve_path(root: str, rel_or_abs: str) -> Optional[str]:
    """Resolve a user-supplied path inside ``root``.

    Accepts either a path relative to ``root`` or an absolute path that lives
    under ``root``. Returns the real, in-root absolute path, or None.
    """
    root = os.path.abspath(os.path.expanduser(str(root or "")))
    if not os.path.isdir(root):
        return None
    raw = str(rel_or_abs or "").strip().strip('"').strip("'")
    if not raw:
        return None
    candidate = os.path.abspath(os.path.join(root, raw))
    if not _is_within(root, candidate):
        return None
    return os.path.realpath(candidate)


def list_dir(root: str, path: str = "", max_entries: int = DEFAULT_MAX_LIST_ENTRIES) -> dict[str, Any]:
    """List a directory inside ``root``."""
    target = resolve_path(root, path or ".")
    if target is None:
        return {"ok": False, "error": "路径无效或不在工作区内"}
    if not os.path.isdir(target):
        return {"ok": False, "error": "不是目录"}
    entries: list[dict[str, Any]] = []
    total = 0
    try:
        names = sorted(os.listdir(target))
        for name in names:
            full = os.path.join(target, name)
            try:
                is_dir = os.path.isdir(full)
                size = 0 if is_dir else os.path.getsize(full)
            except OSError:
                is_dir, size = False, 0
            entries.append({"name": name, "type": "dir" if is_dir else "file", "size": size})
            total += 1
            if len(entries) >= max_entries:
                break
    except OSError as exc:
        return {"ok": False, "error": f"无法读取目录：{exc}"}
    rel = os.path.relpath(target, os.path.realpath(root)) if target != os.path.realpath(root) else ""
    return {
        "ok": True,
        "path": rel or ".",
        "abs_path": target,
        "is_root": rel == "",
        "total": total,
        "truncated": total > len(entries),
        "entries": entries,
    }


def _decode_bytes(raw: bytes) -> tuple[str, bool]:
    """Decode ``raw`` as text. Returns (text, is_binary)."""
    if not raw:
        return "", False
    # NUL bytes or a high ratio of control bytes => almost certainly binary.
    if b"\x00" in raw:
        return raw.decode("utf-8", errors="replace"), True
    text = ""
    for enc in _ENCODINGS:
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, ValueError):
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    control = sum(1 for b in raw if b < 9 or 13 < b < 32)
    is_binary = len(raw) > 0 and (control * 100 // len(raw)) > 30
    return text, is_binary


def read_file(
    root: str,
    path: str,
    *,
    max_bytes: int = DEFAULT_MAX_READ_BYTES,
    start_line: int = 1,
    line_count: int = 0,
) -> dict[str, Any]:
    """Read a text file inside ``root``.

    Supports a line window via ``start_line``/``line_count`` so large files can
    be read in slices. Output is bounded by ``max_bytes``. Binary files are
    flagged via ``is_binary`` so the caller does not mistake garbage for text.
    """
    target = resolve_path(root, path)
    if target is None:
        return {"ok": False, "error": "路径无效或不在工作区内"}
    if not os.path.isfile(target):
        return {"ok": False, "error": "不是文件"}
    try:
        size = os.path.getsize(target)
    except OSError as exc:
        return {"ok": False, "error": f"无法读取文件：{exc}"}
    if size == 0:
        return {"ok": True, "path": path, "abs_path": target, "size": 0, "content": ""}

    try:
        with open(target, "rb") as fh:
            raw = fh.read(min(size, max_bytes))
    except OSError as exc:
        return {"ok": False, "error": f"无法读取文件：{exc}"}

    text, is_binary = _decode_bytes(raw)
    truncated_bytes = size > len(raw)

    start_line = max(1, int(start_line or 1))
    if start_line > 1 or line_count:
        lines = text.splitlines()
        end = len(lines) if not line_count else min(len(lines), start_line + int(line_count) - 1)
        window = lines[start_line - 1 : end]
        content = "\n".join(window)
        truncated_lines = end < len(lines)
        line_info = {"start_line": start_line, "end_line": end, "total_lines": len(lines)}
    else:
        content = text
        truncated_lines = False
        line_info = {"total_lines": text.count("\n") + 1}

    return {
        "ok": True,
        "path": path,
        "abs_path": target,
        "size": size,
        "is_binary": is_binary,
        "truncated_bytes": truncated_bytes,
        "truncated_lines": truncated_lines,
        "line_info": line_info,
        "content": content,
    }


def write_file(
    root: str,
    path: str,
    content: str,
    *,
    append: bool = False,
    max_bytes: int = DEFAULT_MAX_WRITE_BYTES,
) -> dict[str, Any]:
    """Write (or append) text to a file inside ``root``."""
    target = resolve_path(root, path)
    if target is None:
        return {"ok": False, "error": "路径无效或不在工作区内"}
    content = str(content or "")
    if len(content.encode("utf-8")) > max_bytes:
        return {"ok": False, "error": f"内容过大（上限 {max_bytes} 字节）"}

    parent = os.path.dirname(target)
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "error": f"无法创建目录：{exc}"}

    existed = os.path.isfile(target)
    try:
        mode = "a" if append else "w"
        with open(target, mode, encoding="utf-8", newline="") as fh:
            fh.write(content)
    except OSError as exc:
        return {"ok": False, "error": f"无法写入文件：{exc}"}

    return {
        "ok": True,
        "path": path,
        "abs_path": target,
        "appended": bool(append),
        "created": not existed,
        "bytes_written": len(content.encode("utf-8")),
        "message": f"{'追加' if append else '写入'}完成：{path}",
    }


__all__ = [
    "DEFAULT_MAX_LIST_ENTRIES",
    "DEFAULT_MAX_READ_BYTES",
    "DEFAULT_MAX_WRITE_BYTES",
    "list_dir",
    "read_file",
    "resolve_path",
    "write_file",
]
