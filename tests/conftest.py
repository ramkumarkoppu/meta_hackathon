"""Shared fixtures: a real nanokvm service on a free local port, wired to the simulated locked computer (a
simulated desktop, serial link and HDMI capture). Every test that goes through the service starts here, so
nothing needs the USB KVM, a GPU or a key."""
import socket
import threading

import pytest

from nanokvm_mcp.client import ServiceClient
from nanokvm_mcp.config import Config
from nanokvm_mcp.service import Service, load_or_create_token, make_server
from nanokvm_mcp.simulator import SimCapture, SimDesktop, SimLink


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class SimEnv:
    """The service and its HTTP server running in this process on the simulated desktop, link and capture, with
    a ServiceClient that talks to it. Keys are held for a millisecond and clicks for five, so the tests run fast."""

    def __init__(self, tmp_path, bars=(0, 0), caps_lock=False, port=None, editor_lines=None, **cfg_overrides):
        cfg_overrides.setdefault("wake_after_s", 0)      # off: most tests count the exact keys sent (test_hid has it on)
        self.cfg = Config(home=tmp_path, port=port or free_port(), simulate=True, idle_exit_min=0,
                          key_hold_ms=1, key_gap_ms=1, click_hold_ms=5, **cfg_overrides)
        self.desk = SimDesktop(1920, 1080, bars=bars, editor_lines=editor_lines)
        self.link = SimLink(self.desk, caps_lock=caps_lock)
        self.capture = SimCapture(self.desk, fps=30)
        self.service = Service(self.cfg, self.capture, lambda: self.link)
        self.token = load_or_create_token(self.cfg.run_dir)
        self.server = make_server(self.cfg, self.service, self.token)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1},
                                       daemon=True)
        self.thread.start()
        self.client = ServiceClient(self.cfg)
        assert self.capture.wait_frame(0, timeout=5) is not None     # a frame is in before any test asks for one

    def close(self):
        self.server.stopping.set()
        self.server.shutdown()
        self.server.server_close()
        self.service.close()


@pytest.fixture
def sim(tmp_path):
    """One simulated setup with the default settings, closed after the test."""
    env = SimEnv(tmp_path)
    yield env
    env.close()


@pytest.fixture
def sim_factory(tmp_path):
    """Makes simulated setups with their own settings, each in its own folder. All are closed after the test."""
    envs = []

    def make(**kw):
        sub = tmp_path / f"env{len(envs)}"
        sub.mkdir()
        env = SimEnv(sub, **kw)
        envs.append(env)
        return env

    yield make
    for e in envs:
        e.close()
