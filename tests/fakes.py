"""Mock HTTP servers for the two models: llama-server, where Muse Glimmer reads the screen on the laptop, and
Meta's Model API, where Muse Spark answers from redacted text. With these the tests need no GPU and no key."""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeServer:
    """Records every request (path, headers, JSON body) and answers with ``handler(path, body)``,
    which returns (status, payload). A wrong or missing bearer key gets 401."""

    def __init__(self, key: str, handler: Callable[[str, dict | None], tuple[int, dict]]) -> None:
        self.key = key
        self.handler = handler
        self.requests: list[tuple[str, dict, dict | None]] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: A003
                pass

            def _answer(self, body: dict | None) -> None:
                fake.requests.append((self.path, dict(self.headers), body))
                if self.headers.get("Authorization") != f"Bearer {fake.key}":
                    status, payload = 401, {"error": {"message": "bad key", "code": "invalid_api_key"}}
                else:
                    status, payload = fake.handler(self.path, body)
                stream = isinstance(payload, str)       # a str is a ready-made text/event-stream body
                data = payload.encode() if stream else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                self._answer(None)

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                self._answer(json.loads(self.rfile.read(n)) if n else {})

        self.port = free_port()
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def bodies(self, path: str) -> list[dict]:
        """The JSON bodies posted to ``path``, oldest first."""
        return [b for p, _h, b in self.requests if p == path and b is not None]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def spark_stream(content: str = "Use i < n.", model: str = "muse-spark-1.3", tool_calls: list | None = None,
                 finish: str = "stop", done: bool = True) -> str:
    """A Chat Completions event stream: content in two pieces, optional tool calls, then usage."""
    chunks = [{"model": model, "choices": [{"index": 0, "delta": {"role": "assistant", "content": content[:5]}}]},
              {"model": model, "choices": [{"index": 0, "delta": {"content": content[5:]}}]}]
    for i, (name, args) in enumerate(tool_calls or []):
        half = len(args) // 2
        chunks.append({"model": model, "choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": i, "id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": args[:half]}}]}}]})
        chunks.append({"model": model, "choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": i, "function": {"arguments": args[half:]}}]}}]})
    chunks.append({"model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    chunks.append({"model": model, "choices": [], "usage": {"prompt_tokens": 1000, "completion_tokens": 200}})
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
    return "".join(lines) + ("data: [DONE]\n\n" if done else "")


def llama_reply(content: str = "1 int x;", finish: str = "stop", reasoning: str = "",
                tool_calls: list | None = None) -> dict:
    """llama-server's answer. ``tool_calls`` are (name, arguments JSON text) pairs, the form it returns Glimmer's in."""
    message = {"content": content, "reasoning_content": reasoning}
    if tool_calls:
        message["tool_calls"] = [{"type": "function", "function": {"name": name, "arguments": args}, "id": f"id{i}"}
                                 for i, (name, args) in enumerate(tool_calls)]
    return {"choices": [{"finish_reason": finish, "message": message}],
            "usage": {"prompt_tokens": 2700, "completion_tokens": 20},
            "timings": {"prompt_ms": 5000.0, "predicted_per_second": 140.0}}
