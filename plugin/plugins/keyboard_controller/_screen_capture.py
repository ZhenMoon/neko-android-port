"""Screen capture + OCR for keyboard_controller.

Captures the target window (or fullscreen) as a PIL image and runs OCR via
the shared RapidOCR backend (``plugin/plugins/_shared/rapidocr``), so
non-vision LLM tools can "see" the screen as text.

Backends:
- capture: mss (multi-monitor) with a DPI-aware rect, fallback pyautogui/ImageGrab
- OCR: shared ``RapidOcrBackend`` (lazy; reuses galgame/study runtime when present)
"""

from __future__ import annotations

import base64
import ctypes
import importlib
import io
import threading
from pathlib import Path
from typing import Any, Optional

from PIL import Image

from ._win32_input import RECT, find_window_for_pid, is_windows

_CAPTURE_MAX_LONG_EDGE = 1920
_OCR_RESULT_MAX_CHARS = 2000

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

_ocr_backend: Any = None
_ocr_backend_lock = threading.Lock()


def _run_with_thread_dpi_awareness(fn):
    windll = getattr(ctypes, "windll", None)
    user32 = getattr(windll, "user32", None) if windll is not None else None
    set_context = (
        getattr(user32, "SetThreadDpiAwarenessContext", None)
        if user32 is not None
        else None
    )
    if not callable(set_context):
        return fn()
    try:
        set_context.restype = ctypes.c_void_p
        set_context.argtypes = [ctypes.c_void_p]
    except Exception:
        pass
    old_context = None
    try:
        old_context = set_context(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    except Exception:
        old_context = None
    try:
        return fn()
    finally:
        if old_context is not None:
            try:
                set_context(old_context)
            except Exception:
                pass


def _window_rect_dpi_aware(hwnd: int) -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32

    def _read() -> tuple[int, int, int, int]:
        rect = RECT()
        if not user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
            return (0, 0, 0, 0)
        return (
            int(rect.left),
            int(rect.top),
            int(rect.right),
            int(rect.bottom),
        )

    return _run_with_thread_dpi_awareness(_read)


def capture_fullscreen() -> Image.Image:
    """Capture the virtual screen as a PIL RGB image."""
    def _grab() -> Image.Image:
        try:
            import mss

            with mss.mss() as sct:
                monitor = sct.monitors[0]  # virtual screen union
                shot = sct.grab(monitor)
                return Image.frombytes("RGB", shot.size, shot.rgb)
        except Exception:
            from PIL import ImageGrab

            return ImageGrab.grab()

    return _normalize_image(_run_with_thread_dpi_awareness(_grab))


def capture_window(window: dict[str, Any]) -> Image.Image:
    """Capture a target window (``find_window_for_pid`` result) as PIL RGB."""
    hwnd = int(window.get("hwnd") or 0)
    if hwnd <= 0:
        raise RuntimeError("target window has no hwnd")
    left, top, right, bottom = _window_rect_dpi_aware(hwnd)
    if right <= left or bottom <= top:
        raise RuntimeError(f"target window has invalid rect ({left},{top},{right},{bottom})")

    def _grab() -> Image.Image:
        try:
            import mss

            with mss.mss() as sct:
                monitor = {
                    "left": int(left),
                    "top": int(top),
                    "width": int(right - left),
                    "height": int(bottom - top),
                }
                shot = sct.grab(monitor)
                return Image.frombytes("RGB", shot.size, shot.rgb)
        except Exception:
            from PIL import ImageGrab

            return ImageGrab.grab(bbox=(int(left), int(top), int(right), int(bottom)))

    # The DPI-aware rect was read under per-monitor V2 awareness; the fallback
    # ImageGrab path must run under the same awareness, otherwise on high-DPI
    # displays it captures the wrong region (offset/scaled).
    return _normalize_image(_run_with_thread_dpi_awareness(_grab))


def _normalize_image(image: Image.Image) -> Image.Image:
    frame = image.convert("RGB") if hasattr(image, "convert") else image
    width, height = frame.size
    if width <= 0 or height <= 0:
        raise RuntimeError(f"invalid capture dimensions {width}x{height}")
    scale = min(1.0, float(_CAPTURE_MAX_LONG_EDGE) / float(max(width, height)))
    if scale < 1.0:
        try:
            resampling = Image.Resampling.LANCZOS
        except AttributeError:  # Pillow < 9.1
            resampling = Image.LANCZOS
        frame = frame.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            resampling,
        )
    return frame


def encode_jpeg_base64(image: Image.Image, *, quality: int = 72) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    raw = buffer.getvalue()
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def save_png(image: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return path


def ocr_is_available() -> bool:
    try:
        return _resolve_ocr_backend().is_available()
    except Exception:
        return False


def ocr_image(image: Image.Image) -> tuple[str, str]:
    """Run OCR on a PIL image. Returns (text, status)."""
    backend = _resolve_ocr_backend()
    if backend is None or not backend.is_available():
        return "", "unavailable"
    try:
        text = backend.extract_text(image)
    except Exception:
        return "", "ocr_failed"
    text = str(text or "").strip()
    if not text:
        return "", "empty"
    if len(text) > _OCR_RESULT_MAX_CHARS:
        text = text[:_OCR_RESULT_MAX_CHARS] + "\n…[truncated]"
    return text, "ok"


def ocr_image_with_boxes(
    image: Image.Image,
    *,
    max_boxes: int = 0,
    query: str = "",
) -> tuple[str, list[dict[str, Any]], str]:
    """Run OCR and return (text, boxes, status).

    Each box is ``{"text", "left", "top", "right", "bottom", "score"}`` in image
    pixel coordinates. When ``query`` is non-empty only matching boxes (case-
    insensitive substring) are returned, which keeps the payload tiny.
    """
    backend = _resolve_ocr_backend()
    if backend is None or not backend.is_available():
        return "", [], "unavailable"
    try:
        text, boxes = backend.extract_text_with_boxes(image)
    except Exception:
        return "", [], "ocr_failed"
    if not boxes:
        return "", [], "empty"

    query = str(query or "").strip().lower()
    result: list[dict[str, Any]] = []
    for box in boxes:
        box_text = str(getattr(box, "text", "") or "")
        if query and query not in box_text.lower():
            continue
        result.append({
            "text": box_text,
            "left": int(round(getattr(box, "left", 0) or 0)),
            "top": int(round(getattr(box, "top", 0) or 0)),
            "right": int(round(getattr(box, "right", 0) or 0)),
            "bottom": int(round(getattr(box, "bottom", 0) or 0)),
            "score": round(float(getattr(box, "score", 0) or 0), 3),
        })
        if max_boxes and len(result) >= int(max_boxes):
            break

    if not result:
        return "", [], "no_match"
    joined = "\n".join(b["text"] for b in result)
    return joined, result, "ok"


def _resolve_ocr_backend() -> Any:
    global _ocr_backend
    with _ocr_backend_lock:
        if _ocr_backend is not None:
            return _ocr_backend
        if not is_windows():
            return None
        from plugin.plugins._shared.rapidocr.ocr_backends import RapidOcrBackend

        _ocr_backend = RapidOcrBackend(
            install_target_dir_raw="",
            engine_type="onnxruntime",
            lang_type="ch",
            model_type="mobile",
            ocr_version="PP-OCRv4",
            plugin_id="keyboard_controller",
        )
        return _ocr_backend


def close_ocr_backend() -> None:
    global _ocr_backend
    with _ocr_backend_lock:
        backend = _ocr_backend
        _ocr_backend = None
    if backend is None:
        return
    close = getattr(backend, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def describe_capture() -> dict[str, Any]:
    """Status payload for the panel and status entry."""
    mss_ok = importlib.util.find_spec("mss") is not None
    return {
        "windows_supported": is_windows(),
        "ocr_available": ocr_is_available(),
        "mss_available": mss_ok,
        "max_ocr_chars": _OCR_RESULT_MAX_CHARS,
    }


def target_window_for_capture(pid: int) -> Optional[dict[str, Any]]:
    if pid <= 0:
        return None
    return find_window_for_pid(pid)


__all__ = [
    "capture_fullscreen",
    "capture_window",
    "close_ocr_backend",
    "describe_capture",
    "encode_jpeg_base64",
    "ocr_image",
    "ocr_is_available",
    "save_png",
    "target_window_for_capture",
]
