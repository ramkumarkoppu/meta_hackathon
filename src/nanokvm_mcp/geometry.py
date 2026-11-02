"""Coordinate spaces and frame helpers.

Spaces:
  * capture px   - pixels of the HDMI frame delivered by the NanoKVM (e.g. 1920x1080)
  * region       - the part of the capture frame that shows the locked computer desktop (normally the
                   whole frame; smaller if the GPU adds black bars, set with ``region`` in nanokvm.toml)
  * screen coords- the region scaled to ``shot_width`` wide (nanokvm status shows the size as Screen);
                   this is the ONLY space pointer actions accept
  * abs          - the NanoKVM absolute-mouse grid, 0..4095 on each axis, spanning the target's
                   primary display (= the region)
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import cv2
import numpy as np

from .protocol import ABS_MAX, ABS_RANGE


@dataclass(frozen=True)
class Region:
    x: int
    y: int
    w: int
    h: int

    @classmethod
    def full(cls, width: int, height: int) -> "Region":
        return cls(0, 0, width, height)

    def clamp_to(self, width: int, height: int) -> "Region":
        x = max(0, min(self.x, width - 1))
        y = max(0, min(self.y, height - 1))
        w = max(1, min(self.w, width - x))
        h = max(1, min(self.h, height - y))
        return Region(x, y, w, h)

    def as_dict(self) -> dict:
        return asdict(self)


def shot_size(region: Region, shot_width: int, aspect: float | None = None) -> tuple[int, int]:
    """Size of the pointer coordinate space for a region: shot_width wide, at the region's aspect ratio
    (or the configured display aspect)."""
    ratio = aspect if aspect else region.w / region.h
    w = int(shot_width)
    h = max(1, int(round(w / ratio)))
    return w, h


def screen_to_abs(x: float, y: float, shot_w: int, shot_h: int) -> tuple[int, int]:
    """Screen coords (pixel centres) -> absolute 0..4095 grid."""
    ax = math.floor((x + 0.5) / shot_w * ABS_RANGE)
    ay = math.floor((y + 0.5) / shot_h * ABS_RANGE)
    return max(0, min(ABS_MAX, ax)), max(0, min(ABS_MAX, ay))


def screen_rect_to_capture(rect: tuple[float, float, float, float], region: Region,
                           shot_w: int, shot_h: int) -> tuple[int, int, int, int]:
    """Screen-coordinate rectangle -> capture-pixel rectangle (clamped to the region)."""
    x, y, w, h = rect
    sx, sy = region.w / shot_w, region.h / shot_h
    x0 = region.x + max(0.0, x) * sx
    y0 = region.y + max(0.0, y) * sy
    x1 = region.x + min(float(shot_w), x + w) * sx
    y1 = region.y + min(float(shot_h), y + h) * sy
    cx0, cy0 = int(math.floor(x0)), int(math.floor(y0))
    cx1, cy1 = int(math.ceil(x1)), int(math.ceil(y1))
    return cx0, cy0, max(1, cx1 - cx0), max(1, cy1 - cy0)


# ---------------------------------------------------------------- frames
def resize(img: np.ndarray, w: int, h: int) -> np.ndarray:
    if img.shape[1] == w and img.shape[0] == h:
        return img
    interp = cv2.INTER_AREA if (w < img.shape[1] or h < img.shape[0]) else cv2.INTER_CUBIC
    return cv2.resize(img, (w, h), interpolation=interp)


def encode_jpeg(img: np.ndarray, quality: int = 80, full_chroma: bool = True) -> bytes:
    """JPEG-encode. full_chroma keeps colour at full resolution (4:4:4) so thin coloured text
    (syntax-highlighted code) stays sharp; the default 4:2:0 halves colour resolution."""
    params = [cv2.IMWRITE_JPEG_QUALITY, int(quality)]
    if full_chroma and hasattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR"):
        params += [cv2.IMWRITE_JPEG_SAMPLING_FACTOR, cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444]
    ok, buf = cv2.imencode(".jpg", img, params)
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return buf.tobytes()


def encode_png(img: np.ndarray) -> bytes:
    """Lossless PNG (fast compression): no second lossy pass on top of the capture's MJPEG."""
    ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    if not ok:
        raise RuntimeError("PNG encoding failed")
    return buf.tobytes()


VISION_CELL = 28          # Muse Glimmer: one visual token per 28x28-pixel cell
VISION_MAX_TOKENS = 4096  # its per-image limit; llama.cpp shrinks larger images


def vision_tokens(w: int, h: int, cell: int = VISION_CELL) -> int:
    return math.ceil(w / cell) * math.ceil(h / cell)


def fit_token_budget(w: int, h: int, max_tokens: int = VISION_MAX_TOKENS, cell: int = VISION_CELL) -> tuple[int, int]:
    """Largest size with the same aspect ratio that costs at most max_tokens visual tokens
    (unchanged if it already fits), so the model sees exactly the pixels we send."""
    if vision_tokens(w, h, cell) <= max_tokens:
        return w, h
    s = math.sqrt(max_tokens * cell * cell / (w * h))
    while True:
        w2, h2 = max(1, int(w * s)), max(1, int(h * s))
        if vision_tokens(w2, h2, cell) <= max_tokens:
            return w2, h2
        s *= 0.99


def blank_stats(frame: np.ndarray) -> tuple[bool, float, float]:
    """(looks_blank, mean, std): a frame whose 160x90 grey thumbnail is nearly flat (tiny spread
    and tiny range) means no signal, a sleeping display, or a solid-colour screen."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    small = cv2.resize(gray, THUMB_SIZE, interpolation=cv2.INTER_AREA)
    mean, std = float(small.mean()), float(small.std())
    spread = int(small.max()) - int(small.min())
    return (std < 2.0 and spread < 12), mean, std


THUMB_SIZE = (160, 90)
