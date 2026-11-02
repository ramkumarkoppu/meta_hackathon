"""The live view window (nanokvm viewer) and the stream it plays, against the simulated locked computer."""
import argparse
import gc
import io
import tempfile
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from conftest import SimEnv, free_port
from nanokvm_mcp import cli
from nanokvm_mcp import geometry as G
from nanokvm_mcp import service as S
from nanokvm_mcp import viewer as V
from nanokvm_mcp.client import ServiceClient, ServiceError, ServiceUnreachable
from nanokvm_mcp.config import Config
from nanokvm_mcp.hid import SerialLink
from nanokvm_mcp.pipeline import Pipeline
from nanokvm_mcp.simulator import SimCapture, SimDesktop

PICTURE_SUFFIXES = (".jpg", ".jpeg", ".png", ".ppm", ".bmp")


@pytest.fixture(autouse=True)
def _free_tk_on_the_main_thread():
    """Collect any leftover Tk objects here: freed by garbage collection on another thread, Tcl aborts."""
    yield
    gc.collect()


def _part(body, extra=b""):
    return (b"--frame\r\nContent-Type: image/jpeg\r\n" + extra + b"Content-Length: %d\r\n\r\n" % len(body)
            + body + b"\r\n")


def _wait(condition, timeout=10.0, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def _wait_picture(feed, size=None, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pic = feed.take()
        if pic is not None and (size is None or (pic.width, pic.height) == size):
            return pic
        time.sleep(0.02)
    raise AssertionError(f"no picture (feed {feed.state}: {feed.error})")


def _window(sim, **kw):
    tk = pytest.importorskip("tkinter")
    try:
        win = V.ViewerWindow(sim.client, fps=30, **kw)
    except tk.TclError as e:
        pytest.skip(f"Tk cannot open a window here: {e}")
    win.root.withdraw()                       # don't pop up on the screen while the tests run
    return win


# ---------------------------------------------------------------- the stream
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


def test_stream_marks_the_no_video_picture(sim):
    resp = sim.client.open_stream(fps=30)
    try:
        parts = V.read_parts(resp)
        first, _ = next(parts)
        assert "x-frame-seq" in first and "x-frame-placeholder" not in first
        sim.capture.close()                   # no more frames: the service sends its "no video" picture
        for headers, _body in parts:
            if headers.get("x-frame-placeholder") == "1":
                assert "x-frame-seq" not in headers
                break
    finally:
        resp.close()


def test_stream_needs_a_share_for_a_client_with_an_id(sim):
    caller = ServiceClient(sim.cfg, client_id="program-A")
    with pytest.raises(ServiceError) as refused:
        caller.open_stream(fps=30)            # refused on the client side at once
    assert refused.value.status == 403
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(f"{sim.cfg.base_url}/stream.mjpg",
                                 headers={"X-NanoKVM-Token": sim.token, "X-NanoKVM-Client": "program-A"})
    with pytest.raises(urllib.error.HTTPError) as refused:
        opener.open(req, timeout=5)           # and by the service
    assert refused.value.code == 403
    caller.share(5)
    with opener.open(req, timeout=5) as r:    # with an approved share it streams
        assert r.headers["Content-Type"].startswith("multipart/x-mixed-replace")


def test_open_stream_says_when_the_service_is_not_running(tmp_path):
    cl = ServiceClient(Config(home=tmp_path, port=free_port()))
    with pytest.raises(ServiceUnreachable):
        cl.open_stream(fps=30)


def test_viewers_share_one_encode_per_frame(sim, monkeypatch):
    encodes = []
    real = G.encode_jpeg

    def counting(img, quality=80, full_chroma=True):
        encodes.append(1)
        return real(img, quality, full_chroma)
    monkeypatch.setattr(G, "encode_jpeg", counting)
    seen: list[list[int]] = []

    def watch():
        resp = sim.client.open_stream(fps=30)
        got = []
        try:
            for headers, _body in V.read_parts(resp):
                got.append(int(headers["x-frame-seq"]))
                if len(got) == 15:
                    break
        finally:
            resp.close()
            seen.append(got)
    threads = [threading.Thread(target=watch) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    distinct = len(set(seen[0]) | set(seen[1]))
    assert len(encodes) <= distinct + 2, (len(encodes), distinct)   # two viewers, still about one encode a frame


# ---------------------------------------------------------------- status and shares
def test_status_counts_active_shares(sim):
    def count():
        return sim.client.status()["shares_active"]
    assert count() == 0
    other = ServiceClient(sim.cfg, client_id="program-A")
    other.share(5)
    assert count() == 1
    with Pipeline(sim.cfg, approve_share=lambda m: True) as pipe:   # what nanokvm read/ask opens
        pipe.open(5)
        assert count() == 2
        other.unshare()
        assert count() == 1
    assert count() == 0


class _ClosedLink:
    """A keyboard/mouse link whose port is closed: any write or transact would reach the locked computer."""
    is_open = False

    def __init__(self) -> None:
        self.calls: list[str] = []

    def write(self, frame: bytes) -> None:
        self.calls.append("write")

    def transact(self, frame: bytes, timeout: float = 0.5):
        self.calls.append("transact")

    def describe(self) -> str:
        return "not open"

    def close(self) -> None:
        pass


def test_status_never_opens_the_keyboard_mouse_link(tmp_path):
    cfg = Config(home=tmp_path, port=free_port(), simulate=True, idle_exit_min=0)
    link = _ClosedLink()
    svc = S.Service(cfg, SimCapture(SimDesktop(1920, 1080), fps=30), lambda: link)
    try:
        assert svc.capture.wait_frame(0, timeout=5) is not None
        with pytest.raises(S.ServiceError):
            svc.act({"action": "click", "x": 99999, "y": 5})   # refused, after making the keyboard/mouse object
        assert svc._hid is not None
        for _ in range(3):                                      # the viewer polls about once a second
            st = svc.status()
        assert link.calls == []                                 # no open, no all-keys-up, no GET_INFO
        assert st["control"]["state"].startswith("not open")
        assert SerialLink(None, [(0x1A86, 0x55D3)]).is_open is False
    finally:
        svc.capture.close()


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
        stats.record(V.Picture(b"", 1920, 1080, (1920, 1080), i + 1, time.time() - 0.05))
    feed = SimpleNamespace(state="streaming", error=None, unreachable=False)
    text, colour = V.video_line(_status(), None, feed, stats)
    assert text.startswith("Video: 1920x1080 MJPG") and "capture 30 fps" in text and "delay" in text
    assert colour == V.OK_FG and "shown at" not in text
    scaled = V.ViewStats()
    scaled.record(V.Picture(b"", 1440, 810, (1920, 1080), 1, time.time()))
    assert "shown at 75% (press 1 for actual pixels)" in V.video_line(_status(), None, feed, scaled)[0]
    blank = _status(capture={**_status()["capture"], "blank": True})
    text, colour = V.video_line(blank, None, feed, stats)
    assert "uniform" in text and colour == V.WARN_FG
    lost = _status(capture={"state": "no_frames", "error": "no video for 6s"})
    assert V.video_line(lost, None, feed, stats) == ("No video (no_frames): no video for 6s", V.BAD_FG)
    text, colour = V.video_line(None, None, feed, stats)               # first status answer not in yet
    assert "waiting for the service status" in text and "None" not in text and colour == V.FG
    old = _status()
    del old["shares_active"]                                           # a service started from older code
    down = SimpleNamespace(state="reconnecting", error="cannot reach the NanoKVM service", unreachable=True)
    assert "press R" in V.video_line(None, None, down, stats)[0]

    text, colour = V.other_line(_status(), ("COM5", True))
    assert "COM5 found" in text and "Screen sharing: off" in text and colour == V.FG
    text, colour = V.other_line(_status(shares_active=1), (None, False))
    assert "COM port not found" in text and "Screen sharing: Muse Glimmer on this laptop can read it" in text
    assert colour == V.FG
    assert "COM9 (serial_port in nanokvm.toml) is not present" in V.other_line(_status(), ("COM9", False))[0]
    assert "Keyboard/mouse: simulated" in V.other_line(_status(simulated=True), (None, False))[0]
    opened = _status(control={"enabled": True, "link": "COM5 @ 57600 baud", "state": "ok",
                              "device_info": {"chip_version": "V1.8", "target_usb_connected": False}})
    assert "COM5 @ 57600 baud, chip V1.8, locked computer USB NOT connected" in V.other_line(opened, ("COM5", True))[0]
    assert "Screen sharing: ?" in V.other_line(old, ("COM5", True))[0]
    assert "Screen sharing: ?" in V.other_line(None, (None, False))[0]


def test_stats_count_frames_from_an_older_service_and_no_video_pictures():
    stats = V.ViewStats()
    stats.record(V.Picture(b"", 1, 1, (1920, 1080), None, None))          # older service: no frame headers
    stats.record(V.Picture(b"", 1, 1, (1920, 1080), None, None, True))    # the "no video" picture
    assert (stats.shown, stats.placeholders) == (2, 1) and stats.seconds_since_new() is not None
    assert "1 frames shown" in stats.summary() and "1 'no video' pictures" in stats.summary()


def test_a_configured_com_port_must_exist(monkeypatch, tmp_path):
    import nanokvm_mcp.hid as H
    monkeypatch.setattr(H, "list_serial_ports", lambda: [
        {"device": "COM5", "description": "USB-Enhanced-SERIAL CH343", "hwid": "", "vid": 0x1A86, "pid": 0x55D3}])
    cfg = Config(home=tmp_path, serial_ids="1A86:55D3")
    assert V.find_serial_port(cfg) == ("COM5", True)
    cfg.serial_port = "COM9"
    assert V.find_serial_port(cfg) == ("COM9", False)
    cfg.serial_port = "com5"
    assert V.find_serial_port(cfg) == ("com5", True)


# ---------------------------------------------------------------- the feed and the window
def test_feed_scales_each_frame_for_the_window(sim):
    feed = V.LiveFeed(sim.client, fps=30)
    feed.view = ("fit", 640, 640)
    feed.start()
    try:
        pic = _wait_picture(feed, (640, 360))
        assert pic.frame_size == (1920, 1080) and pic.seq is not None and pic.captured is not None
        assert not pic.placeholder
        header = b"P6\n640 360\n255\n"
        assert pic.ppm.startswith(header) and len(pic.ppm) == len(header) + 640 * 360 * 3
        feed.view = ("actual", 640, 640)                             # 1 = actual pixels
        _wait_picture(feed, (1920, 1080))
    finally:
        feed.stop(wait=2)


def test_feed_reconnects_after_the_service_comes_back(tmp_path):
    first = SimEnv(tmp_path / "a")
    client = ServiceClient(first.cfg)
    feed = V.LiveFeed(client, fps=30).start()
    second = None
    try:
        _wait_picture(feed)
        first.close()                                                # the service stops
        _wait(lambda: feed.state == "reconnecting" and feed.unreachable, what="the feed to notice")
        assert "press R" in V.video_line(None, None, feed, V.ViewStats())[0]
        assert feed.reconnects == 0                                  # a lost stream is not a reconnection
        second = SimEnv(tmp_path / "b")                              # it comes back (here on another port)
        client.cfg = second.cfg
        feed.retry_now()
        _wait(lambda: feed.state == "streaming", what="the stream to come back")
        _wait_picture(feed)
        assert feed.reconnects == 1
    finally:
        feed.stop(wait=2)
        if second is not None:
            second.close()


def test_window_shows_the_screen_and_stays_read_only(sim, tmp_path, monkeypatch):
    import serial
    requests = []
    real_get = S.Handler.do_GET

    def recording_get(handler):
        requests.append((handler.path.split("?")[0], handler.headers.get("X-NanoKVM-Client")))
        return real_get(handler)
    monkeypatch.setattr(S.Handler, "do_GET", recording_get)

    def no_serial(*_a, **_k):
        raise AssertionError("the viewer opened a serial port")
    monkeypatch.setattr(serial, "Serial", no_serial)
    for sub in ("cwd", "temp"):
        (tmp_path / sub).mkdir()
    monkeypatch.chdir(tmp_path / "cwd")                  # catch a frame written to a relative path...
    for var in ("TEMP", "TMP"):                          # ...or to the temp folder
        monkeypatch.setenv(var, str(tmp_path / "temp"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "temp"))

    win = _window(sim, close_after=2.0)
    win.run()
    win.feed.stop(wait=2)
    win.poller.stop(wait=2)
    assert win.stats.shown - win.stats.placeholders >= 10, win.summary()
    assert win.stats.delays_ms and max(win.stats.delays_ms) < 2000
    assert "frames shown" in win.summary()
    assert {path for path, _ in requests} == {"/stream.mjpg", "/api/status"}
    assert {client for _, client in requests} == {None}  # never a client id: the user's own view, never gated
    assert sim.service._hid is None                      # the keyboard/mouse link was never opened
    assert sim.desk.text == "" and sim.desk.button_presses == 0
    assert [p for p in tmp_path.rglob("*") if p.suffix.lower() in PICTURE_SUFFIXES] == []   # nothing on disk


def test_window_follows_resizing_and_actual_size(sim):
    win = _window(sim)
    try:
        win._on_resize(SimpleNamespace(width=800, height=450))
        assert win.feed.view == ("fit", 800, 450)
        win.feed.start()
        _wait_picture(win.feed, (800, 450))
        win.toggle_actual_size()
        assert win.mode == "actual" and win.feed.view[0] == "actual"
        _wait_picture(win.feed, (1920, 1080))
        win.toggle_actual_size()
        assert win.mode == "fit" and win.feed.view == ("fit",) + win.feed.view[1:]
        win.set_fullscreen(True)                  # the picture alone: actual size on a 1920x1080 screen
        assert win.bar.winfo_manager() == ""
        win.set_fullscreen(False)                 # Esc: the status lines come back, under the picture
        assert win.bar.winfo_manager() == "pack" and win.root.pack_slaves().index(win.bar) == 0
    finally:
        win.close()
        win.feed.stop(wait=2)


def test_r_starts_the_service_once_and_says_why_it_failed(sim, monkeypatch):
    win = _window(sim)
    calls = []
    release = threading.Event()

    def failing_start():
        calls.append(1)
        release.wait(5)
        raise ServiceError("the NanoKVM service did not start within 25s. Last lines of x\\service.log:\nBOOM")
    monkeypatch.setattr(win.client, "ensure_running", failing_start)
    down = SimpleNamespace(state="reconnecting", error="cannot reach the NanoKVM service", unreachable=True)
    try:
        for _ in range(5):                    # a held R key repeats
            win.start_service()
        assert win.starting
        assert V.video_line(None, None, down, win.stats, win.starting, win.start_error) == \
            ("Starting the NanoKVM service…", V.FG)
        release.set()
        win._starter.join(5)
        assert calls == [1]
        text, colour = V.video_line(None, None, down, win.stats, win.starting, win.start_error)
        assert text == ("Couldn't start the NanoKVM service: the NanoKVM service did not start within 25s "
                        "– press R to try again") and colour == V.BAD_FG
    finally:
        release.set()
        win.close()


class _FakeSession:
    """Stands in for assist.AssistSession: records what the panel asks of it."""
    def __init__(self, cfg, post, **_kw):
        self.cfg, self.post, self.calls, self.answers, self.busy = cfg, post, [], [], False

    def ask(self, *a):
        self.calls.append(("ask",) + a)
        return True

    def do(self, *a):
        self.calls.append(("do",) + a)
        return True

    def answer(self, approval, yes):
        self.answers.append((approval.kind, yes))

    def stop(self):
        self.calls.append(("stop",))

    def close(self):
        self.calls.append(("close",))

    def shared_minutes_left(self):
        return 0


def test_a_fix_it_target_is_outlined_on_the_picture_and_fix_it_plans_here_by_default(sim, monkeypatch):
    monkeypatch.setattr(V, "AssistSession", _FakeSession)
    win = _window(sim)
    try:
        win.photo = win.tk.PhotoImage(width=400, height=200)
        win.canvas.coords(win.image_item, 10, 20)
        win.panel.events.put(("target", (100, 200, 300, 400)))
        win.panel.poll()
        assert win.canvas.itemcget(win.target_item, "state") == "normal"
        assert [round(c) for c in win.canvas.coords(win.target_item)] == [10 + 40 - 3, 20 + 40 - 3, 10 + 120 + 3,
                                                                           20 + 80 + 3]
        win.panel.events.put(("idle", None))                 # the work is over: the outline goes
        win.panel.poll()
        assert win.canvas.itemcget(win.target_item, "state") == "hidden"
        p = win.panel
        p.prompt.insert("1.0", "copy it into the other folder")
        p.submit_do()                                        # planned on this laptop unless Spark is ticked
        assert p.session.calls[-1] == ("do", "copy it into the other folder", False, False, False)
        p.spark_var.set(True)
        p.local_var.set(True)                                # Local only wins: still planned here
        p.refresh()
        assert p.spark_box["state"] == "disabled"
        p.prompt.insert("1.0", "and again")
        p.submit_do()
        assert p.session.calls[-1] == ("do", "and again", False, False, False)
        p.local_var.set(False)
        p.refresh()
        p.prompt.insert("1.0", "with Spark")
        p.submit_do()
        assert p.session.calls[-1] == ("do", "with Spark", False, True, False)
    finally:
        win.close()


def test_the_prompt_box_asks_with_the_choices_made_and_letters_stay_typing(sim, monkeypatch):
    from nanokvm_mcp.assist import Approval
    monkeypatch.setattr(V, "AssistSession", _FakeSession)
    win = _window(sim)
    p = win.panel
    try:
        assert p.session is None and p.ask_btn["state"] == "normal"            # nothing happens until a question
        p.prompt.insert("1.0", "why does it crash?")
        p.kind_var.set("code")
        p.local_var.set(True)
        p.refresh()
        assert not hasattr(p, "fix_btn")                     # one button for tasks: Fix it hands code to the fix loop
        p.submit_ask()
        assert p.session.calls == [("ask", "why does it crash?", "code", True, False)]
        assert p.prompt.get("1.0", "end").strip() == ""
        p.local_var.set(False)
        p.show_var.set(True)
        p.prompt.insert("1.0", "and now?")
        p.submit_ask()
        assert p.session.calls[-1] == ("ask", "and now?", "code", False, True)

        # an approval: shown in the panel, its buttons work only after a moment
        a = Approval("edit", "Type Spark's edit of line 6?", "-    6 x\n+    6 y", "Type this edit", "Don't type",
                     mono=True)
        p.events.put(("approve", a))
        p.poll()
        assert p.card.winfo_manager() == "pack" and p.yes_btn["state"] == "disabled"
        assert p.yes_btn["text"] == "Type this edit" and "+    6 y" in p.card_text.get("1.0", "end")
        p._arm(a)
        assert p.yes_btn["state"] == "normal"
        p.decide(True)
        assert p.session.answers == [("edit", True)] and p.card.winfo_manager() == ""
        assert "You: Type this edit" in p.log.get("1.0", "end")
        p.events.put(("report", [("- line 6: verified (line 6 replaced)", ""), ("Saved on the locked computer: ok.",
                                                                                  "ok")]))
        p.events.put(("idle", None))
        p.poll()
        assert "Saved on the locked computer" in p.log.get("1.0", "end")
        assert "ok" in p.log.tag_names(p.log.search("Saved on the locked", "1.0"))

        # an approval waiting while the panel is hidden: shown again, and its buttons wait again before working
        long = Approval("send", "Send exactly this to Muse Spark (Meta)?", "\n".join(f"{n} line" for n in range(60)),
                        "Send", "Don't send", mono=True)
        win.toggle_panel()
        assert p.frame.winfo_manager() == ""
        p.events.put(("approve", long))
        p.poll()                                             # an approval shows the panel again
        assert p.frame.winfo_manager() == "pack" and "(scroll to read all of it)" in p.card_title["text"]
        win.toggle_panel()                                   # hidden while it waits: arming does nothing
        p._arm(long)
        assert p.yes_btn["state"] == "disabled"
        win.toggle_panel()
        assert p.yes_btn["state"] == "disabled"              # shown again: still a moment before it works
        p._arm(long)
        assert p.yes_btn["state"] == "normal"
        p.events.put(("idle", None))                         # Stop answered it no: the card goes
        p.poll()
        assert p.approval is None and p.card.winfo_manager() == "" and p.yes_btn["state"] == "disabled"
        win.set_fullscreen(True)
        p.events.put(("approve", a))                         # an approval in full screen leaves full screen
        p.poll()
        assert not win.root.attributes("-fullscreen") and p.frame.winfo_manager() == "pack"
        p.decide(False)

        # a hidden prompt box can't send: the focus leaves it, and Ask does nothing
        p.prompt.focus_set()
        p.prompt.insert("1.0", "typed blind")
        win.toggle_panel()
        assert win.root.focus_get() is not p.prompt
        calls = len(p.session.calls)
        p.submit_ask()
        assert len(p.session.calls) == calls
        win.toggle_panel()
        p.prompt.delete("1.0", "end")

        # letter keys are the window's only while the picture, not a text box, has the focus
        called = []
        handler = win._unless_typing(lambda: called.append(1))
        monkeypatch.setattr(win.root, "focus_get", lambda: p.prompt)
        handler(None)
        assert called == []
        monkeypatch.setattr(win.root, "focus_get", lambda: win.canvas)
        handler(None)
        assert called == [1]

        win.toggle_panel()                                                    # F2
        assert p.frame.winfo_manager() == ""
        win.toggle_panel()
        assert p.frame.winfo_manager() == "pack"
        win.set_fullscreen(True)                                              # the picture alone
        assert p.frame.winfo_manager() == "" and win.bar.winfo_manager() == ""
        win.set_fullscreen(False)
        assert p.frame.winfo_manager() == "pack" and win.root.pack_slaves().index(win.bar) == 0
    finally:
        win.close()
    assert p.session.calls[-1] == ("close",)                                  # closing the window ends its work


def test_the_prompt_box_follows_stricter_settings(sim, monkeypatch):
    monkeypatch.setattr(V, "AssistSession", _FakeSession)
    sim.client.cfg.outbound = "local_only"
    win = _window(sim)
    try:
        p = win.panel
        assert p.local_var.get() and p.local_box["state"] == "disabled"
        assert p.spark_box["state"] == "disabled" and p.do_btn["state"] == "normal"   # Fix it: planned here only
    finally:
        win.close()
        sim.client.cfg.outbound = "auto"
    win = _window(sim)
    try:                                                     # while work runs, its choices are fixed
        p = win.panel
        p.session = _FakeSession(p.cfg, p.events.put)
        p.session.busy = True
        p.refresh()
        assert p.local_box["state"] == p.show_box["state"] == p.spark_box["state"] == "disabled"
        p.session.busy = False
        p.refresh()
        assert p.local_box["state"] == p.show_box["state"] == p.spark_box["state"] == "normal"
        p.submit_do()                                        # nothing typed: a hint, not the last question
        assert p.session.calls == [] and "Type the task in the box first" in p.log.get("1.0", "end")
    finally:
        win.close()
    sim.client.cfg.control = False
    win = _window(sim)
    try:
        assert win.panel.do_btn["state"] == "disabled"
        assert "Fix it is off: keyboard/mouse control is off" in win.panel.status["text"]
        assert win.panel.show_box["state"] == "normal"
    finally:
        win.close()
        sim.client.cfg.control = True


def test_ctrl_c_closes_the_window_and_still_prints_the_summary(sim, monkeypatch, capsys):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError as e:
        pytest.skip(f"Tk cannot open a window here: {e}")

    def interrupted(self):                    # mainloop() raising KeyboardInterrupt, as Ctrl+C usually does
        self.root.withdraw()
        raise KeyboardInterrupt
    monkeypatch.setattr(V.ViewerWindow, "run", interrupted)
    assert V.run_viewer(sim.client, fps=30) == 130
    assert "Live view closed after" in capsys.readouterr().out


def test_viewer_command_modes(sim, monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(V, "run_viewer", lambda cl, fps, close_after: ran.append((fps, close_after)) or 0)
    assert cli.cmd_status(sim.client, argparse.Namespace(json=False)) == 0
    status = capsys.readouterr().out
    assert sim.token not in status                                # status never shows the token
    assert "Sharing:   0 active share(s) (nanokvm read, ask, edit or fix, or the viewer's prompt box)" in status
    assert cli.cmd_viewer(sim.client, argparse.Namespace(fps=12, close_after=3.0)) == 0
    assert ran == [(12, 3.0)]
    for bad in (["--fps", "0"], ["--close-after", "-1"], ["--browser"]):
        with pytest.raises(SystemExit):
            cli.main(["viewer", *bad])


def test_keytest_checks_that_num_lock_follows_each_press(monkeypatch, capsys):
    monkeypatch.setattr(cli, "KEYTEST_WAIT_S", 0)

    class Client:                                      # the locked computer's Num Lock, as the chip reports it
        def __init__(self, follows):
            self.follows, self.num, self.acts = follows, True, []

        def act(self, body):
            self.acts.append(body)
            if body["action"] == "keys" and self.follows:
                self.num = not self.num
            return {}

        def status(self):
            return {"control": {"device_info": {"num_lock": self.num}}}
    ok = Client(True)
    assert cli.cmd_keytest(ok, None) == 0 and "Keyboard OK" in capsys.readouterr().out
    assert [a["action"] for a in ok.acts] == ["release_all", "keys", "keys"] and ok.num is True
    assert cli.cmd_keytest(Client(False), None) == 1 and "isn't taking keystrokes" in capsys.readouterr().out


def test_stop_says_whether_the_service_stopped(capsys):
    class Client:
        def __init__(self, running, stops):
            self.running, self.stops = running, stops

        def is_running(self):
            return self.running

        def stop(self):
            return self.stops
    assert cli.cmd_stop(Client(False, False), None) == 0 and "was not running" in capsys.readouterr().out
    assert cli.cmd_stop(Client(True, True), None) == 0 and "service stopped" in capsys.readouterr().out
    assert cli.cmd_stop(Client(True, False), None) == 1 and "did not stop within 20 s" in capsys.readouterr().out


def test_the_live_view_forgets_its_last_picture_when_nobody_watches(sim):
    resp = sim.client.open_stream(fps=30)
    try:
        next(V.read_parts(resp))
        assert sim.service._viewer_cache[1]
    finally:
        resp.close()
    _wait(lambda: sim.service.viewers == 0, what="the stream to end")
    assert sim.service._viewer_cache == (0, b"")
