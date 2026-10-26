"""HDMI capture from the NanoKVM-USB (MacroSilicon MS2131 UVC device) via OpenCV DirectShow.

Rules (from OpenCV's cap_dshow.cpp):
  * Only open a device whose DirectShow path carries an allowed VID:PID. Never fall back to
    "index 0": on the AI computer that is its own webcam. Setting ``video_index``
    skips this check, so use it with care.
  * One thread owns cv2.VideoCapture for its whole life (DirectShow COM apartment).
  * grab() + retrieve(), never read(): read() reports success even when retrieve() timed out.
  * Read continuously, otherwise DirectShow hands back an old frame.
  * Property order: FPS, WIDTH, HEIGHT, FOURCC last.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Frame:
    image: np.ndarray   # BGR uint8, HxWx3
    t: float            # time.monotonic() when captured
    wall: float         # time.time() when captured
    seq: int


class CaptureSource(Protocol):
    def wait_frame(self, after_seq: int = 0, timeout: float = 3.0) -> Frame | None: ...
    def latest(self) -> Frame | None: ...
    def status(self) -> dict: ...
    def close(self) -> None: ...


class FrameStore:
    """Thread-safe 'latest frame' slot with a condition variable for waiters."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._latest: Frame | None = None
        self._seq = 0
        self._fps = 0.0

    def put(self, image: np.ndarray) -> Frame:
        now = time.monotonic()
        with self._cond:
            if self._latest is not None:
                dt = now - self._latest.t
                if dt > 0:
                    inst = 1.0 / dt
                    self._fps = inst if self._fps == 0 else 0.9 * self._fps + 0.1 * inst
            self._seq += 1
            self._latest = Frame(image, now, time.time(), self._seq)
            self._cond.notify_all()
            return self._latest

    def latest(self) -> Frame | None:
        with self._cond:
            return self._latest

    def wait_frame(self, after_seq: int = 0, timeout: float = 3.0) -> Frame | None:
        with self._cond:
            self._cond.wait_for(lambda: self._latest is not None and self._latest.seq > after_seq, timeout)
            f = self._latest
            return f if f is not None and f.seq > after_seq else None

    def wake(self) -> None:
        with self._cond:
            self._cond.notify_all()

    @property
    def fps(self) -> float:
        return self._fps


def fourcc_to_str(value: float) -> str:
    v = int(value)
    return "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4))


def require_windows() -> None:
    """Capture uses DirectShow, so the AI computer must run Windows for now (the locked computer may
    run Windows, macOS or Linux). Without this, macOS and Linux fail with an obscure backend error."""
    if sys.platform != "win32":
        raise RuntimeError(f"video capture needs Windows on the AI computer for now (it uses DirectShow; "
                           f"this is {sys.platform}). See README section 1a.")


def list_video_devices() -> list[dict]:
    """DirectShow capture devices (enumeration only; nothing is opened)."""
    require_windows()
    import cv2
    from cv2_enumerate_cameras import enumerate_cameras
    out = []
    for c in enumerate_cameras(cv2.CAP_DSHOW):
        out.append({"index": c.index, "name": c.name, "vid": c.vid, "pid": c.pid,
                    "path": getattr(c, "path", None)})
    return out


def find_capture_device(ids: list[tuple[int, int]]) -> dict | None:
    hits = [d for d in list_video_devices() if (d["vid"], d["pid"]) in ids]
    if len(hits) > 1:
        log.warning("several capture devices match %s, using the first: %r", ids, hits)
    return hits[0] if hits else None


class HdmiCapture:
    """Continuously captures the NanoKVM HDMI input in a background thread."""

    def __init__(self, ids: list[tuple[int, int]], index: int | None = None, width: int = 1920,
                 height: int = 1080, fps: int = 30, fourcc: str = "MJPG",
                 no_frame_timeout_s: float = 5.0, reopen_after_s: float = 15.0) -> None:
        self.ids, self.index = ids, index
        self.width, self.height, self.fps, self.fourcc = width, height, fps, fourcc
        self.no_frame_timeout_s = no_frame_timeout_s
        self.reopen_after_s = reopen_after_s   # the chip may not resume by itself after HDMI loss
        self.store = FrameStore()
        self._lock = threading.Lock()
        self._state = "starting"
        self._error: str | None = None
        self._device: str | None = None
        self._mode: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hdmi-capture", daemon=True)
        self._thread.start()

    # ---- public
    def latest(self) -> Frame | None:
        return self.store.latest()

    def wait_frame(self, after_seq: int = 0, timeout: float = 3.0) -> Frame | None:
        return self.store.wait_frame(after_seq, timeout)

    def status(self) -> dict:
        f = self.store.latest()
        with self._lock:
            return {
                "state": self._state,
                "error": self._error,
                "device": self._device,
                "mode": self._mode,
                "fps": round(self.store.fps, 1),
                "frame_size": None if f is None else [int(f.image.shape[1]), int(f.image.shape[0])],
                "last_frame_age_ms": None if f is None else int((time.monotonic() - f.t) * 1000),
            }

    def close(self) -> None:
        self._stop.set()
        self.store.wake()
        self._thread.join(timeout=5)

    # ---- capture thread
    def _set(self, state: str, error: str | None = None) -> None:
        with self._lock:
            if (state, error) != (self._state, self._error):
                log.info("capture %s%s", state, f": {error}" if error else "")
            self._state, self._error = state, error

    def _open(self):
        require_windows()
        import cv2
        if self.index is not None:
            idx, name = self.index, f"DirectShow index {self.index} (configured)"
        else:
            info = find_capture_device(self.ids)
            if info is None:
                want = ", ".join(f"{v:04X}:{p:04X}" for v, p in self.ids)
                raise RuntimeError(
                    f"NanoKVM capture device not found (USB VID:PID {want}). Plug the NanoKVM's "
                    "HOST-end USB-C into this laptop.")
            idx, name = info["index"], f"{info['name']} (DirectShow index {info['index']})"
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"DirectShow could not open {name}; another program may be using it")
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        if self.fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.fourcc))
        got_w, got_h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        got_fourcc = fourcc_to_str(cap.get(cv2.CAP_PROP_FOURCC))
        good = 0
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline and good < 3:
            if not cap.grab():
                break
            ok, _ = cap.retrieve()
            good += bool(ok)
        if good == 0:
            cap.release()
            raise RuntimeError(
                "the capture device opened but sent no video. Another program may be using it "
                "(NanoKVM-USB app, usbkvm.sipeed.com tab, Camera app, OBS, Teams), or the locked computer "
                "has its HDMI output off.")
        with self._lock:
            self._device = name
            self._mode = f"{got_w}x{got_h} {got_fourcc} (requested {self.width}x{self.height}@{self.fps})"
        log.info("capture opened %s as %s", name, self._mode)
        return cap

    def _run(self) -> None:
        cap = None
        backoff = 0.5
        no_frame_since: float | None = None
        try:
            while not self._stop.is_set():
                if cap is None:
                    try:
                        cap = self._open()
                        backoff, no_frame_since = 0.5, None
                        self._set("streaming")
                    except Exception as e:  # noqa: BLE001
                        self._set("error", str(e))
                        self._stop.wait(backoff)
                        backoff = min(backoff * 2, 5.0)
                    continue
                if not cap.grab():                  # False only after the device was lost
                    cap.release()
                    cap = None
                    self._set("error", "capture device lost (unplugged?); reconnecting")
                    continue
                ok, img = cap.retrieve()            # waits up to 1 s for a new frame
                now = time.monotonic()
                if not ok or img is None:
                    no_frame_since = no_frame_since or now
                    if now - no_frame_since > self.no_frame_timeout_s:
                        self._set("no_frames", f"no video for {now - no_frame_since:.0f}s "
                                  "(locked computer asleep, HDMI unplugged, or HDMI in the wrong port?)")
                    if now - no_frame_since > self.reopen_after_s:
                        log.info("no frames for %.0fs, reopening the capture device", now - no_frame_since)
                        cap.release()
                        cap = None
                        self._stop.wait(backoff)
                        backoff = min(backoff * 2, 10.0)
                    continue
                no_frame_since = None
                self.store.put(img)
                if self._state != "streaming":
                    self._set("streaming")
        finally:
            if cap is not None:
                cap.release()
            self._set("stopped")
