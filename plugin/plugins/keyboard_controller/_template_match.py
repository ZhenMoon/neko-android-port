"""Template (icon/image) matching inside a captured frame, numpy-only.

Lets a text-only LLM locate icons/buttons that OCR cannot name, then click
them: ``find_template(template, frame)`` returns the center of the best match.

Uses zero-normalized cross-correlation computed with FFT cross-correlation and
integral-image window statistics — fast for real-time screens and needs no
OpenCV. Scores are in [-1, 1].
"""

from __future__ import annotations

import math
from typing import Any

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]

DEFAULT_MIN_SCORE = 0.75
DEFAULT_TOP_K = 5


def _to_gray_float(image: Any) -> Any:
    if image is None:
        raise ValueError("image is None")
    if np is None:
        raise RuntimeError("numpy unavailable")
    if hasattr(image, "convert"):  # PIL Image
        arr = np.asarray(image.convert("L"), dtype=np.float32)
    else:
        arr = np.asarray(image, dtype=np.float32)
        if arr.ndim == 3:
            arr = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    if arr.ndim != 2:
        raise ValueError(f"expected 2D gray frame, got {arr.shape}")
    if arr.size == 0:
        raise ValueError("empty frame")
    return np.ascontiguousarray(arr)


def _integral(a: Any) -> Any:
    """2D integral image (shape +1 on both axes)."""
    ii = np.zeros((a.shape[0] + 1, a.shape[1] + 1), dtype=np.float64)
    np.cumsum(a, axis=0, out=ii[1:, 1:])
    np.cumsum(ii[1:, 1:], axis=1, out=ii[1:, 1:])
    return ii


def _window_sum(ii: Any, rows: int, cols: int) -> Any:
    """Sum over every (rows x cols) window. Output shape (H-rows+1, W-cols+1)."""
    return (ii[rows:, cols:]
            - ii[:-rows, cols:]
            - ii[rows:, :-cols]
            + ii[:-rows, :-cols])


def _ncc_scores(frame: Any, template: Any) -> Any:
    f = _to_gray_float(frame)
    t = _to_gray_float(template)
    th, tw = t.shape
    fh, fw = f.shape
    if th > fh or tw > fw:
        return None

    if th == fh and tw == fw:
        a = f - f.mean()
        b = t - t.mean()
        denom = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
        return np.array([[0.0 if denom <= 1e-12 else float((a * b).sum()) / denom]])

    # Cross-correlation via FFT (avoid scipy dependency).
    pad_h = fh + th - 1
    pad_w = fw + tw - 1
    fpad = np.zeros((pad_h, pad_w), dtype=np.float64)
    fpad[:fh, :fw] = f
    tpad = np.zeros((pad_h, pad_w), dtype=np.float64)
    tpad[:th, :tw] = t[::-1, ::-1]
    F = np.fft.rfft2(fpad)
    T = np.fft.rfft2(tpad)
    prod = np.fft.irfft2(F * np.conj(T), s=(pad_h, pad_w))
    # prod[dy, dx] = sum_yx f[dy+y, dx+x] * t[y,x], so the peak at (dy,dx) is
    # the template top-left position in the frame. Keep the valid region
    # [0..fh-th] x [0..fw-tw].
    corr = prod[: fh - th + 1, : fw - tw + 1]

    # Window mean/std of frame via integral images.
    count = float(th * tw)
    ii_f = _integral(f)
    f_sum = _window_sum(ii_f, th, tw)
    ii_f2 = _integral(f.astype(np.float64) ** 2)
    f_sq = _window_sum(ii_f2, th, tw)
    f_mean = f_sum / count
    f_var = f_sq / count - f_mean * f_mean
    f_std = np.sqrt(np.maximum(f_var, 0.0))

    t_mean = float(t.mean())
    t_var = float(((t - t_mean) ** 2).sum()) / count
    t_std = math.sqrt(max(t_var, 0.0))

    numerator = corr - count * f_mean * t_mean
    denominator = f_std * t_std * count
    scores = np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 1e-12)
    return np.clip(scores, -1.0, 1.0)


def find_template(
    frame: Any,
    template: Any,
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    top_k: int = DEFAULT_TOP_K,
    dedupe_distance: int = 24,
) -> list[dict[str, Any]]:
    """Find ``template`` inside ``frame``.

    Returns a list of dicts (best first): ``{"x","y","w","h","score"}`` with
    center in frame pixels. Empty list when nothing clears ``min_score``.
    """
    if np is None:
        return []
    if template is None or frame is None:
        return []
    scores = _ncc_scores(frame, template)
    if scores is None or scores.size == 0:
        return []

    t = _to_gray_float(template)
    th, tw = t.shape
    candidates: list[tuple[float, int, int]] = []
    flat = scores.ravel()
    order = flat.argsort()[::-1][: max(1, int(top_k) * 16)]
    for idx in order:
        score = float(flat[idx])
        if score < float(min_score):
            continue
        ry, rx = divmod(int(idx), scores.shape[1])
        too_close = any(
            abs(ry - ay) < dedupe_distance and abs(rx - ax) < dedupe_distance
            for _, ay, ax in candidates
        )
        if too_close:
            continue
        candidates.append((score, int(ry), int(rx)))
        if len(candidates) >= int(top_k):
            break

    results: list[dict[str, Any]] = []
    for score, ry, rx in sorted(candidates, key=lambda c: -c[0]):
        results.append({
            "x": int(rx + tw / 2),
            "y": int(ry + th / 2),
            "w": int(tw),
            "h": int(th),
            "score": round(score, 4),
        })
    return results


def is_available() -> bool:
    return np is not None


__all__ = [
    "DEFAULT_MIN_SCORE",
    "DEFAULT_TOP_K",
    "find_template",
    "is_available",
]
