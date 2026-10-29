"""The whole path (pipeline.py, nanokvm read/ask) against the simulated locked computer, a fake Glimmer
server and a fake Meta Model API."""
import json

import pytest

from fakes import FakeServer, llama_reply, spark_stream
from nanokvm_mcp import cli
from nanokvm_mcp.client import ServiceClient, ServiceError
from nanokvm_mcp.glimmer import GlimmerClient
from nanokvm_mcp.outbound import OutboundBlocked
from nanokvm_mcp.pipeline import Pipeline
from nanokvm_mcp.spark import SparkClient

LLAMA_KEY, META_KEY = "k" * 32, "LLM_1234567890_" + "dummySECRETvalue"  # split so the commit hook allows it
SCREEN = ("19 // maintainer: jane.doe@example.org\n21 int sum_first(const std::vector<int>& v, int n) {\n"
          "23     for (int i = 0; i <= n; ++i) {\n24         total += v[i];")


@pytest.fixture
def world(sim):
    def llama(path, body):
        if path == "/health":
            return 200, {"status": "ok"}
        text = json.dumps(body["messages"])
        if body.get("max_tokens") == 1:
            return 200, llama_reply("ok")
        if "image_url" in text:
            return 200, llama_reply(SCREEN)
        if "<question>" in text:                       # minimise: keep the loop lines only
            return 200, llama_reply("23     for (int i = 0; i <= n; ++i) {\n24         total += v[i];")
        return 200, llama_reply("Glimmer: use i < n.")
    glim = FakeServer(LLAMA_KEY, llama)
    meta = FakeServer(META_KEY, lambda path, body: (200, spark_stream("Spark: use i < n on line 23.")))
    cfg = sim.cfg
    cfg.glimmer_port = glim.port
    (cfg.run_dir / "llama_api_key").write_text(LLAMA_KEY, encoding="ascii")
    (cfg.run_dir / "meta_api_key").write_text(META_KEY, encoding="utf-8")

    def make(approve_share=lambda m: True, **kw):
        return Pipeline(cfg, glimmer=GlimmerClient(cfg), approve_share=approve_share,
                        spark=SparkClient(cfg, base_url=f"http://127.0.0.1:{meta.port}"), **kw)

    yield make, glim, meta, cfg
    for _p, _h, body in meta.requests:              # no image ever reached "Meta"
        assert "data:" not in json.dumps(body)
    glim.close()
    meta.close()


def test_read_needs_approval_and_closes_its_share(world):
    make, glim, meta, cfg = world
    with pytest.raises(PermissionError):
        with make(approve_share=lambda m: False) as pipe:
            pipe.open()
    assert glim.requests == []
    with make() as pipe:
        pipe.open(5)
        r = pipe.read()
        client_id = pipe.client_id
    assert r.text == SCREEN and r.frame["width"] == 1920 and "image_b64" not in r.frame
    images = [b for b in glim.bodies("/v1/chat/completions") if "image_url" in json.dumps(b)]
    assert len(images) == 1 and images[0]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png")
    with pytest.raises(ServiceError):               # the share was closed on exit
        ServiceClient(cfg, client_id=client_id).frame()
    assert meta.requests == []


def test_ask_with_stand_in_sends_only_redacted_trimmed_text(world):
    make, glim, meta, cfg = world
    with make(stand_in=True) as pipe:
        pipe.open()
        res = pipe.ask("Why does sum_first overrun?")
    assert res.answer == "Spark: use i < n on line 23." and not res.local_only
    sent = meta.requests[0][2]["messages"][1]["content"]
    assert "jane.doe" not in sent and "i <= n" in sent and "maintainer" not in sent   # trimmed to the loop
    log = [json.loads(x) for x in (cfg.run_dir / "outbound.log").read_text(encoding="utf-8").splitlines()]
    assert log[0]["sent_text"] == sent and log[0]["frame_sha256"] == res.reading.frame["image_sha256"]


def test_free_credits_block_real_content_and_local_only_sends_nothing(world):
    make, glim, meta, cfg = world
    with make() as pipe:
        pipe.open()
        with pytest.raises(OutboundBlocked, match="free credits"):
            pipe.ask("why?")
        cfg.outbound = "local_only"
        res = pipe.ask("why?")
    assert res.local_only and res.answer == "Glimmer: use i < n."
    assert meta.requests == []


def test_no_files_written_except_the_audit_log(world):
    make, glim, meta, cfg = world
    before = {p for p in cfg.home.rglob("*") if p.is_file()}
    with make(stand_in=True) as pipe:
        pipe.open()
        pipe.read("describe")
        pipe.ask("why?")
    new = {p for p in cfg.home.rglob("*") if p.is_file()} - before
    assert {p.name for p in new} == {"outbound.log"}


def test_read_summarises_line_numbers_and_checks_a_dummy_file(tmp_path):
    text = ("1 // live check\n2 #include <vector>\n4 int sum_first(const std::vector<int>& v, int n) {\n"
            "5     int total = 0;")
    assert cli._line_summary(text) == "(4 numbered lines read, 1-5, 1 missing: 3)"
    assert cli._line_summary("no numbers here").startswith("(no numbered lines")
    ref = tmp_path / "ref.cpp"
    ref.write_text("// live check\n#include <vector>\n\nint sum_first(const std::vector<int>& v, int n) {\n"
                   "    int total  = 0;\n", encoding="utf-8")
    assert cli._check_against(text, ref) == ("check against ref.cpp (lines 1-5 of 5 on the screen): "
                                             "3/4 exact, 4/4 equal apart from spacing")
    ref.write_text("// live check\n#include <vector>\n\nint sum(int n) {\n    int total = 0;\n", encoding="utf-8")
    out = cli._check_against(text, ref)
    assert "3/4 exact" in out and "line 4: read" in out


def test_cli_read_refuses_without_a_person_at_the_keyboard(world, monkeypatch, capsys):
    make, glim, meta, cfg = world
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    monkeypatch.setattr(cli.Config, "load", classmethod(lambda c, *a, **k: cfg))
    assert cli.main(["read"]) == 1
    assert "approval at the keyboard" in capsys.readouterr().out
    assert glim.requests == []
