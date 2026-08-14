"""Windows input primitives for keyboard_controller.

Self-contained implementation (window enumeration, process-name lookup,
foreground focusing, and SendInput keyboard/mouse injection) so the plugin
does not depend on galgame_plugin internals. Borrows the approach used by
galgame's local input actuator but is generic and plugin-local.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from typing import Any, Optional

from ._key_map import (
    EXTENDED_KEYS,
)

logger = logging.getLogger(__name__)

# --- win32 constants -----------------------------------------------------------
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MAPVK_VK_TO_VSC = 0
SW_RESTORE = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenElevation = 20

INPUT_SAFETY_DENY_MARKERS = (
    "anti-cheat",
    "anticheat",
    "easy anti-cheat",
    "easyanticheat",
    "battleye",
    "battl-eye",
    "vanguard",
    "ricochet",
    "xigncode",
    "gameguard",
    "faceit",
    "equ8",
    "ace anti",
)

# --- ctypes structs -------------------------------------------------------------
class RECT(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    )


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    )


class INPUT_UNION(ctypes.Union):
    _fields_ = (
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
    )


class INPUT(ctypes.Structure):
    _fields_ = (
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION),
    )


class TOKEN_ELEVATION(ctypes.Structure):
    _fields_ = (("TokenIsElevated", wintypes.DWORD),)


_WAIT_EVENT = threading.Event()
_LOCK = threading.Lock()


def is_windows() -> bool:
    return sys.platform == "win32"


def _wait_seconds(delay: float) -> None:
    _WAIT_EVENT.wait(max(0.0, float(delay or 0.0)))


def _warn(message: str, exc: Exception) -> None:
    logger.warning("%s: %s", message, exc)


def _matching_input_safety_deny_marker(*values: str) -> str:
    text = "\n".join(str(value or "") for value in values).lower()
    for marker in INPUT_SAFETY_DENY_MARKERS:
        if marker in text:
            return marker
    return ""


# --- window enumeration -----------------------------------------------------------
def _window_text(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    try:
        length = int(user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return str(buffer.value or "")
    except Exception as exc:
        _warn("window text lookup failed", exc)
        return ""


def _window_pid(hwnd: int) -> int:
    user32 = ctypes.windll.user32
    try:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value or 0)
    except Exception as exc:
        _warn("window pid lookup failed", exc)
        return 0


def _process_name_for_pid(pid: int) -> str:
    if pid <= 0:
        return ""
    kernel32 = ctypes.windll.kernel32
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not process:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            path = str(buffer.value or "")
            name = path.replace("\\", "/").rsplit("/", 1)[-1]
            return name
        return ""
    except Exception as exc:
        _warn("process name lookup failed", exc)
        return ""
    finally:
        kernel32.CloseHandle(process)


def enumerate_windows(min_width: int = 120, min_height: int = 80) -> list[dict[str, Any]]:
    """Enumerate visible top-level windows and describe them."""
    if not is_windows():
        return []
    user32 = ctypes.windll.user32
    results: list[dict[str, Any]] = []
    seen_pids: set[int] = set()

    enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.IsIconic(hwnd):
            return True
        title = _window_text(hwnd)
        pid = _window_pid(hwnd)
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width < min_width or height < min_height:
            return True
        # Prefer the biggest window per pid for a given title/process pair.
        key = (pid, title)
        if key in seen_pids:
            return True
        seen_pids.add(key)
        results.append({
            "hwnd": int(hwnd),
            "pid": pid,
            "title": title,
            "process_name": _process_name_for_pid(pid),
            "rect": {
                "left": int(rect.left),
                "top": int(rect.top),
                "right": int(rect.right),
                "bottom": int(rect.bottom),
            },
        })
        return True

    try:
        user32.EnumWindows(enum_proc_type(_callback), 0)
    except Exception as exc:
        _warn("EnumWindows failed", exc)
        return []
    return results


def find_window_for_pid(pid: int) -> Optional[dict[str, Any]]:
    if not is_windows() or pid <= 0:
        return None
    user32 = ctypes.windll.user32
    best: Optional[dict[str, Any]] = None
    best_area = 0

    enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd: int, _lparam: int) -> bool:
        nonlocal best, best_area
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.IsIconic(hwnd):
            return True
        if _window_pid(hwnd) != int(pid):
            return True
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width < 120 or height < 80:
            return True
        area = width * height
        if area > best_area:
            best_area = area
            best = {
                "hwnd": int(hwnd),
                "pid": int(pid),
                "title": _window_text(hwnd),
                "process_name": _process_name_for_pid(int(pid)),
                "rect": {
                    "left": int(rect.left),
                    "top": int(rect.top),
                    "right": int(rect.right),
                    "bottom": int(rect.bottom),
                },
            }
        return True

    try:
        user32.EnumWindows(enum_proc_type(_callback), 0)
    except Exception as exc:
        _warn("EnumWindows for pid failed", exc)
        return None
    return best


def foreground_hwnd() -> int:
    if not is_windows():
        return 0
    try:
        return int(ctypes.windll.user32.GetForegroundWindow())
    except Exception as exc:
        _warn("GetForegroundWindow failed", exc)
        return 0


def foreground_window() -> dict[str, Any]:
    """Describe the current foreground window (hwnd, pid, title, process_name)."""
    hwnd = foreground_hwnd()
    if hwnd <= 0:
        return {"hwnd": 0, "pid": 0, "title": "", "process_name": "", "rect": {}}
    pid = _window_pid(hwnd)
    return {
        "hwnd": hwnd,
        "pid": pid,
        "title": _window_text(hwnd),
        "process_name": _process_name_for_pid(pid),
        "rect": {},
    }


def foreground_matches(hwnd: int, pid: int) -> bool:
    fg = foreground_hwnd()
    if fg <= 0:
        return False
    return _foreground_matches_target(fg, hwnd, pid)


# --- window coordinate helpers -------------------------------------------------------
def window_rect(hwnd: int) -> Optional[dict[str, int]]:
    """Return the window rect in screen coordinates: left/top/right/bottom."""
    if not is_windows() or not hwnd:
        return None
    user32 = ctypes.windll.user32
    rect = RECT()
    try:
        if not user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
            return None
    except Exception as exc:
        _warn("GetWindowRect failed", exc)
        return None
    return {
        "left": int(rect.left),
        "top": int(rect.top),
        "right": int(rect.right),
        "bottom": int(rect.bottom),
    }


def window_client_rect(hwnd: int) -> Optional[dict[str, int]]:
    """Return the client-area rect in screen coordinates.

    ``left/top`` is the client origin in screen space (already offset by the
    window frame), ``width/height`` the client size. This is what in-window
    coordinates (0,0 = client top-left) should be added to.
    """
    if not is_windows() or not hwnd:
        return None
    user32 = ctypes.windll.user32
    client = RECT()
    try:
        if not user32.GetClientRect(int(hwnd), ctypes.byref(client)):
            return None
        origin = wintypes.POINT()
        if not user32.ClientToScreen(int(hwnd), ctypes.byref(origin)):
            return None
    except Exception as exc:
        _warn("client rect lookup failed", exc)
        return None
    return {
        "left": int(origin.x),
        "top": int(origin.y),
        "right": int(origin.x + (client.right - client.left)),
        "bottom": int(origin.y + (client.bottom - client.top)),
        "width": int(client.right - client.left),
        "height": int(client.bottom - client.top),
    }


def client_to_screen(hwnd: int, x: int, y: int) -> Optional[tuple[int, int]]:
    """Convert an in-window client coordinate to screen absolute."""
    rect = window_client_rect(hwnd)
    if rect is None:
        return None
    return int(rect["left"]) + int(x), int(rect["top"]) + int(y)


# --- foreground focusing ------------------------------------------------------------
def _root_window_handle(hwnd: int) -> int:
    if not hwnd:
        return 0
    try:
        root = int(ctypes.windll.user32.GetAncestor(int(hwnd), 2))
        return root or int(hwnd)
    except Exception as exc:
        _warn("root window lookup failed", exc)
        return int(hwnd)


def _foreground_matches_target(foreground_hwnd: int, target_hwnd: int, target_pid: int) -> bool:
    if not foreground_hwnd or not target_hwnd:
        return False
    if int(foreground_hwnd) == int(target_hwnd):
        return True
    fg_root = _root_window_handle(int(foreground_hwnd))
    target_root = _root_window_handle(int(target_hwnd))
    if fg_root and target_root and fg_root == target_root:
        return True
    fg_pid = _window_pid(int(foreground_hwnd))
    return bool(fg_pid and target_pid and fg_pid == int(target_pid))


def focus_window(hwnd: int, *, attempts: int = 1, retry_delay: float = 0.25) -> bool:
    """Bring a window to the foreground and verify it got focus.

    Retries ``attempts`` times with ``retry_delay`` between tries to handle
    transient foreground-lock failures (Windows foreground stealing rules).
    """
    if not is_windows():
        return False
    hwnd = int(hwnd or 0)
    attempts = max(1, int(attempts or 1))
    for _ in range(attempts):
        if _focus_window_once(hwnd):
            return True
        if attempts > 1:
            _wait_seconds(retry_delay)
    return False


def _focus_window_once(hwnd: int) -> bool:
    user32 = ctypes.windll.user32
    target_pid = _window_pid(hwnd)
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    try:
        user32.AllowSetForegroundWindow(-1)
    except Exception as exc:
        _warn("AllowSetForegroundWindow failed", exc)
    foreground = user32.GetForegroundWindow()
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    attached_foreground = False
    attached_target = False
    try:
        if foreground_thread and foreground_thread != current_thread:
            attached_foreground = bool(user32.AttachThreadInput(current_thread, foreground_thread, True))
        if target_thread and target_thread != current_thread:
            attached_target = bool(user32.AttachThreadInput(current_thread, target_thread, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
    except Exception as exc:
        _warn("SetForegroundWindow sequence failed", exc)
    finally:
        if attached_target:
            user32.AttachThreadInput(current_thread, target_thread, False)
        if attached_foreground:
            user32.AttachThreadInput(current_thread, foreground_thread, False)
    _wait_seconds(0.12)
    try:
        fg = int(user32.GetForegroundWindow())
        return _foreground_matches_target(fg, int(hwnd), int(target_pid))
    except Exception as exc:
        _warn("foreground verification failed", exc)
        return False


# --- elevation & safety ---------------------------------------------------------------
def _is_current_process_elevated() -> Optional[bool]:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception as exc:
        _warn("current elevation lookup failed", exc)
        return None


def _is_process_elevated(pid: int) -> Optional[bool]:
    if pid <= 0:
        return None
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32
    process = None
    token = wintypes.HANDLE()
    try:
        process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not process:
            return None
        if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
            return None
        elevation = TOKEN_ELEVATION()
        returned = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token, TokenElevation, ctypes.byref(elevation),
            ctypes.sizeof(elevation), ctypes.byref(returned),
        ):
            return None
        return bool(elevation.TokenIsElevated)
    except Exception as exc:
        _warn("target elevation lookup failed", exc)
        return None
    finally:
        try:
            if token:
                kernel32.CloseHandle(token)
        except Exception:
            pass
        try:
            if process:
                kernel32.CloseHandle(process)
        except Exception:
            pass


def input_safety_block_reason(*, pid: int, hwnd: int, process_name: str, window_title: str) -> str:
    """Return a human-readable reason the input should be blocked, or ''."""
    if pid <= 0 or not hwnd:
        return "no valid target window"
    if not process_name:
        return "missing target process name"
    deny_marker = _matching_input_safety_deny_marker(process_name, window_title)
    if deny_marker:
        return f"deny marker {deny_marker} (anti-cheat target)"
    current_elevated = _is_current_process_elevated()
    target_elevated = _is_process_elevated(pid)
    if target_elevated is True and current_elevated is False:
        return "target process is elevated"
    return ""


# --- SendInput keyboard ---------------------------------------------------------------
def _send_inputs(inputs: list[INPUT]) -> int:
    user32 = ctypes.windll.user32
    if not inputs:
        return 0
    array = (INPUT * len(inputs))(*inputs)
    return int(user32.SendInput(len(inputs), ctypes.byref(array), ctypes.sizeof(INPUT)))


def _scan_for_vk(vk: int) -> int:
    try:
        return int(ctypes.windll.user32.MapVirtualKeyW(int(vk), MAPVK_VK_TO_VSC))
    except Exception:
        return 0


def _keyboard_input(vk: int, scan: int, flags: int) -> INPUT:
    return INPUT(
        INPUT_KEYBOARD,
        INPUT_UNION(
            ki=KEYBDINPUT(
                int(vk),
                int(scan),
                flags,
                0,
                None,
            )
        ),
    )


def key_down(vk: int) -> None:
    if not is_windows():
        return
    scan = _scan_for_vk(vk)
    flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_KEYS else 0
    if scan:
        _send_inputs([_keyboard_input(vk, scan, KEYEVENTF_SCANCODE | flags)])
    else:
        _send_inputs([_keyboard_input(vk, 0, 0)])


def key_up(vk: int) -> None:
    if not is_windows():
        return
    scan = _scan_for_vk(vk)
    flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_KEYS else 0
    if scan:
        _send_inputs([_keyboard_input(vk, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP | flags)])
    else:
        _send_inputs([_keyboard_input(vk, 0, KEYEVENTF_KEYUP)])


def tap_key(vk: int, *, count: int = 1, delay: float = 0.05) -> None:
    for _ in range(max(1, int(count))):
        key_down(vk)
        _wait_seconds(delay)
        key_up(vk)
        _wait_seconds(delay)


def press_combo(modifiers: list[int], main_vk: int, *, delay: float = 0.05) -> None:
    for mod in modifiers:
        key_down(mod)
        _wait_seconds(delay * 0.5)
    tap_key(main_vk, count=1, delay=delay)
    for mod in reversed(modifiers):
        key_up(mod)
        _wait_seconds(delay * 0.5)


def _unicode_units(text: str) -> list[int]:
    """Encode text as UTF-16 code units for KEYEVENTF_UNICODE injection.

    Characters outside the BMP (emoji, some CJK extensions) are one Python
    codepoint but two UTF-16 code units (surrogate pair); each must be sent
    as its own KEYBDINPUT wScan, otherwise ctypes truncates the 32-bit value
    into a 16-bit WORD and the wrong character is typed.
    """
    raw = str(text or "").encode("utf-16-le", errors="surrogatepass")
    return [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]


def type_text(
    text: str,
    *,
    char_delay: float = 0.01,
    use_clipboard: bool = True,
    clipboard_threshold: int = 80,
    retries: int = 2,
) -> bool:
    """Type text into the focused window.

    Small text is injected via SendInput with KEYEVENTF_UNICODE (supports
    Chinese, emoji, layout-independent). Long text (>= ``clipboard_threshold``
    chars) is pasted through the clipboard (Ctrl+V) to be fast and reliable.

    Returns True if injection was accepted (or clipboard path taken).
    """
    if not is_windows():
        return False
    text = str(text or "")
    if not text:
        return True

    if use_clipboard and len(text) >= clipboard_threshold:
        _paste_clipboard(text, char_delay=char_delay)
        return True

    inputs: list[INPUT] = []
    for unit in _unicode_units(text):
        inputs.append(INPUT(
            INPUT_KEYBOARD,
            INPUT_UNION(ki=KEYBDINPUT(0, unit, KEYEVENTF_UNICODE, 0, None)),
        ))
        inputs.append(INPUT(
            INPUT_KEYBOARD,
            INPUT_UNION(ki=KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)),
        ))

    for _ in range(max(1, int(retries))):
        sent = 0
        for i in range(0, len(inputs), 16):
            batch = inputs[i:i + 16]
            accepted = _send_inputs(batch)
            sent += accepted
            if accepted < len(batch):
                break
            _wait_seconds(char_delay)
        if sent >= len(inputs):
            return True
        _wait_seconds(0.05)
    return False


def _paste_clipboard(text: str, *, char_delay: float, restore: bool = True) -> bool:
    """Put ``text`` on the Windows clipboard, paste it with Ctrl+V, then restore.

    When ``restore`` is True the previous clipboard text is saved first and
    put back after the paste so the user's clipboard is not clobbered.
    """
    text = str(text or "")
    if not text:
        return False
    previous = _get_clipboard_text() if restore else ""
    if not _set_clipboard_text(text):
        return False
    from ._key_map import parse_combo

    modifiers, main_vk = parse_combo("ctrl+v")
    try:
        press_combo(modifiers, main_vk, delay=max(char_delay, 0.04))
        ok = True
    except Exception as exc:
        _warn("clipboard paste failed", exc)
        ok = False
    if restore and previous is not None:
        _wait_seconds(0.1)
        _set_clipboard_text(previous)
    return ok


def _get_clipboard_text() -> str | None:
    """Read the current clipboard text, or None if unavailable/empty."""
    if not is_windows():
        return None
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    try:
        kernel32.GlobalSize.restype = ctypes.c_size_t
        kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        if not user32.OpenClipboard(None):
            return None
        try:
            handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
            if not handle:
                return None
            size = int(kernel32.GlobalSize(handle))
            if size <= 0:
                return None
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return None
            try:
                # Cap the read so a non-NUL-terminated clipboard buffer cannot
                # walk past the allocated memory.
                max_chars = min(size // 2, 1024 * 1024)
                return ctypes.wstring_at(ptr, max_chars)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except Exception as exc:
        _warn("clipboard read failed", exc)
        return None


def _set_clipboard_text(text: str) -> bool:
    """Open the Windows clipboard and set UTF-16 text. Returns True on success."""
    if not is_windows():
        return False
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    # Set explicit signatures so 64-bit pointer values are not truncated.
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    try:
        if not user32.OpenClipboard(None):
            return False
        try:
            if not user32.EmptyClipboard():
                return False
            buffer = ctypes.create_unicode_buffer(text)
            size = (len(text) + 1) * 2
            h_mem = kernel32.GlobalAlloc(0x0042, size)  # GHND | GMEM_MOVEABLE
            if not h_mem:
                return False
            ptr = kernel32.GlobalLock(h_mem)
            if not ptr:
                kernel32.GlobalFree(h_mem)
                return False
            try:
                ctypes.memmove(ptr, buffer, size)
            finally:
                kernel32.GlobalUnlock(h_mem)
            if not user32.SetClipboardData(13, h_mem):  # CF_UNICODETEXT
                kernel32.GlobalFree(h_mem)
                return False
        finally:
            user32.CloseClipboard()
        return True
    except Exception as exc:
        _warn("clipboard set failed", exc)
        return False


# --- mouse -------------------------------------------------------------------------------
def _virtual_screen() -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    x = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    y = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
    w = max(user32.GetSystemMetrics(78) - 1, 1)  # SM_CXVIRTUALSCREEN
    h = max(user32.GetSystemMetrics(79) - 1, 1)  # SM_CYVIRTUALSCREEN
    return int(x), int(y), int(w), int(h)


def mouse_move(x: int, y: int) -> None:
    if not is_windows():
        return
    vx, vy, vw, vh = _virtual_screen()
    abs_x = int((int(x) - vx) * 65535 // vw)
    abs_y = int((int(y) - vy) * 65535 // vh)
    _send_inputs([INPUT(
        INPUT_MOUSE,
        INPUT_UNION(mi=MOUSEINPUT(abs_x, abs_y, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 0, None)),
    )])
    _wait_seconds(0.03)


def mouse_drag(x1: int, y1: int, x2: int, y2: int, *, button: str = "left", steps: int = 20) -> None:
    """Drag the mouse from (x1,y1) to (x2,y2) with button held down.

    Uses absolute screen coordinates. ``steps`` interpolates movement so
    in-app drag gestures (paint, sliders, file move) are recognized.
    """
    if not is_windows():
        return
    if int(steps) < 1:
        steps = 1
    mouse_move(x1, y1)
    _wait_seconds(0.05)
    _mouse_button_down(button)
    try:
        for i in range(1, int(steps) + 1):
            t = i / int(steps)
            ix = int(x1 + (x2 - x1) * t)
            iy = int(y1 + (y2 - y1) * t)
            mouse_move(ix, iy)
            _wait_seconds(0.008)
    finally:
        _mouse_button_up(button)
    _wait_seconds(0.04)


def _mouse_button_down(button: str) -> None:
    down_flag = {
        "left": MOUSEEVENTF_LEFTDOWN,
        "right": MOUSEEVENTF_RIGHTDOWN,
        "middle": MOUSEEVENTF_MIDDLEDOWN,
    }.get(str(button).lower(), MOUSEEVENTF_LEFTDOWN)
    _send_inputs([INPUT(INPUT_MOUSE, INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, down_flag, 0, None)))])


def _mouse_button_up(button: str) -> None:
    up_flag = {
        "left": MOUSEEVENTF_LEFTUP,
        "right": MOUSEEVENTF_RIGHTUP,
        "middle": MOUSEEVENTF_MIDDLEUP,
    }.get(str(button).lower(), MOUSEEVENTF_LEFTUP)
    _send_inputs([INPUT(INPUT_MOUSE, INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, up_flag, 0, None)))])


def mouse_wheel(x: int, y: int, *, delta: int = 120) -> None:
    """Scroll the mouse wheel at (x, y). Positive ``delta`` scrolls up.

    One notch is typically 120 units; use multiples (240, 360...) for faster
    scrolling, or negative for down.
    """
    if not is_windows():
        return
    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x), int(y))
    _wait_seconds(0.03)
    data = max(-32768, min(32767, int(delta or 0)))
    if not data:
        return
    _send_inputs([INPUT(
        INPUT_MOUSE,
        INPUT_UNION(mi=MOUSEINPUT(0, 0, data, MOUSEEVENTF_WHEEL, 0, None)),
    )])
    _wait_seconds(0.03)


def mouse_click(x: int, y: int, *, button: str = "left", clicks: int = 1, click_interval: float = 0.08) -> None:
    if not is_windows():
        return
    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x), int(y))
    _wait_seconds(0.04)
    vx, vy, vw, vh = _virtual_screen()
    abs_x = int((int(x) - vx) * 65535 // vw)
    abs_y = int((int(y) - vy) * 65535 // vh)
    down_flag = {
        "left": MOUSEEVENTF_LEFTDOWN,
        "right": MOUSEEVENTF_RIGHTDOWN,
        "middle": MOUSEEVENTF_MIDDLEDOWN,
    }.get(str(button).lower(), MOUSEEVENTF_LEFTDOWN)
    up_flag = {
        "left": MOUSEEVENTF_LEFTUP,
        "right": MOUSEEVENTF_RIGHTUP,
        "middle": MOUSEEVENTF_MIDDLEUP,
    }.get(str(button).lower(), MOUSEEVENTF_LEFTUP)
    clicks = max(1, int(clicks or 1))
    for _ in range(clicks):
        _send_inputs([
            INPUT(INPUT_MOUSE, INPUT_UNION(mi=MOUSEINPUT(abs_x, abs_y, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 0, None))),
            INPUT(INPUT_MOUSE, INPUT_UNION(mi=MOUSEINPUT(abs_x, abs_y, 0, down_flag, 0, None))),
            INPUT(INPUT_MOUSE, INPUT_UNION(mi=MOUSEINPUT(abs_x, abs_y, 0, up_flag, 0, None))),
        ])
        if clicks > 1:
            _wait_seconds(click_interval)
    _wait_seconds(0.05)


def press_key_combination(spec: str, *, count: int = 1, delay: float = 0.05) -> None:
    """Convenience: parse and send a combo string like ``"ctrl+c"``."""
    from ._key_map import parse_combo

    modifiers, main_vk = parse_combo(spec)
    for _ in range(max(1, int(count))):
        press_combo(modifiers, main_vk, delay=delay)
        if count > 1:
            _wait_seconds(delay)


def hold_key(spec: str, *, seconds: float = 1.0) -> None:
    """Press and hold a key/combo for ``seconds`` then release."""
    from ._key_map import parse_combo

    modifiers, main_vk = parse_combo(spec)
    for mod in modifiers:
        key_down(mod)
    key_down(main_vk)
    _wait_seconds(max(0.05, float(seconds or 0.05)))
    key_up(main_vk)
    for mod in reversed(modifiers):
        key_up(mod)


__all__ = [
    "INPUT",
    "INPUT_UNION",
    "KEYBDINPUT",
    "MOUSEINPUT",
    "RECT",
    "enumerate_windows",
    "find_window_for_pid",
    "focus_window",
    "foreground_hwnd",
    "foreground_matches",
    "foreground_window",
    "hold_key",
    "input_safety_block_reason",
    "is_windows",
    "key_down",
    "key_up",
    "mouse_click",
    "mouse_drag",
    "mouse_move",
    "mouse_wheel",
    "press_combo",
    "press_key_combination",
    "tap_key",
    "type_text",
    "client_to_screen",
    "window_client_rect",
    "window_rect",
]
