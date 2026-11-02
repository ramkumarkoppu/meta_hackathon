"""The nanokvm service's HTTP API against the simulated locked computer. The service is the only process that
opens the USB KVM. It checks the token and the Host header, maps screen coordinates to the KVM's mouse, types and
clicks, streams the live view, and hands out frames only to a client with an active screen share."""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from nanokvm_mcp.client import ServiceError
from nanokvm_mcp.simulator import BUTTON_RECT


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
    assert raw(sim, f"/api/status?t={sim.token}")[0] == 401          # never accepted in a URL


def test_rejects_foreign_host_header(sim):
    """Only a localhost Host header is served, which blocks DNS rebinding."""
    code, _ = raw(sim, "/api/status", {"X-NanoKVM-Token": sim.token, "Host": "evil.example:80"})
    assert code == 403


def test_post_from_web_page_without_token_cannot_type(sim):
    """A web page can send a text/plain POST without a CORS preflight, so the token is what stops it typing."""
    body = json.dumps({"action": "type", "text": "pwned"}).encode()
    code, _ = raw(sim, "/api/act", {"Content-Type": "text/plain"}, method="POST", data=body)
    assert code == 401
    assert sim.desk.text == ""


def test_status(sim):
    st = sim.client.status()
    assert st["service"] == "nanokvm" and st["simulated"] is True
    assert st["capture"]["state"] == "streaming"
    assert st["screen"]["width"] == 1280 and st["screen"]["height"] == 720


def test_click_lands_on_button(sim):
    bx, by, bw, bh = BUTTON_RECT                   # desktop px; the coordinate space is 2/3 scale
    x, y = (bx + bw / 2) * 2 / 3, (by + bh / 2) * 2 / 3
    sim.client.act({"action": "click", "x": x, "y": y})
    assert sim.desk.button_presses == 1
    cx, cy, _ = sim.desk.clicks[-1]
    assert abs(cx - (bx + bw / 2)) <= 2 and abs(cy - (by + bh / 2)) <= 2


def test_click_outside_screen_is_rejected(sim):
    with pytest.raises(ServiceError, match="outside the screen"):
        sim.client.act({"action": "click", "x": 1280, "y": 10})


def test_type_and_keys(sim):
    r = sim.client.act({"action": "type", "text": "Hello, World! 123"})
    assert "typed 17" in r["summary"]
    sim.client.act({"action": "keys", "keys": "enter"})
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


def test_release_all(sim):
    r = sim.client.act({"action": "release_all"})
    assert "released" in r["summary"]


def test_manual_region_maps_clicks_into_a_pillarbox(sim_factory):
    env = sim_factory(bars=(96, 0), region="96,0,1728,1080")     # GPU pillarbox, set in nanokvm.toml
    screen = env.client.status()["screen"]
    assert (screen["width"], screen["height"]) == (1280, 800) and screen["region_source"].startswith("manual")
    bx, by, bw, bh = BUTTON_RECT
    scale = 1280 / 1728
    env.client.act({"action": "click", "x": (bx + bw / 2) * scale, "y": (by + bh / 2) * scale})
    assert env.desk.button_presses == 1


def test_an_existing_calibration_file_still_sets_the_region(tmp_path):
    # There's no calibrate tool any more, but a machine that has a calibration file must keep the same
    # coordinate space, so clicks and --region don't shift.
    from conftest import SimEnv
    from nanokvm_mcp.config import Config
    run_dir = Config(home=tmp_path).run_dir
    run_dir.mkdir(parents=True)
    (run_dir / "calibration.json").write_text(json.dumps(
        {"frame_size": [1920, 1080], "region": {"x": 96, "y": 0, "w": 1728, "h": 1080}, "method": "cursor"}),
        encoding="utf-8")
    env = SimEnv(tmp_path, bars=(96, 0))
    try:
        screen = env.client.status()["screen"]
        assert (screen["width"], screen["height"]) == (1280, 800)
        assert screen["region_source"] == "calibration (cursor)"
    finally:
        env.close()


def test_view_only_mode(sim_factory):
    env = sim_factory(control=False)
    with pytest.raises(ServiceError, match="disabled"):
        env.client.act({"action": "click", "x": 1, "y": 1})
    assert env.client.status()["control"]["enabled"] is False


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


def test_stream_sends_jpeg_parts_with_the_token_in_a_header_only(sim):
    assert raw(sim, f"/stream.mjpg?t={sim.token}")[0] == 401
    req = urllib.request.Request(f"{sim.cfg.base_url}/stream.mjpg", headers={"X-NanoKVM-Token": sim.token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=5) as r:
        assert r.headers["Content-Type"].startswith("multipart/x-mixed-replace")
        chunk = r.read(4096)
        assert b"--frame" in chunk and b"\xff\xd8" in chunk


def test_screen_sharing_gates_frames_per_client_id(sim):
    from nanokvm_mcp.client import ServiceClient
    caller = ServiceClient(sim.cfg, client_id="program-A")
    other = ServiceClient(sim.cfg, client_id="program-B")
    with pytest.raises(ServiceError, match="sharing is off"):
        caller.frame()
    r = caller.share(5)
    assert r["sharing"] and r["minutes"] == 5
    assert caller.frame()["width"] == 1920
    assert caller.status()["sharing"]["active"] is True
    with pytest.raises(ServiceError, match="sharing is off"):      # sharing is per client id
        other.frame()
    assert caller.unshare()["was_sharing"] is True
    with pytest.raises(ServiceError, match="sharing is off"):
        caller.frame()


def test_share_expires(sim):
    from nanokvm_mcp.client import ServiceClient
    caller = ServiceClient(sim.cfg, client_id="program-A")
    caller.share(1)
    sim.service._shares["program-A"] -= 61                          # pretend a minute passed
    with pytest.raises(ServiceError, match="sharing is off"):
        caller.frame()
    assert sim.client.status()["shares_active"] == 0                 # and it no longer counts
