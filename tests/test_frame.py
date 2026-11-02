"""The service's /api/frame: the full-resolution frame Glimmer reads. It goes only to a client with an active
share, fits Glimmer's visual-token budget and is never written to disk."""
import base64
import hashlib

import cv2
import numpy as np
import pytest

from nanokvm_mcp import geometry as G
from nanokvm_mcp.client import ServiceClient, ServiceError


def _decode(r: dict) -> np.ndarray:
    data = base64.b64decode(r["image_b64"])
    assert hashlib.sha256(data).hexdigest() == r["image_sha256"]
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def test_frame_needs_a_client_id(sim):
    with pytest.raises(ServiceError) as e:
        sim.client.frame()
    assert e.value.status == 403


def test_frame_needs_an_active_share(sim):
    pipe = ServiceClient(sim.cfg, client_id="pipeline-test")
    with pytest.raises(ServiceError) as e:
        pipe.frame()
    assert e.value.status == 403
    pipe.share(5)
    r = pipe.frame()
    assert (r["width"], r["height"]) == (1920, 1080)          # native capture size, not the 1280-wide screen
    assert r["visual_tokens"] == 69 * 39
    assert r["format"] == "png"
    assert _decode(r).shape == (1080, 1920, 3)
    pipe.unshare()
    with pytest.raises(ServiceError):
        pipe.frame()


def test_frame_rectangle_is_in_screen_coordinates(sim):
    pipe = ServiceClient(sim.cfg, client_id="pipeline-test")
    pipe.share(5)
    r = pipe.frame((100, 50, 200, 100))                       # screen coordinates are 1280x720 here
    assert r["capture_rect"] == [150, 75, 300, 150]           # x1.5 into the 1920x1080 capture
    assert (r["width"], r["height"]) == (300, 150)
    assert _decode(r).shape == (150, 300, 3)


def test_frame_as_jpeg(sim):
    pipe = ServiceClient(sim.cfg, client_id="pipeline-test")
    pipe.share(5)
    r = pipe.frame(fmt="jpeg")
    assert r["format"] == "jpeg"
    assert _decode(r).shape == (1080, 1920, 3)


def test_frame_respects_a_smaller_token_budget(sim):
    pipe = ServiceClient(sim.cfg, client_id="pipeline-test")
    pipe.share(5)
    r = pipe.frame(max_tokens=1000)
    assert r["visual_tokens"] <= 1000
    assert abs(r["width"] / r["height"] - 16 / 9) < 0.02


def test_frame_writes_nothing_to_disk(sim):
    pipe = ServiceClient(sim.cfg, client_id="pipeline-test")
    pipe.share(5)
    before = {p for p in sim.cfg.home.rglob("*") if p.is_file()}
    pipe.frame()
    pipe.frame((0, 0, 640, 360), fmt="jpeg")
    after = {p for p in sim.cfg.home.rglob("*") if p.is_file()}
    assert after == before


def test_token_budget_helpers():
    assert G.vision_tokens(1920, 1080) == 2691
    assert G.fit_token_budget(1920, 1080) == (1920, 1080)
    w, h = G.fit_token_budget(2560, 1600)
    assert G.vision_tokens(w, h) <= 4096 and abs(w / h - 1.6) < 0.01
