"""Client for the shared NanoKVM service; starts the service in the background when needed."""
from __future__ import annotations

import http.client
import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never proxy localhost


class ServiceError(RuntimeError):
    """Problem reaching or using the service; the message is shown to Claude."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ServiceUnreachable(ServiceError):
    """Nothing is listening on the port (connection refused): the service is not running."""


class ServiceTimeout(ServiceError):
    """The service is running but did not answer in time."""


def _is_timeout(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason)


class ServiceClient:
    def __init__(self, cfg: Config, client_id: str | None = None) -> None:
        self.cfg = cfg
        self.client_id = client_id   # set by the MCP server: captures then need an approved screen share

    # ---- token / urls
    def _token(self) -> str:
        try:
            return (self.cfg.run_dir / "token").read_text(encoding="ascii").strip()
        except OSError:
            return ""

    def viewer_url(self) -> str:
        return f"{self.cfg.base_url}/?t={self._token()}"

    # ---- raw HTTP
    def request(self, method: str, path: str, body: dict | None = None, timeout: float = 15.0) -> dict:
        data = None if body is None else json.dumps(body).encode("utf-8")
        for attempt in (0, 1):
            req = urllib.request.Request(self.cfg.base_url + path, data=data, method=method)
            # read the token per attempt: a service that is just starting writes it moments before listening
            req.add_header("X-NanoKVM-Token", self._token())
            if self.client_id:
                req.add_header("X-NanoKVM-Client", self.client_id)
            if data is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with _NO_PROXY.open(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    time.sleep(0.2)
                    continue
                try:
                    msg = json.loads(e.read().decode("utf-8")).get("error", str(e))
                except Exception:  # noqa: BLE001
                    msg = str(e)
                raise ServiceError(msg, status=e.code) from None
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
                if _is_timeout(e):
                    raise ServiceTimeout(f"the NanoKVM service did not answer within {timeout:.0f}s "
                                         f"({method} {path})") from None
                reason = getattr(e, "reason", e)
                raise ServiceUnreachable(
                    f"cannot reach the NanoKVM service at {self.cfg.base_url}: {reason}") from None
            except (http.client.HTTPException, ValueError) as e:
                raise ServiceError(f"unexpected answer from {self.cfg.base_url}{path}: "
                                   f"{type(e).__name__}: {e}. Is another program using port "
                                   f"{self.cfg.port}? (set port in nanokvm.toml)") from None
        raise AssertionError("unreachable")

    def open_stream(self, fps: int, timeout: float = 5.0) -> http.client.HTTPResponse:
        """The live view's MJPEG stream (/stream.mjpg) at up to ``fps`` frames a second, for the viewer
        window. Like the browser live view it sends no client id. The caller reads the parts
        (viewer.read_parts) and closes the response; a read that waits longer than ``timeout`` s fails."""
        conn = http.client.HTTPConnection("127.0.0.1", self.cfg.port, timeout=timeout)
        try:
            conn.request("GET", f"/stream.mjpg?fps={int(fps)}", headers={"X-NanoKVM-Token": self._token()})
            resp = conn.getresponse()
        except ConnectionRefusedError:
            conn.close()
            raise ServiceUnreachable(f"cannot reach the NanoKVM service at {self.cfg.base_url}: "
                                     "connection refused (is it running?)") from None
        except (OSError, http.client.HTTPException) as e:
            conn.close()
            if _is_timeout(e):
                raise ServiceTimeout(f"the NanoKVM service did not answer within {timeout:.0f}s "
                                     "(GET /stream.mjpg)") from None
            raise ServiceError(f"cannot open the live stream at {self.cfg.base_url}: "
                               f"{type(e).__name__}: {e}") from None
        if resp.status != 200:
            try:
                msg = json.loads(resp.read(2000).decode("utf-8")).get("error", "")
            except Exception:  # noqa: BLE001
                msg = ""
            resp.close()
            conn.close()
            raise ServiceError(f"the live stream was refused (HTTP {resp.status}) {msg}".strip(),
                               status=resp.status)
        return resp

    # ---- lifecycle
    def is_running(self) -> bool:
        """True if our service answers (or is merely slow); False only when nothing listens."""
        try:
            st = self.request("GET", "/api/status", timeout=5.0)
        except ServiceUnreachable:
            return False
        except ServiceTimeout:
            return True
        except ServiceError as e:
            if e.status == 401:
                raise ServiceError(
                    f"a NanoKVM service on port {self.cfg.port} rejected the token in "
                    f"{self.cfg.run_dir / 'token'}; it was probably started from another folder. "
                    "Stop it (nanokvm stop there, or end its pythonw.exe process) and retry.") from None
            raise ServiceError(f"port {self.cfg.port} answers but not as the NanoKVM service ({e}); "
                               "set a different port in nanokvm.toml") from None
        if st.get("service") != "nanokvm":
            raise ServiceError(f"port {self.cfg.port} is used by another program; set port in nanokvm.toml")
        return True

    def ensure_running(self, start_timeout: float = 25.0) -> None:
        if self.is_running():
            return
        self.spawn()
        deadline = time.monotonic() + start_timeout
        while time.monotonic() < deadline:
            time.sleep(0.3)
            if self.is_running():
                return
        log_path = self.cfg.run_dir / "service.log"
        tail = ""
        try:
            tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-8:])
        except OSError:
            pass
        raise ServiceError(f"the NanoKVM service did not start within {start_timeout:.0f}s. "
                           f"Last lines of {log_path}:\n{tail}")

    def spawn(self) -> None:
        """Start ``python -m nanokvm_mcp.service`` detached, with no console window."""
        self.cfg.run_dir.mkdir(parents=True, exist_ok=True)
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe")
        cmd = [str(pyw if pyw.exists() else exe), "-m", "nanokvm_mcp.service"]
        env = dict(os.environ)
        env["NANOKVM_HOME"] = str(self.cfg.home)
        out_path = self.cfg.run_dir / "service.stdout.log"
        if out_path.exists() and out_path.stat().st_size > 1_000_000:
            out_path.unlink(missing_ok=True)
        out = open(out_path, "ab")  # noqa: SIM115
        kwargs: dict = dict(stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                            cwd=str(self.cfg.home), env=env, close_fds=True)
        try:
            if os.name == "nt":
                base = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                try:  # survive the MCP server's job object if the host allows breakaway
                    subprocess.Popen(cmd, creationflags=base | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
                except OSError:
                    subprocess.Popen(cmd, creationflags=base, **kwargs)
            else:
                subprocess.Popen(cmd, start_new_session=True, **kwargs)
        finally:
            out.close()
        log.info("started NanoKVM service: %s", " ".join(cmd))

    def stop(self, wait_s: float = 20.0) -> bool:
        """Ask the service to exit; returns once the camera and COM port are released."""
        if not self.is_running():
            return False
        try:
            self.request("POST", "/api/shutdown", {}, timeout=5.0)
        except (ServiceUnreachable, ServiceTimeout):
            pass
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            time.sleep(0.2)
            if not self.is_running():      # the listener closes only after the devices are released
                return True
        return False

    # ---- API wrappers (all auto-start the service)
    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 15.0) -> dict:
        try:
            return self.request(method, path, body, timeout)
        except ServiceUnreachable:
            self.ensure_running()
            return self.request(method, path, body, timeout)

    def status(self) -> dict:
        return self.call("GET", "/api/status")

    def screenshot(self) -> dict:
        return self.call("GET", "/api/screenshot", timeout=25.0)

    def zoom(self, x: float, y: float, w: float, h: float) -> dict:
        return self.call("POST", "/api/zoom", {"x": x, "y": y, "w": w, "h": h}, timeout=25.0)

    def frame(self, rect: tuple[float, float, float, float] | None = None, fmt: str = "png",
              max_tokens: int = 4096) -> dict:
        """Full-resolution frame for the local vision model; needs client_id and an active share."""
        body: dict = {"format": fmt, "max_tokens": max_tokens}
        if rect is not None:
            body.update(zip("xywh", rect))
        return self.call("POST", "/api/frame", body, timeout=25.0)

    def wait_change(self, timeout_s: float, min_change_percent: float, settle_ms: int,
                    include_shot_on_timeout: bool = False) -> dict:
        body = {"timeout_s": timeout_s, "min_change_percent": min_change_percent, "settle_ms": settle_ms,
                "include_shot_on_timeout": include_shot_on_timeout}
        settle_cap = max(3.0, 6 * settle_ms / 1000.0)       # mirrors Service._wait_change
        return self.call("POST", "/api/wait_change", body, timeout=timeout_s + settle_cap + 20)

    def act(self, body: dict) -> dict:
        timeout = 30.0 + self.cfg.post_action_ms / 1000.0
        if body.get("action") == "type":
            per_char = (self.cfg.key_hold_ms + 2 * self.cfg.key_gap_ms) / 1000.0
            timeout += len(str(body.get("text", ""))) * per_char * 1.5
        elif body.get("action") == "keys":
            timeout += 2.0 * int(body.get("repeat", 1)) * max(1, len(str(body.get("keys", "")).split()))
        return self.call("POST", "/api/act", body, timeout=timeout)

    def share(self, minutes: int) -> dict:
        return self.call("POST", "/api/share", {"minutes": minutes})

    def unshare(self) -> dict:
        return self.call("POST", "/api/unshare", {})

    def calibrate(self, reset: bool = False) -> dict:
        return self.call("POST", "/api/calibrate", {"reset": reset}, timeout=60.0)
