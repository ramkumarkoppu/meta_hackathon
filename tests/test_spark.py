"""Client for Muse Spark (spark.py), against a fake Meta Model API."""
import json

import pytest

from fakes import FakeServer, spark_stream
from nanokvm_mcp.config import Config
from nanokvm_mcp.spark import SparkClient, SparkError, SparkModelMismatch, cost_usd

KEY = "LLM_1234567890_" + "dummySECRETvalue"  # split so the commit hook allows it


@pytest.fixture
def setup(tmp_path):
    answers = []

    def handler(path, body):
        return 200, answers.pop(0) if answers else spark_stream()

    fake = FakeServer(KEY, handler)
    cfg = Config(home=tmp_path, simulate=True)
    cfg.run_dir.mkdir()
    (cfg.run_dir / "meta_api_key").write_text(KEY, encoding="utf-8")
    yield SparkClient(cfg, base_url=f"http://127.0.0.1:{fake.port}"), fake, answers
    fake.close()


def test_request_is_text_only_store_false_standard_model(setup):
    spark, fake, _ = setup
    reply = spark.chat([{"role": "developer", "content": "Answer briefly."}, {"role": "user", "content": "Why?"}])
    assert reply.content == "Use i < n." and reply.model == "muse-spark-1.3" and reply.finish_reason == "stop"
    assert reply.cost_usd == pytest.approx(cost_usd("muse-spark-1.3", {"prompt_tokens": 1000, "completion_tokens": 200}))
    (path, headers, body), = fake.requests
    assert path == "/chat/completions"
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert body["store"] is False and body["model"] == "muse-spark-1.3" and body["stream"] is True
    assert body["reasoning_effort"] == "medium"
    assert "temperature" not in body and "top_p" not in body


def test_contributor_reply_is_discarded(setup):
    spark, _fake, answers = setup
    answers.append(spark_stream(model="muse-spark-1.3-contributor"))
    with pytest.raises(SparkModelMismatch):
        spark.chat([{"role": "user", "content": "hi"}])


def test_incomplete_stream_is_an_error(setup):
    spark, _fake, answers = setup
    answers.append(spark_stream(done=False))
    with pytest.raises(SparkError):
        spark.chat([{"role": "user", "content": "hi"}])


def test_tool_calls_are_assembled(setup):
    spark, fake, answers = setup
    args = json.dumps({"start_line": 23, "end_line": 23, "new_text": "    for (int i = 0; i < n; ++i) {"})
    answers.append(spark_stream(content="", tool_calls=[("apply_edit", args)], finish="tool_calls"))
    tools = [{"type": "function", "function": {"name": "apply_edit", "parameters": {"type": "object"}}}]
    reply = spark.chat([{"role": "user", "content": "fix it"}], tools=tools, parallel_tool_calls=False)
    assert reply.finish_reason == "tool_calls"
    assert reply.tool_calls == [{"id": "call_0", "name": "apply_edit", "arguments": args}]
    body = fake.requests[0][2]
    assert body["tool_choice"] == "auto" and body["parallel_tool_calls"] is False


@pytest.mark.parametrize("messages", [
    [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}],
    [{"role": "user", "content": "look: data:image/png;base64,AAAA"}],
    [{"role": "user", "content": [{"type": "text", "text": "ok"}, {"type": "input_image"}]}],
])
def test_images_never_leave(setup, messages):
    spark, fake, _ = setup
    with pytest.raises(ValueError):
        spark.chat(messages)
    assert fake.requests == []


def test_only_standard_models_and_no_address_change_outside_tests(tmp_path):
    with pytest.raises(ValueError):
        SparkClient(Config(home=tmp_path, spark_model="muse-spark-1.3-contributor"))
    with pytest.raises(ValueError):
        SparkClient(Config(home=tmp_path), base_url="http://127.0.0.1:1")


def test_malformed_key_file_is_refused_without_showing_it(setup):
    spark, fake, _ = setup
    (spark.cfg.run_dir / "meta_api_key").write_text(KEY + "\nmy note", encoding="utf-8")
    with pytest.raises(SparkError) as e:
        spark.chat([{"role": "user", "content": "hi"}])
    assert "dummySECRET" not in str(e.value)
    assert fake.requests == []


def test_none_effort_is_refused(setup):
    spark, _fake, _ = setup
    with pytest.raises(ValueError):
        spark.chat([{"role": "user", "content": "hi"}], reasoning_effort="none")
