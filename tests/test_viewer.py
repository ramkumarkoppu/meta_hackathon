"""The live view window (nanokvm viewer) and the stream it plays, against the simulated locked computer."""
import argparse
import io
import time
from types import SimpleNamespace

import pytest

from conftest import free_port
from nanokvm_mcp import cli
from nanokvm_mcp import viewer as V
from nanokvm_mcp.client import ServiceClient, ServiceUnreachable
from nanokvm_mcp.config import Config


def _part(body, extra=b""):
    return (b"--frame\r\nContent-Type: image/jpeg\r\n" + extra + b"Content-Length: %d\r\n\r\n" % len(body)
            + body + b"\r\n")


def test_read_parts_reads_the_service_format():
    data = _part(b"\xff\xd8one", b"X-Frame-Seq: 7\r\nX-Frame-Time: 12.5\r\n") + _part(b"\xff\xd8two")
    parts = list(V.read_parts(io.BytesIO(data)))
    assert [body for _, body in parts] == [b"\xff\xd8one", b"\xff\xd8two"]
    assert parts[0][0]["x-frame-seq"] == "7" and parts[0][0]["x-frame-time"] == "12.5"
    assert "x-frame-seq" not in parts[1][0]


def test_read_parts_stops_at_a_cut_and_refuses_bad_lengths():
    whole = _part(b"\xff\xd8" + b"x" * 100)
    assert list(V.read_parts(io.BytesIO(whole[:-40]))) == []          # the stream ended inside a part
    with pytest.raises(ValueError, match="no Content-Length"):
        list(V.read_parts(io.BytesIO(b"--frame\r\nContent-Type: image/jpeg\r\n\r\nabc")))
    with pytest.raises(ValueError, match="impossible"):
        list(V.read_parts(io.BytesIO(b"--frame\r\nContent-Length: 0\r\n\r\n")))


def test_fit_size_keeps_the_aspect_ratio():
    assert V.fit_size(1920, 1080, 1280, 1000) == (1280, 720)
    assert V.fit_size(1920, 1080, 2000, 720) == (1280, 720)
    assert V.fit_size(1920, 1080, 1, 1) == (1920, 1080)              # window size not known yet
    w, h = V.fit_size(1920, 1080, 1001, 563)
    assert w <= 1001 and h <= 563


def test_stream_sends_capture_times_and_honours_fps(sim):
    resp = sim.client.open_stream(fps=4)
    parts = []
    try:
        t0 = time.monotonic()
        for headers, _body in V.read_parts(resp):
            parts.append(headers)
            if len(parts) == 4:
                break
        elapsed = time.monotonic() - t0
    finally:
        resp.close()
    seqs = [int(h["x-frame-seq"]) for h in parts]
    assert seqs == sorted(seqs) and len(set(seqs)) == 4
    assert all(abs(time.time() - float(h["x-frame-time"])) < 5 for h in parts)
    assert elapsed >= 0.6                     # 4 frames at 4 fps: three gaps of 0.25 s


def test_open_stream_says_when_the_service_is_not_running(tmp_path):
    cl = ServiceClient(Config(home=tmp_path, port=free_port()))
    with pytest.raises(ServiceUnreachable):
        cl.open_stream(fps=30)


def test_status_counts_active_shares(sim):
    assert sim.client.status()["shares_active"] == 0
    claude = ServiceClient(sim.cfg, client_id="conversation-A")
    claude.share(5)
    assert sim.client.status()["shares_active"] == 1
    claude.unshare()
    assert sim.client.status()["shares_active"] == 0


def _wait_picture(feed, size=None, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pic = feed.take()
        if pic is not None and (size is None or (pic.width, pic.height) == size):
            return pic
        time.sleep(0.02)
    raise AssertionError(f"no picture (feed {feed.state}: {feed.error})")


def test_feed_scales_each_frame_for_the_window(sim):
    feed = V.LiveFeed(sim.client, fps=30)
    feed.view = ("fit", 640, 640)
    feed.start()
    try:
        pic = _wait_picture(feed, (640, 360))
        assert pic.frame_size == (1920, 1080) and pic.seq is not None and pic.captured is not None
        header = b"P6\n640 360\n255\n"
        assert pic.ppm.startswith(header) and len(pic.ppm) == len(header) + 640 * 360 * 3
        feed.view = ("actual", 640, 640)                             # 1 = actual pixels
        _wait_picture(feed, (1920, 1080))
    finally:
        feed.stop(wait=2)


def _status(**over):
    st = {"simulated": False, "shares_active": 0,
          "capture": {"state": "streaming", "mode": "1920x1080 MJPG (requested 1920x1080@30)", "fps": 30.4,
                      "frame_size": [1920, 1080], "blank": False, "error": None},
          "control": {"enabled": True, "state": "not opened yet (opens on the first keyboard/mouse action)"}}
    st.update(over)
    return st


def test_status_lines_report_the_setup():
    stats = V.ViewStats()
    for i in range(3):
        stats.record(V.Picture(b"", 1, 1, (1920, 1080), i + 1, time.time() - 0.05))
    feed = SimpleNamespace(state="streaming", error=None, unreachable=False)
    text, colour = V.video_line(_status(), None, feed, stats)
    assert text.startswith("Video: 1920x1080 MJPG") and "capture 30 fps" in text and "delay" in text
    assert colour == V.OK_FG
    blank = _status(capture={**_status()["capture"], "blank": True})
    text, colour = V.video_line(blank, None, feed, stats)
    assert "uniform" in text and colour == V.WARN_FG
    lost = _status(capture={"state": "no_frames", "error": "no video for 6s"})
    assert V.video_line(lost, None, feed, stats) == ("No video (no_frames): no video for 6s", V.BAD_FG)
    down = SimpleNamespace(state="reconnecting", error="cannot reach the NanoKVM service", unreachable=True)
    assert "press R" in V.video_line(None, None, down, stats)[0]

    text, colour = V.other_line(_status(), "COM5")
    assert "COM5 found" in text and "Claude screen sharing: off" in text and colour == V.FG
    text, colour = V.other_line(_status(shares_active=1), None)
    assert "COM port not found" in text and "ON (1 conversation)" in text and colour == V.WARN_FG
    opened = _status(control={"enabled": True, "link": "COM5 @ 57600 baud", "state": "ok",
                              "device_info": {"chip_version": "V1.8", "target_usb_connected": False}})
    assert "COM5 @ 57600 baud, chip V1.8, locked computer USB NOT connected" in V.other_line(opened, "COM5")[0]
    assert "Claude screen sharing: ?" in V.other_line(_status(shares_active=None), "COM5")[0]   # older service


def test_window_shows_the_screen_and_stays_read_only(sim, tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        win = V.ViewerWindow(sim.client, fps=30, close_after=2.0)
    except tk.TclError as e:
        pytest.skip(f"Tk cannot open a window here: {e}")
    win.root.withdraw()                       # don't pop up on the screen while the tests run
    win.run()
    win.feed.stop(wait=2)
    win.poller.stop(wait=2)
    assert win.stats.shown - win.stats.placeholders >= 10, win.summary()
    assert win.stats.delays_ms and max(win.stats.delays_ms) < 2000
    assert "frames shown" in win.summary()
    assert sim.service._hid is None           # the keyboard/mouse link was never opened
    assert sim.desk.text == "" and sim.desk.button_presses == 0
    pictures = [p for p in tmp_path.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".ppm", ".bmp")]
    assert pictures == []                     # nothing written to disk


def test_viewer_command_modes(sim, monkeypatch, capsys):
    opened, ran = [], []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    monkeypatch.setattr(V, "run_viewer", lambda cl, fps, close_after: ran.append((fps, close_after)) or 0)

    def args(**kw):
        return argparse.Namespace(**{"browser": False, "no_open": False, "fps": 30, "close_after": None, **kw})

    assert cli.cmd_viewer(sim.client, args(no_open=True)) == 0
    assert "?t=" in capsys.readouterr().out and opened == [] and ran == []
    assert cli.cmd_viewer(sim.client, args(browser=True)) == 0
    assert len(opened) == 1 and ran == []
    assert cli.cmd_viewer(sim.client, args(fps=12, close_after=3.0)) == 0
    assert ran == [(12, 3.0)]
    with pytest.raises(SystemExit):
        cli.main(["viewer", "--fps", "0"])
