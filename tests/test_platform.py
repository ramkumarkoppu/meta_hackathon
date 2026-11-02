"""Operating systems: the locked computer's OS is a setting (locked_os: windows, macos or linux);
capture needs a Windows AI computer."""
import argparse
import sys

import pytest

from nanokvm_mcp import capture, cli, hid
from nanokvm_mcp.client import ServiceError
from nanokvm_mcp.config import Config


def test_locked_os_defaults_to_windows(tmp_path):
    assert Config.load(home=tmp_path, env={}).locked_os_name == "windows"


@pytest.mark.parametrize("value,name", [("macos", "macos"), (" MacOS ", "macos"), ("LINUX", "linux")])
def test_locked_os_is_normalised(tmp_path, value, name):
    assert Config.load(home=tmp_path, env={"NANOKVM_LOCKED_OS": value}).locked_os_name == name


def test_unknown_locked_os_is_refused(tmp_path):
    cfg = Config.load(home=tmp_path, env={"NANOKVM_LOCKED_OS": "chromeos"})
    with pytest.raises(ValueError, match="windows, macos, linux"):
        cfg.locked_os_name


@pytest.mark.parametrize("os_,layout,warns", [("macos", "uk", True), ("macos", "us", False),
                                              ("windows", "uk", False), ("linux", "uk", False)])
def test_layout_warning_only_for_a_mac_with_the_pc_uk_layout(tmp_path, os_, layout, warns):
    cfg = Config.load(home=tmp_path, env={"NANOKVM_LOCKED_OS": os_, "NANOKVM_KEYBOARD_LAYOUT": layout})
    assert bool(cfg.layout_warning) is warns


def test_capture_explains_it_needs_a_windows_ai_computer(monkeypatch):
    # macOS and Linux would otherwise fail with an obscure OpenCV backend error
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="needs Windows on the AI computer"):
        capture.list_video_devices()


def _no_camera(*a, **k):
    raise AssertionError("opened a camera")


def test_capture_open_checks_the_os_first(monkeypatch):
    import cv2
    monkeypatch.setattr(cv2, "VideoCapture", _no_camera)   # never touch a real camera, even if the guard goes
    monkeypatch.setattr(sys, "platform", "darwin")
    cap = object.__new__(capture.HdmiCapture)             # no capture thread
    cap.index, cap.ids = 0, []                             # the video_index path, which skips enumeration
    with pytest.raises(RuntimeError, match="needs Windows on the AI computer"):
        cap._open()


class _NoService:
    def __init__(self, cfg):
        self.cfg = cfg

    def ensure_running(self):
        raise ServiceError("no service in this test")


def _diag(tmp_path, monkeypatch, capsys, env=None):
    monkeypatch.setattr(hid, "list_serial_ports", lambda: [])
    cfg = Config.load(home=tmp_path, env=env or {})
    rc = cli.cmd_diag(_NoService(cfg), argparse.Namespace(wait=0.0, mouse_test=False))
    return rc, capsys.readouterr().out


def test_diag_on_another_os_says_why(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    rc, out = _diag(tmp_path, monkeypatch, capsys)
    assert "needs Windows on the AI computer" in out
    assert "capture device not found" not in out and "Device Manager" not in out
    assert rc == 1


def test_diag_without_capture_devices(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(capture, "list_video_devices", lambda: [])
    rc, out = _diag(tmp_path, monkeypatch, capsys)
    assert rc == 1 and "NanoKVM capture device not found" in out


def test_diag_reports_an_invalid_locked_os(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(capture, "list_video_devices", lambda: [])
    rc, out = _diag(tmp_path, monkeypatch, capsys, env={"NANOKVM_LOCKED_OS": "mac"})
    assert rc == 1 and "locked_os must be one of windows, macos, linux, got 'mac'" in out
