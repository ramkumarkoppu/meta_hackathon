"""Coordinate spaces (geometry.py): screen coordinates to the KVM's absolute mouse grid and to capture pixels,
and the check for a blank picture. Every click the service sends and every crop Glimmer reads goes through these."""
import cv2
import numpy as np

from nanokvm_mcp import geometry as G
from nanokvm_mcp.protocol import ABS_MAX


def test_shot_size_default_and_aspect():
    r = G.Region.full(1920, 1080)
    assert G.shot_size(r, 1280) == (1280, 720)
    assert G.shot_size(r, 1280, aspect=16 / 10) == (1280, 800)
    assert G.shot_size(G.Region(96, 0, 1728, 1080), 1280) == (1280, 800)




def test_screen_to_abs_corners_and_centre():
    assert G.screen_to_abs(0, 0, 1280, 720) == (1, 2)
    assert G.screen_to_abs(1279, 719, 1280, 720) == (4094, 4093)
    assert G.screen_to_abs(639.5, 359.5, 1280, 720) == (2048, 2048)
    assert G.screen_to_abs(5000, -10, 1280, 720) == (ABS_MAX, 0)




def test_target_pixel_lands_where_the_click_was_aimed():
    # the locked computer maps abs a to desktop pixel floor(a * W / 4096). A click at screen coordinate x, in a
    # 1280-wide space over a 1920-wide desktop, must land within 1 desktop pixel of x * 1.5
    for x in range(0, 1280, 37):
        ax, _ = G.screen_to_abs(x, 0, 1280, 720)
        desk = ax * 1920 // 4096
        assert abs((desk + 0.5) - (x + 0.5) * 1.5) <= 1.0   # pixel centre vs pixel centre


def test_screen_rect_to_capture():
    r = G.Region(96, 0, 1728, 1080)
    assert G.screen_rect_to_capture((0, 0, 1280, 800), r, 1280, 800) == (96, 0, 1728, 1080)
    cx, cy, cw, ch = G.screen_rect_to_capture((640, 400, 100, 50), r, 1280, 800)
    assert (cx, cy) == (96 + 864, 540) and abs(cw - 135) <= 1 and abs(ch - 68) <= 1










def test_blank_detection():
    assert G.blank_stats(np.zeros((1080, 1920, 3), np.uint8))[0] is True
    jpeg_noise = np.random.default_rng(0).integers(0, 4, (1080, 1920, 3), dtype=np.uint8)
    assert G.blank_stats(jpeg_noise)[0] is True                       # black + compression noise
    desktop = np.full((1080, 1920, 3), 240, np.uint8)                  # white document ...
    desktop[1032:] = 32                                                # ... with a dark taskbar
    assert G.blank_stats(desktop)[0] is False
    one_line = np.full((1080, 1920, 3), 250, np.uint8)
    cv2.putText(one_line, "x = 1", (100, 100), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    assert G.blank_stats(one_line)[0] is False
