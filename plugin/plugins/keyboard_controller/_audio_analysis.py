"""Host audio capture + spectral analysis for keyboard_controller.

Captures what the machine is currently playing (WASAPI loopback) and runs a
numpy FFT to produce spectral features, so a text-only LLM can "hear" whether
the host is silent, quiet, loud, and roughly what kind of sound is playing.

Backend: ctypes on top of the Windows Core Audio (WASAPI) loopback endpoint,
no third-party audio library required. Falls back gracefully when numpy or
the loopback device is unavailable.
"""

from __future__ import annotations

import ctypes
import math
import sys
import time
from ctypes import wintypes
from typing import Any, Optional

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]

_CAPTURE_SECONDS_DEFAULT = 4.0
_CAPTURE_SECONDS_MAX = 15.0

COINIT_APARTMENTTHREADED = 0x2
CLSCTX_ALL = 0x17
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
WAVE_FORMAT_EXTENSIBLE = 0xFFFE
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_PCM = 0x0001

CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IAudioClient = "{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}"
IID_IAudioCaptureClient = "{C8ADBD64-E71E-48A0-A4DE-185C395CD317}"

KSDATAFORMAT_SUBTYPE_IEEE_FLOAT = bytes.fromhex("0000000300000010800000aa00389b71")
KSDATAFORMAT_SUBTYPE_PCM = bytes.fromhex("0100000000001000800000aa00389b71")


class GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    )


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = (
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    )


_ole32 = None


def _ensure_com() -> bool:
    global _ole32
    if sys.platform != "win32":
        return False
    try:
        _ole32 = ctypes.windll.ole32
        _ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        _ole32.CoInitializeEx.restype = ctypes.c_long
        _ole32.CoUninitialize.argtypes = []
        _ole32.CoUninitialize.restype = None
        _ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(GUID),
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        _ole32.CoCreateInstance.restype = ctypes.c_long
        _ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        _ole32.CoTaskMemFree.restype = None
        return True
    except Exception:
        return False


def _guid_bytes(guid: str) -> GUID:
    raw = bytes.fromhex(guid.strip("{}").replace("-", ""))
    return GUID(
        int.from_bytes(raw[0:4], "little"),
        int.from_bytes(raw[4:6], "little"),
        int.from_bytes(raw[6:8], "little"),
        (ctypes.c_ubyte * 8).from_buffer_copy(raw[8:16]),
    )


def _co_task_mem_free(ptr: Any) -> None:
    if ptr and _ole32 is not None:
        try:
            _ole32.CoTaskMemFree(ptr)
        except Exception:
            pass


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise RuntimeError(f"COM call failed {what}: hr=0x{hr & 0xFFFFFFFF:08X}")


def _com_method(vtbl, index: int, restype, argtypes):
    return ctypes.CFUNCTYPE(restype, *argtypes)(vtbl[index])


def _com_release(ptr: Any) -> None:
    if not ptr:
        return
    try:
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        release = _com_method(vtbl, 2, ctypes.c_long, [ctypes.c_void_p])
        release(ptr)
    except Exception:
        pass


def _decode_samples(raw: bytes, fmt: WAVEFORMATEX, frames: int) -> Optional[Any]:
    if np is None:
        return None
    bits = int(fmt.wBitsPerSample or 16)
    channels = max(1, int(fmt.nChannels or 1))
    format_tag = int(fmt.wFormatTag)
    if format_tag == WAVE_FORMAT_EXTENSIBLE:
        tail = bytes(raw)[44:60] if len(raw) >= 60 else b""
        format_tag = WAVE_FORMAT_IEEE_FLOAT if tail == KSDATAFORMAT_SUBTYPE_IEEE_FLOAT else WAVE_FORMAT_PCM
    try:
        if format_tag == WAVE_FORMAT_IEEE_FLOAT:
            data = np.frombuffer(raw, dtype=np.float32)
        elif bits == 16:
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif bits == 32:
            data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
        elif bits == 8:
            data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            return None
    except Exception:
        return None
    if data.size == 0:
        return None
    try:
        data = data[: frames * channels].reshape(-1, channels)
        return data.mean(axis=1).astype(np.float32)
    except Exception:
        return data.astype(np.float32)

class _AudioCaptureSession:
    """Minimal ctypes wrapper around an initialized WASAPI loopback client."""

    def __init__(self) -> None:
        self._enumerator = None
        self._device = None
        self._client = None
        self._capture = None
        self._format: Optional[WAVEFORMATEX] = None
        self._format_mem = None

    def open(self) -> None:
        if not _ensure_com():
            raise RuntimeError("only supported on Windows")
        _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        clsid = _guid_bytes(CLSID_MMDeviceEnumerator)
        iid_enum = _guid_bytes(IID_IMMDeviceEnumerator)
        iid_client = _guid_bytes(IID_IAudioClient)
        iid_capture = _guid_bytes(IID_IAudioCaptureClient)

        pp_enum = ctypes.c_void_p()
        _check(
            _ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_ALL, ctypes.byref(iid_enum), ctypes.byref(pp_enum)),
            "CoCreateInstance(MMDeviceEnumerator)",
        )
        self._enumerator = pp_enum
        vtbl = ctypes.cast(pp_enum, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        get_default = _com_method(vtbl, 4, ctypes.c_long, [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)])
        pp_dev = ctypes.c_void_p()
        _check(get_default(pp_enum, 0, 1, ctypes.byref(pp_dev)), "GetDefaultAudioEndpoint(eRender)")
        self._device = pp_dev

        dev_vtbl = ctypes.cast(pp_dev, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        activate = _com_method(dev_vtbl, 3, ctypes.c_long, [ctypes.c_void_p, ctypes.POINTER(GUID), wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)])
        pp_client = ctypes.c_void_p()
        _check(activate(pp_dev, ctypes.byref(iid_client), CLSCTX_ALL, None, ctypes.byref(pp_client)), "IMMDevice::Activate(IAudioClient)")
        self._client = pp_client

        client_vtbl = ctypes.cast(pp_client, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        get_mix = _com_method(client_vtbl, 8, ctypes.c_long, [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(WAVEFORMATEX))])
        p_fmt = ctypes.POINTER(WAVEFORMATEX)()
        _check(get_mix(pp_client, ctypes.byref(p_fmt)), "IAudioClient::GetMixFormat")
        self._format_mem = p_fmt
        fmt = p_fmt.contents

        init = _com_method(
            client_vtbl, 3, ctypes.c_long,
            [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_longlong, ctypes.c_longlong, ctypes.POINTER(WAVEFORMATEX), ctypes.c_void_p],
        )
        hns = 200000
        _check(init(pp_client, AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK, hns, hns, p_fmt, None), "IAudioClient::Initialize(loopback)")

        get_service = _com_method(client_vtbl, 14, ctypes.c_long, [ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)])
        pp_capture = ctypes.c_void_p()
        _check(get_service(pp_client, ctypes.byref(iid_capture), ctypes.byref(pp_capture)), "IAudioClient::GetService(IAudioCaptureClient)")
        self._capture = pp_capture
        self._format = fmt

    def capture(self, seconds: float) -> Optional[tuple[Any, int, int]]:
        if np is None:
            raise RuntimeError("numpy is unavailable, cannot analyze audio")
        fmt = self._format
        frames_per_sec = int(fmt.nSamplesPerSec)
        block_align = max(1, int(fmt.nBlockAlign))

        client_vtbl = ctypes.cast(self._client, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        start = _com_method(client_vtbl, 10, ctypes.c_long, [ctypes.c_void_p])
        _check(start(self._client), "IAudioClient::Start")

        capture_vtbl = ctypes.cast(self._capture, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        get_next = _com_method(capture_vtbl, 5, ctypes.c_long, [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)])
        get_buf = _com_method(
            capture_vtbl, 3, ctypes.c_long,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p],
        )
        release_buf = _com_method(capture_vtbl, 4, ctypes.c_long, [ctypes.c_void_p, wintypes.DWORD])

        chunks: list[Any] = []
        deadline = time.monotonic() + max(0.5, float(seconds))
        silent_count = 0
        try:
            while time.monotonic() < deadline:
                packet_size = wintypes.DWORD()
                if get_next(self._capture, ctypes.byref(packet_size)) < 0:
                    break
                frames = int(packet_size.value or 0)
                if frames <= 0:
                    time.sleep(0.01)
                    continue
                p_data = ctypes.c_void_p()
                n_frames = wintypes.DWORD()
                flags = wintypes.DWORD()
                hr = get_buf(self._capture, ctypes.byref(p_data), ctypes.byref(n_frames), ctypes.byref(flags), None, None)
                if hr < 0:
                    break
                got_frames = int(n_frames.value or 0)
                is_silent = bool(int(flags.value or 0) & AUDCLNT_BUFFERFLAGS_SILENT)
                try:
                    if is_silent or not p_data.value or got_frames <= 0:
                        mono = np.zeros(got_frames, dtype=np.float32)
                        silent_count += 1
                    else:
                        raw = ctypes.string_at(p_data, got_frames * block_align)
                        mono = _decode_samples(raw, fmt, got_frames)
                        if mono is None:
                            mono = np.zeros(got_frames, dtype=np.float32)
                    chunks.append(mono)
                finally:
                    release_buf(self._capture, got_frames)
        finally:
            try:
                stop = _com_method(client_vtbl, 11, ctypes.c_long, [ctypes.c_void_p])
                stop(self._client)
            except Exception:
                pass

        if not chunks:
            return None
        data = np.concatenate(chunks)
        if data.size == 0:
            return None
        return data, frames_per_sec, silent_count

    def close(self) -> None:
        if self._format_mem:
            _co_task_mem_free(self._format_mem)
            self._format_mem = None
        for attr in ("_capture", "_client", "_device", "_enumerator"):
            ptr = getattr(self, attr, None)
            if ptr:
                _com_release(ptr)
                setattr(self, attr, None)
        if _ole32 is not None:
            try:
                _ole32.CoUninitialize()
            except Exception:
                pass


def capture_and_analyze(seconds: float = _CAPTURE_SECONDS_DEFAULT) -> dict[str, Any]:
    """Capture host loopback audio for ``seconds`` and analyze its spectrum.

    Returns a dict with ``available``, spectral features, and a human-readable
    ``interpretation``. Never raises; errors are folded into the result dict.
    """
    if not _ensure_com():
        return {"available": False, "error": "仅支持 Windows 平台"}
    if np is None:
        return {"available": False, "error": "numpy 不可用，无法分析音频"}
    seconds = max(0.5, min(float(seconds or _CAPTURE_SECONDS_DEFAULT), _CAPTURE_SECONDS_MAX))

    session = _AudioCaptureSession()
    try:
        session.open()
        captured = session.capture(seconds)
    except Exception as exc:
        return {"available": False, "error": f"捕获失败：{exc}", "seconds_requested": round(seconds, 1)}
    finally:
        session.close()

    if captured is None:
        return {"available": False, "error": "未能捕获到音频数据", "seconds_requested": round(seconds, 1)}

    data, sample_rate, silent_count = captured
    features = analyze_signal(data, sample_rate)
    if not features.get("available"):
        return {**features, "seconds_requested": round(seconds, 1)}
    features["captured_seconds"] = round(float(len(data)) / max(1, sample_rate), 2)
    features["silent_buffers"] = silent_count
    features["interpretation"] = interpret_features(features)
    features["seconds_requested"] = round(seconds, 1)
    return features


def analyze_signal(data: Any, sample_rate: int) -> dict[str, Any]:
    if np is None:
        return {"available": False, "error": "numpy 不可用"}
    data = np.asarray(data, dtype=np.float32)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if data.size < 256:
        return {"available": False, "error": "捕获的音频过短"}

    sample_rate = max(1, int(sample_rate))
    rms = float(np.sqrt(np.mean(data**2)))
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    volume_db = 20.0 * math.log10(max(rms, 1e-9))
    silence = bool(rms < 0.002)

    window = np.hanning(len(data))
    spectrum = np.fft.rfft(data * window)
    power = np.abs(spectrum) ** 2
    freqs = np.fft.rfftfreq(len(data), 1.0 / sample_rate)
    total_power = float(np.sum(power))

    if total_power <= 1e-12:
        return {
            "available": True,
            "sample_rate": sample_rate,
            "duration_seconds": round(float(len(data)) / sample_rate, 2),
            "silence": True,
            "volume_db": round(volume_db, 1),
            "rms": 0.0,
            "peak": 0.0,
            "centroid_hz": 0.0,
            "rolloff_hz": 0.0,
            "dominant_hz": 0.0,
            "low_pct": 0.0,
            "mid_pct": 0.0,
            "high_pct": 0.0,
            "flatness_db": 0.0,
        }

    centroid = float(np.sum(freqs * power) / total_power)
    cumsum = np.cumsum(power)
    threshold = cumsum[-1] * 0.85
    rolloff_idx = min(int(np.searchsorted(cumsum, threshold)), len(freqs) - 1)
    rolloff = float(freqs[rolloff_idx])
    dominant = float(freqs[int(np.argmax(power))])

    low_mask = freqs <= 250.0
    mid_mask = (freqs > 250.0) & (freqs <= 4000.0)
    high_mask = freqs > 4000.0
    low_pct = float(np.sum(power[low_mask]) / total_power * 100.0)
    mid_pct = float(np.sum(power[mid_mask]) / total_power * 100.0)
    high_pct = float(np.sum(power[high_mask]) / total_power * 100.0)

    geometric = float(np.exp(np.mean(np.log(power + 1e-12))))
    flatness_db = 10.0 * math.log10(max(geometric, 1e-12) / (total_power / len(power)))

    return {
        "available": True,
        "sample_rate": sample_rate,
        "duration_seconds": round(float(len(data)) / sample_rate, 2),
        "silence": silence,
        "volume_db": round(volume_db, 1),
        "rms": round(rms, 5),
        "peak": round(peak, 5),
        "centroid_hz": round(centroid, 1),
        "rolloff_hz": round(rolloff, 1),
        "dominant_hz": round(dominant, 1),
        "low_pct": round(low_pct, 1),
        "mid_pct": round(mid_pct, 1),
        "high_pct": round(high_pct, 1),
        "flatness_db": round(flatness_db, 2),
        "nyquist_hz": float(sample_rate / 2.0),
    }


def interpret_features(features: dict[str, Any]) -> str:
    if not features.get("available"):
        return str(features.get("error") or "音频分析不可用")
    if features.get("silence"):
        return "电脑当前没有播放声音（静音）"
    volume_db = float(features.get("volume_db") or -100)
    low = float(features.get("low_pct") or 0)
    mid = float(features.get("mid_pct") or 0)
    high = float(features.get("high_pct") or 0)
    dominant = float(features.get("dominant_hz") or 0)
    flatness_db = float(features.get("flatness_db") or 0)

    if volume_db > -12:
        loudness = "音量很大"
    elif volume_db > -30:
        loudness = "音量中等"
    elif volume_db > -50:
        loudness = "声音较轻"
    else:
        loudness = "几乎听不到"

    parts = [f"正在播放声音（{loudness}，约 {volume_db:.0f} dB）"]
    if high > 60:
        parts.append("以高频为主（可能有人声、提示音或金属感声音）")
    elif low > 60:
        parts.append("以低频为主（可能是音乐低音、鼓点或引擎声）")
    elif mid > 45:
        parts.append("以中频为主（可能是对话、人声或器乐）")
    else:
        parts.append("频率分布较均衡（可能是音乐或混合内容）")
    if dominant:
        parts.append(f"能量最集中的频率约 {dominant:.0f} Hz")
    if flatness_db < -20:
        parts.append("音调性较强（接近纯音，如提示音/警报）")
    elif flatness_db < -5:
        parts.append("有音调成分（可能是音乐）")
    return "，".join(parts)


def describe_audio() -> dict[str, Any]:
    """Report whether host audio capture is supported on this platform."""
    return {
        "windows_supported": _ensure_com(),
        "numpy_available": np is not None,
        "available": _ensure_com() and np is not None,
    }


__all__ = [
    "_CAPTURE_SECONDS_DEFAULT",
    "_CAPTURE_SECONDS_MAX",
    "analyze_signal",
    "capture_and_analyze",
    "describe_audio",
    "interpret_features",
]
