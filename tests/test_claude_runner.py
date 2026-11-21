import json
import os
import sys
from pathlib import Path

import pytest

from prospectscout.adapters import claude_runner
from prospectscout.adapters.claude_runner import LLMError

FAKE = str(Path(__file__).resolve().parent / "fake_claude.py")


@pytest.fixture
def fake_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_runner, "claude_argv", lambda: [sys.executable, FAKE])
    monkeypatch.setenv("FAKE_CLAUDE_OUT", str(tmp_path / "fake_out.json"))

    def set_mode(mode):
        monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    set_mode("ok")
    return set_mode


def test_build_argv_flags():
    a = claude_runner.build_argv(["claude"], {"type": "object"}, ["WebSearch", "WebFetch"], "sonnet")
    assert a[:4] == ["claude", "-p", "--output-format", "json"]
    for f in ("--safe-mode", "--no-session-persistence", "--json-schema", "--model"):
        assert f in a
    assert a[a.index("--tools") + 1] == "WebSearch,WebFetch"
    assert a[a.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    assert "--bare" not in a


def test_build_argv_no_tools_passes_empty_tools():
    a = claude_runner.build_argv(["claude"], None, [], None)
    assert a[a.index("--tools") + 1] == "" and "--allowedTools" not in a


def test_build_argv_without_safe_mode_uses_setting_sources():
    a = claude_runner.build_argv(["claude"], None, [], None, safe_mode=False)
    assert "--safe-mode" not in a
    assert a[a.index("--setting-sources") + 1] == "" and "--strict-mcp-config" in a


def test_structured_output_returned(fake_mode):
    fake_mode("ok")
    r = claude_runner.run_llm("hi", schema={"type": "object"})
    assert r.data == {"a": 1} and r.cost_usd == 0.01


def test_json_parsed_from_result_text(fake_mode):
    fake_mode("text_json")
    assert claude_runner.run_llm("hi", schema={"type": "object"}).data == {"a": 2}


def test_answer_mentioning_authentication_is_accepted(fake_mode):
    fake_mode("auth_text")
    r = claude_runner.run_llm("hi")
    assert "authentication" in r.text


def test_is_error_raises_error(fake_mode):
    fake_mode("is_error")
    with pytest.raises(LLMError) as e:
        claude_runner.run_llm("hi")
    assert e.value.kind == "error" and "boom" in str(e.value)


def test_usage_limit_kind(fake_mode):
    fake_mode("usage")
    with pytest.raises(LLMError) as e:
        claude_runner.run_llm("hi")
    assert e.value.kind == "usage_limit"


def test_timeout_kind(fake_mode):
    fake_mode("sleep")
    with pytest.raises(LLMError) as e:
        claude_runner.run_llm("hi", timeout=1)
    assert e.value.kind == "timeout"


def test_not_installed(monkeypatch):
    monkeypatch.setattr(claude_runner, "claude_argv", lambda: None)
    with pytest.raises(LLMError) as e:
        claude_runner.run_llm("hi")
    assert e.value.kind == "not_installed"


def test_prose_without_json_is_bad_output_when_schema_given(fake_mode):
    fake_mode("prose_only")
    with pytest.raises(LLMError) as e:
        claude_runner.run_llm("hi", schema={"type": "object"})
    assert e.value.kind == "bad_output"


def test_prompt_sent_on_stdin_and_cwd_is_work_dir(fake_mode):
    fake_mode("echo")
    claude_runner.run_llm("multi\nline prompt")
    out = json.loads(Path(os.environ["FAKE_CLAUDE_OUT"]).read_text(encoding="utf-8"))
    assert out["stdin"] == "multi\nline prompt" and out["cwd"].endswith("work")
    assert "--safe-mode" in out["argv"]


def test_falls_back_when_safe_mode_unknown(fake_mode):
    fake_mode("unknown_safe_mode")
    r = claude_runner.run_llm("hi", schema={"type": "object"})
    assert r.data == {"a": 1}


def test_test_connection_reports_failure_in_plain_english(monkeypatch):
    monkeypatch.setattr(claude_runner, "claude_argv", lambda: None)
    res = claude_runner.test_connection()
    assert res["ok"] is False and "Claude Code" in res["message"]


# --- streaming (Quick Prospect) ------------------------------------------------------------

def test_stream_argv_flags():
    a = claude_runner.build_stream_argv(["claude"], ["WebSearch", "WebFetch"], None)
    for f in ("stream-json", "--verbose", "--include-partial-messages", "--safe-mode", "--no-session-persistence"):
        assert f in a
    assert a[a.index("--allowedTools") + 1] == "WebSearch,WebFetch" and "--bare" not in a


def test_stream_llm_emits_text_and_steps(fake_mode):
    fake_mode("stream_ok")
    texts, steps = [], []
    final = claude_runner.stream_llm("hi", ["WebSearch"], on_text=texts.append, on_step=lambda n, i: steps.append((n, i)))
    assert "".join(texts) == final == "## First look\nHello\n\n## Verdict\nworld"
    assert steps == [("WebSearch", {"query": "q"})]


def test_stream_llm_usage_limit(fake_mode):
    fake_mode("stream_usage")
    with pytest.raises(LLMError) as e:
        claude_runner.stream_llm("hi", [], on_text=lambda t: None, on_step=lambda n, i: None)
    assert e.value.kind == "usage_limit"


def test_stream_llm_not_installed(monkeypatch):
    monkeypatch.setattr(claude_runner, "claude_argv", lambda: None)
    with pytest.raises(LLMError) as e:
        claude_runner.stream_llm("hi", [], on_text=lambda t: None, on_step=lambda n, i: None)
    assert e.value.kind == "not_installed"
