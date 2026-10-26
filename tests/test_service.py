"""The HTTP service against the simulated Work laptop."""
import base64
import json
import threading
import time
import urllib.error
import urllib.request

import cv2
import numpy as np
import pytest

from nanokvm_mcp.client import ServiceError
from nanokvm_mcp.simulator import BUTTON_RECT


def decode(shot):
    return cv2.imdecode(np.frombuffer(base64.b64decode(shot["jpeg_b64"]), np.uint8), cv2.IMREAD_COLOR)


def raw(env, path, headers=None, method="GET", data=None):
    req = urllib.request.Request(env.cfg.base_url + path, method=method, data=data, headers=headers or {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_rejects_missing_or_wrong_token(sim):
    assert raw(sim, "/api/status")[0] == 401
    assert raw(sim, "/api/status", {"X-NanoKVM-Token": "nope"})[0] == 401
    assert raw(sim, "/api/status", {"X-NanoKVM-Token": sim.token})[0] == 200
    assert raw(sim, f"/api/status?t={sim.token}")[0] == 200


def test_rejects_foreign_host_header(sim):
    code, _ = raw(sim, "/api/status", {"X-NanoKVM-Token": sim.token, "Host": "evil.example:80"})
    assert code == 403


def test_post_from_web_page_without_token_cannot_type(sim):
    body = json.dumps({"action": "type", "text": "pwned"}).encode()
    code, _ = raw(sim, "/api/act", {"Content-Type": "text/plain"}, method="POST", data=body)
    assert code == 401
    assert sim.desk.text == ""


def test_status(sim):
    st = sim.client.status()
    assert st["service"] == "nanokvm" and st["simulated"] is True
    assert st["capture"]["state"] == "streaming"
    assert st["screen"]["width"] == 1280 and st["screen"]["height"] == 720


def test_screenshot_is_live_jpeg(sim):
    shot = sim.client.screenshot()
    img = decode(shot)
    assert img.shape[:2] == (720, 1280)
    assert shot["age_ms"] < 1000 and shot["warnings"] == []


def test_click_lands_on_button(sim):
    bx, by, bw, bh = BUTTON_RECT                   # desktop px; screenshot is 2/3 scale
    x, y = (bx + bw / 2) * 2 / 3, (by + bh / 2) * 2 / 3
    res = sim.client.act({"action": "click", "x": x, "y": y, "screenshot_after": True})
    assert sim.desk.button_presses == 1
    assert res["screen_changed"] is True and "shot" in res
    cx, cy, _ = sim.desk.clicks[-1]
    assert abs(cx - (bx + bw / 2)) <= 2 and abs(cy - (by + bh / 2)) <= 2


def test_click_outside_screen_is_rejected(sim):
    with pytest.raises(ServiceError, match="outside the screen"):
        sim.client.act({"action": "click", "x": 1280, "y": 10})


def test_type_and_keys(sim):
    r = sim.client.act({"action": "type", "text": "Hello, World! 123", "press_enter": True})
    assert "typed 17" in r["summary"]
    assert sim.desk.text == "Hello, World! 123\n"
    sim.client.act({"action": "keys", "keys": "backspace backspace"})
    assert sim.desk.text == "Hello, World! 12"
    r = sim.client.act({"action": "type", "text": "naïve"})
    assert "skipped 1" in r["summary"] and sim.desk.text.endswith("nave")


def test_combo_reaches_target(sim):
    sim.client.act({"action": "keys", "keys": "ctrl+alt+delete"})
    assert (0x05, (0x4C,)) in sim.desk.combos


def test_bad_key_name_message(sim):
    with pytest.raises(ServiceError, match="unknown key"):
        sim.client.act({"action": "keys", "keys": "ctrl+banana"})


def test_text_limit(sim):
    with pytest.raises(ServiceError, match="limit"):
        sim.client.act({"action": "type", "text": "x" * (sim.cfg.max_text_len + 1)})


def test_scroll(sim):
    sim.client.act({"action": "scroll", "x": 640, "y": 360, "direction": "down", "notches": 4})
    assert sim.desk.scroll_total == -4


def test_wait_change_detects_typing(sim):
    out = {}

    def waiter():
        out["r"] = sim.client.wait_change(5.0, 0.05, 200)

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.4)
    sim.client.act({"action": "type", "text": "change!"})
    t.join(10)
    assert out["r"]["changed"] is True and "shot" in out["r"]


def test_wait_change_times_out_quietly(sim):
    r = sim.client.wait_change(0.8, 0.5, 200)
    assert r["changed"] is False and "shot" not in r


def test_zoom_returns_enlarged_crop(sim):
    z = sim.client.zoom(0, 0, 320, 180)       # 480x270 capture px, enlarged 3x (the cap)
    assert z["width"] == 1440 and z["screen_rect"] == [0.0, 0.0, 320.0, 180.0]
    assert decode(z).shape[1] == 1440
    big = sim.client.zoom(0, 0, 1280, 720)    # whole screen at native 1920 -> limited to 1456 wide
    assert big["width"] == 1456


def test_zoom_outside_rejected(sim):
    with pytest.raises(ServiceError):
        sim.client.zoom(2000, 0, 10, 10)


def test_release_all(sim):
    r = sim.client.act({"action": "release_all"})
    assert "released" in r["summary"]


def test_calibration_finds_pillarbox(sim_factory):
    env = sim_factory(bars=(96, 0))
    r = env.client.calibrate()
    assert r["method"] == "cursor"
    reg = r["region"]
    assert abs(reg["x"] - 96) <= 3 and abs(reg["w"] - 1728) <= 6 and reg["y"] == 0 and reg["h"] == 1080
    assert r["screen_size"] == [1280, 800]
    # clicks now land correctly inside the pillarboxed desktop
    bx, by, bw, bh = BUTTON_RECT
    scale = 1280 / 1728
    env.client.act({"action": "click", "x": (bx + bw / 2) * scale, "y": (by + bh / 2) * scale})
    assert env.desk.button_presses == 1
    assert env.client.calibrate(reset=True)["method"] == "reset"
    assert env.client.status()["screen"]["region_source"] == "full frame"


def test_calibration_full_frame(sim):
    r = sim.client.calibrate()
    assert r["method"] == "cursor"
    assert r["region"] == {"x": 0, "y": 0, "w": 1920, "h": 1080}


def test_view_only_mode(sim_factory):
    env = sim_factory(control=False)
    with pytest.raises(ServiceError, match="disabled"):
        env.client.act({"action": "click", "x": 1, "y": 1})
    assert env.client.status()["control"]["enabled"] is False


def test_click_rejected_after_coordinate_space_changes(sim_factory):
    env = sim_factory(bars=(96, 0))
    env.client.screenshot()                                  # Claude saw 1280x720
    env.client.calibrate()                                   # space becomes 1280x800
    with pytest.raises(ServiceError, match="coordinate space changed"):
        env.client.act({"action": "click", "x": 10, "y": 10})
    env.client.screenshot()                                  # new screenshot -> allowed again
    env.client.act({"action": "click", "x": 10, "y": 10})


def test_status_and_release_all_respond_while_typing(sim):
    sim.service.hid().hold = 0.01                            # slow typing: ~1 s for 40 chars
    sim.service.hid().gap = 0.01
    out = {}
    t = threading.Thread(target=lambda: out.update(r=sim.client.act({"action": "type", "text": "y" * 40})))
    t.start()
    time.sleep(0.3)
    t0 = time.monotonic()
    st = sim.client.status()
    assert time.monotonic() - t0 < 1.0 and st["control"]["state"].startswith("busy")
    sim.client.act({"action": "release_all"})
    t.join(10)
    assert "STOPPED early" in out["r"]["summary"]
    assert 0 < len(sim.desk.text) < 40


def test_second_action_gets_busy_instead_of_running_later(sim):
    hid = sim.service.hid()
    hid.busy_timeout = 0.3
    hid.hold = hid.gap = 0.02
    t = threading.Thread(target=lambda: sim.client.act({"action": "type", "text": "a" * 40}))
    t.start()
    time.sleep(0.2)
    with pytest.raises(ServiceError, match="busy"):
        sim.client.act({"action": "type", "text": "SECRET"})
    t.join(10)
    time.sleep(0.3)
    assert "SECRET" not in sim.desk.text


def test_viewer_page_and_stream(sim):
    code, html = raw(sim, f"/?t={sim.token}")
    assert code == 200 and b"live view" in html
    req = urllib.request.Request(f"{sim.cfg.base_url}/stream.mjpg?t={sim.token}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=5) as r:
        assert r.headers["Content-Type"].startswith("multipart/x-mixed-replace")
        chunk = r.read(4096)
        assert b"--frame" in chunk and b"\xff\xd8" in chunk


def test_screen_sharing_gates_claude_captures(sim):
    from nanokvm_mcp.client import ServiceClient
    claude = ServiceClient(sim.cfg, client_id="conversation-A")
    other = ServiceClient(sim.cfg, client_id="conversation-B")
    for fn in (claude.screenshot, lambda: claude.zoom(0, 0, 10, 10), lambda: claude.wait_change(0.5, 0.5, 100)):
        with pytest.raises(ServiceError, match="sharing is off"):
            fn()
    r = claude.share(5)
    assert r["sharing"] and r["minutes"] == 5
    assert claude.screenshot()["width"] == 1280
    assert claude.status()["sharing"]["active"] is True
    with pytest.raises(ServiceError, match="sharing is off"):      # sharing is per conversation
        other.screenshot()
    assert claude.unshare()["was_sharing"] is True
    with pytest.raises(ServiceError, match="sharing is off"):
        claude.screenshot()
    assert sim.client.screenshot()["width"] == 1280                 # user's own CLI (no client id) is not gated


def test_actions_without_sharing_return_no_screenshot(sim):
    from nanokvm_mcp.client import ServiceClient
    claude = ServiceClient(sim.cfg, client_id="conversation-A")
    r = claude.act({"action": "keys", "keys": "a", "screenshot_after": True})
    assert "shot" not in r and "sharing is off" in r["note"]
    claude.share(1)
    r = claude.act({"action": "keys", "keys": "b", "screenshot_after": True})
    assert "shot" in r


def test_share_expires(sim):
    from nanokvm_mcp.client import ServiceClient
    claude = ServiceClient(sim.cfg, client_id="conversation-A")
    claude.share(1)
    exp, wall = sim.service._shares["conversation-A"]
    sim.service._shares["conversation-A"] = (exp - 61, wall)        # pretend a minute passed
    with pytest.raises(ServiceError, match="sharing is off"):
        claude.screenshot()
