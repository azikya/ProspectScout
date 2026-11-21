"""Stand-in for the `claude` CLI used by tests. Behaviour is chosen by FAKE_CLAUDE_MODE."""

import json
import os
import sys
import time


def out(obj, code=0):
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()
    sys.exit(code)


def result(text, structured=None, is_error=False):
    obj = {"type": "result", "is_error": is_error, "result": text,
           "total_cost_usd": 0.01, "duration_ms": 5}
    if structured is not None:
        obj["structured_output"] = structured
    return obj


def main():
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    prompt = sys.stdin.read()
    argv = sys.argv[1:]
    if mode == "unknown_safe_mode":
        if "--safe-mode" in argv:
            sys.stderr.write("error: unknown option '--safe-mode'\n")
            sys.exit(1)
        mode = "ok"
    if mode == "ok":
        out(result('{"a": 1}', structured={"a": 1}))
    if mode == "text_json":
        out(result('Here:\n{"a": 2}'))
    if mode == "auth_text":
        out(result("I would trace your JWT authentication flow and fix it. Not logged in users too."))
    if mode == "is_error":
        out(result("API Error: boom", is_error=True), code=1)
    if mode == "usage":
        out(result("Claude AI usage limit reached|1764000000", is_error=True), code=1)
    if mode == "prose_only":
        out(result("Sorry, I cannot produce JSON for this."))
    if mode == "sleep":
        time.sleep(5)
        out(result("late"))
    if mode in ("stream_ok", "stream_usage"):
        def emit(obj):
            sys.stdout.write(json.dumps(obj) + "\n")
            sys.stdout.flush()
        emit({"type": "system", "subtype": "init"})
        if mode == "stream_usage":
            emit({"type": "result", "subtype": "error", "is_error": True, "result": "Claude AI usage limit reached|1"})
            sys.exit(1)
        emit({"type": "stream_event", "event": {"type": "content_block_start", "index": 0,
                                                "content_block": {"type": "text", "text": ""}}})
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                                "delta": {"type": "text_delta", "text": "## First look\nHello"}}})
        emit({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "## First look\nHello"},
            {"type": "tool_use", "name": "WebSearch", "input": {"query": "q"}}]}})
        emit({"type": "stream_event", "event": {"type": "content_block_start", "index": 0,
                                                "content_block": {"type": "text", "text": ""}}})
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                                "delta": {"type": "text_delta", "text": "## Verdict\nworld"}}})
        emit({"type": "result", "subtype": "success", "is_error": False, "result": "## Verdict\nworld"})
        sys.exit(0)
    if mode == "echo":
        with open(os.environ["FAKE_CLAUDE_OUT"], "w", encoding="utf-8") as fh:
            json.dump({"stdin": prompt, "cwd": os.getcwd(), "argv": argv}, fh)
        out(result("echoed"))
    sys.stderr.write(f"unknown mode {mode}\n")
    sys.exit(2)


if __name__ == "__main__":
    main()
