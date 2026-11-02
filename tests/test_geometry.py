import cv2
import numpy as np

from nanokvm_mcp import geometry as G
from nanokvm_mcp.protocol import ABS_MAX


def test_shot_size_default_and_aspect():
    r = G.Region.full(1920, 1080)
    assert G.shot_size(r, 1280) == (1280, 720)
    assert G.shot_size(r, 1280, aspect=16 / 10) == (1280, 800)
    assert G.shot_size(G.Region(96, 0, 1728, 1080), 1280) == (1280, 800)


def test_vision_budget():
    assert G.fit_vision_budget(1280, 720) == (1280, 720)              # 1196 tokens: untouched
    assert G.fit_vision_budget(1456, 819) == (1456, 819)              # 1560 tokens: untouched
    for w, h in [(1456, 1092), (1456, 1456), (1456, 910), (3000, 400), (1920, 1080)]:
        w2, h2 = G.fit_vision_budget(w, h)
        assert G.vision_tokens(w2, h2) <= G.VISION_MAX_TOKENS and max(w2, h2) <= G.VISION_MAX_SIDE
        assert abs(w2 / h2 - w / h) < 0.02
    # a too-large configured screenshot width is reduced, so the coordinate space stays within the cap too
    assert G.vision_tokens(*G.shot_size(G.Region.full(1920, 1200), 1456)) <= G.VISION_MAX_TOKENS


def test_screen_to_abs_corners_and_centre():
    assert G.screen_to_abs(0, 0, 1280, 720) == (1, 2)
    assert G.screen_to_abs(1279, 719, 1280, 720) == (4094, 4093)
    assert G.screen_to_abs(639.5, 359.5, 1280, 720) == (2048, 2048)
    assert G.screen_to_abs(5000, -10, 1280, 720) == (ABS_MAX, 0)


def test_abs_screen_roundtrip():
    for x, y in [(0, 0), (100, 200), (640, 360), (1279, 719)]:
        ax, ay = G.screen_to_abs(x, y, 1280, 720)
        bx, by = G.abs_to_screen(ax, ay, 1280, 720)
        assert abs(bx - x) < 0.5 and abs(by - y) < 0.5


def test_target_pixel_lands_where_the_screenshot_was_clicked():
    # the target maps abs a -> desktop pixel floor(a * W / 4096); a click at screenshot pixel x on a
    # 1280-wide image of a 1920-wide desktop must land within 1 desktop pixel of x * 1.5
    for x in range(0, 1280, 37):
        ax, _ = G.screen_to_abs(x, 0, 1280, 720)
        desk = ax * 1920 // 4096
        assert abs((desk + 0.5) - (x + 0.5) * 1.5) <= 1.0   # pixel centre vs pixel centre


def test_screen_rect_to_capture():
    r = G.Region(96, 0, 1728, 1080)
    assert G.screen_rect_to_capture((0, 0, 1280, 800), r, 1280, 800) == (96, 0, 1728, 1080)
    cx, cy, cw, ch = G.screen_rect_to_capture((640, 400, 100, 50), r, 1280, 800)
    assert (cx, cy) == (96 + 864, 540) and abs(cw - 135) <= 1 and abs(ch - 68) <= 1


def _frame(bars_x=0, bars_y=0, fill=120):
    f = np.zeros((1080, 1920, 3), np.uint8)
    f[bars_y:1080 - bars_y, bars_x:1920 - bars_x] = fill
    return f


def test_detect_pillarbox():
    assert G.detect_black_bars(_frame(bars_x=96)) == G.Region(96, 0, 1728, 1080)


def test_detect_letterbox():
    assert G.detect_black_bars(_frame(bars_y=60)) == G.Region(0, 60, 1920, 960)


def test_no_bars_on_normal_or_asymmetric_frames():
    assert G.detect_black_bars(_frame()) is None
    f = _frame()
    f[:, :200] = 0          # dark area only on one side (e.g. a black sidebar)
    assert G.detect_black_bars(f) is None
    g = _frame(bars_x=96)
    g[500, 10] = 200        # something drawn in the "bar" -> not a GPU bar
    assert G.detect_black_bars(g) is None


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


def test_change_fraction():
    a = np.full((1080, 1920, 3), 100, np.uint8)
    b = a.copy()
    assert G.change_fraction(G.thumbnail(a), G.thumbnail(b)) == 0.0
    b[0:540, 0:960] = 200      # a quarter of the screen
    frac = G.change_fraction(G.thumbnail(a), G.thumbnail(b))
    assert 0.24 <= frac <= 0.26


def test_change_fraction_sees_equal_brightness_colour_change():
    a = np.zeros((1080, 1920, 3), np.uint8)
    a[:] = (60, 60, 200)        # red-ish (BGR), grey level ~102
    b = a.copy()
    b[:] = (60, 160, 60)        # green-ish, grey level ~119 (below the threshold in grey)
    assert G.change_fraction(G.thumbnail(a), G.thumbnail(b)) == 1.0


def test_fit_and_region_from_fit():
    # pillarboxed desktop 1728 px wide starting at x=96: px = 96 + abs * 1728/4096
    xs = [819, 2048, 3276]
    fx = G.fit_axis(xs, [96 + a * 1728 / 4096 for a in xs])
    fy = G.fit_axis(xs, [a * 1080 / 4096 for a in xs])
    assert G.region_from_fit(fx, fy, 1920, 1080) == G.Region(96, 0, 1728, 1080)
    full_x = G.fit_axis(xs, [a * 1920 / 4096 + 1.5 for a in xs])   # small offset -> snaps to full
    assert G.region_from_fit(full_x, fy, 1920, 1080) == G.Region(0, 0, 1920, 1080)
