"""The one place ProspectScout talks to Claude.

Runs the locally installed, signed-in Claude Code CLI in headless mode
(``claude -p``), so calls use the user's Claude subscription. Swapping in an
Anthropic API backend later means replacing ``run_llm`` with the same signature.

Rules (see the design spec):
- prompt always goes on stdin (Windows batch wrappers truncate multi-line args);
- ``--safe-mode`` keeps plugins/hooks/CLAUDE.md out while keeping OAuth;
  ``--bare`` is never used because it disables subscription auth;
- successful answers are never keyword-scanned (that rejected real answers in UpworkScout).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from .. import config

_USAGE_RE = re.compile(r"usage limit|limit reached|rate.?limit", re.I)
_UNKNOWN_SAFE_MODE_RE = re.compile(r"unknown option.*--safe-mode", re.I)


class LLMError(Exception):
    """kind: not_installed | usage_limit | timeout | error | bad_output"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class LLMResult:
    text: str
    data: dict | None
    cost_usd: float | None
    duration_ms: int | None


def claude_cli() -> str | None:
    return shutil.which("claude") or shutil.which("claude.cmd")


def claude_argv() -> list[str] | None:
    """How to invoke Claude Code on this machine, or None if it isn't installed.
    On Windows `claude` may be a .cmd wrapper; run its cli.js through node instead."""
    path = claude_cli()
    if not path:
        return None
    if path.lower().endswith((".cmd", ".bat", ".ps1")):
        base = os.path.dirname(path)
        node = shutil.which("node")
        for cli in (os.path.join(base, "node_modules", "@anthropic-ai", "claude-code", "cli.js"),
                    os.path.join(base, "node_modules", "@anthropic-ai", "claude-code", "cli.mjs")):
            if node and os.path.exists(cli):
                return [node, cli]
        exe = os.path.splitext(path)[0] + ".exe"
        if os.path.exists(exe):
            return [exe]
    return [path]


def build_argv(base: list[str], schema: dict | None, tools: list[str], model: str | None,
               safe_mode: bool = True) -> list[str]:
    argv = [*base, "-p", "--output-format", "json"]
    if safe_mode:
        argv.append("--safe-mode")
    else:
        argv += ["--setting-sources", "", "--strict-mcp-config"]
    argv += ["--no-session-persistence", "--tools", ",".join(tools)]
    if tools:
        argv += ["--allowedTools", ",".join(tools)]
    if schema is not None:
        argv += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
    if model:
        argv += ["--model", model]
    return argv


def _first_json_object(text: str) -> dict | None:
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        return obj if isinstance(obj, dict) else None
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def _invoke(argv: list[str], prompt: str, timeout: int, cwd: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired as exc:
        raise LLMError("timeout", f"Claude didn't answer within {timeout}s.") from exc
    except OSError as exc:
        raise LLMError("not_installed", f"Couldn't start Claude Code: {exc}") from exc


def run_llm(prompt: str, schema: dict | None = None, tools: list[str] | None = None,
            model: str | None = None, timeout: int = 300) -> LLMResult:
    base = claude_argv()
    if not base:
        raise LLMError("not_installed", "Claude Code isn't installed or isn't on PATH.")
    tools = list(tools or [])
    work = os.path.join(config.home_dir(), "work")
    os.makedirs(work, exist_ok=True)

    proc = _invoke(build_argv(base, schema, tools, model), prompt, timeout, work)
    if proc.returncode != 0 and _UNKNOWN_SAFE_MODE_RE.search(proc.stderr or ""):
        proc = _invoke(build_argv(base, schema, tools, model, safe_mode=False), prompt, timeout, work)

    stdout = (proc.stdout or "").strip()
    try:
        payload = json.loads(stdout) if stdout else {}
    except ValueError:
        payload = {"result": stdout, "is_error": proc.returncode != 0}
    if not isinstance(payload, dict):
        payload = {"result": stdout}
    text = str(payload.get("result") or "")

    if payload.get("is_error") or proc.returncode != 0:
        detail = text or (proc.stderr or "").strip() or f"exit code {proc.returncode}"
        kind = "usage_limit" if _USAGE_RE.search(detail) else "error"
        raise LLMError(kind, detail[:500])

    data = payload.get("structured_output")
    if not isinstance(data, dict):
        data = _first_json_object(text) if schema is not None else None
    if schema is not None and not isinstance(data, dict):
        raise LLMError("bad_output", "Claude's answer didn't contain the expected JSON.")

    cost = payload.get("total_cost_usd")
    dur = payload.get("duration_ms")
    return LLMResult(text=text, data=data,
                     cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
                     duration_ms=int(dur) if isinstance(dur, (int, float)) else None)


def build_stream_argv(base: list[str], tools: list[str], model: str | None, safe_mode: bool = True) -> list[str]:
    argv = [*base, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    argv += ["--safe-mode"] if safe_mode else ["--setting-sources", "", "--strict-mcp-config"]
    argv += ["--no-session-persistence", "--tools", ",".join(tools)]
    if tools:
        argv += ["--allowedTools", ",".join(tools)]
    if model:
        argv += ["--model", model]
    return argv


def stream_llm(prompt: str, tools: list[str], on_text, on_step, model: str | None = None,
               timeout: int = 600) -> str:
    """Run Claude Code with streamed output. Calls on_text(chunk) as words arrive and on_step(name, input)
    for each tool use (web search/fetch). Returns the full streamed text. Raises LLMError like run_llm."""
    import threading

    base = claude_argv()
    if not base:
        raise LLMError("not_installed", "Claude Code isn't installed or isn't on PATH.")
    work = os.path.join(config.home_dir(), "work")
    os.makedirs(work, exist_ok=True)
    tools = list(tools or [])

    def attempt(safe_mode: bool):
        try:
            proc = subprocess.Popen(build_stream_argv(base, tools, model, safe_mode), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                    errors="replace", cwd=work, bufsize=1)
        except OSError as exc:
            raise LLMError("not_installed", f"Couldn't start Claude Code: {exc}") from exc
        timer = threading.Timer(timeout, proc.kill)
        timer.start()
        parts: list[str] = []
        result: dict = {}
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
            for line in proc.stdout:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                kind = ev.get("type")
                if kind == "stream_event":
                    e = ev.get("event") or {}
                    if e.get("type") == "content_block_start" and (e.get("content_block") or {}).get("type") == "text":
                        if parts and not "".join(parts).endswith("\n\n"):
                            sep = "\n\n" if not "".join(parts).endswith("\n") else "\n"
                            parts.append(sep)
                            on_text(sep)
                    elif e.get("type") == "content_block_delta":
                        d = e.get("delta") or {}
                        if d.get("type") == "text_delta" and d.get("text"):
                            parts.append(d["text"])
                            on_text(d["text"])
                elif kind == "assistant":
                    for block in (ev.get("message") or {}).get("content") or []:
                        if block.get("type") == "tool_use":
                            on_step(block.get("name", ""), block.get("input") or {})
                elif kind == "result":
                    result = ev
            proc.wait()
        finally:
            fired = not timer.is_alive() and proc.returncode not in (0, None) and not result
            timer.cancel()
        stderr = proc.stderr.read() if proc.stderr else ""
        return proc, "".join(parts), result, stderr, fired

    proc, text, result, stderr, fired = attempt(True)
    if proc.returncode != 0 and not text and _UNKNOWN_SAFE_MODE_RE.search(stderr or ""):
        proc, text, result, stderr, fired = attempt(False)
    if fired:
        raise LLMError("timeout", f"Claude didn't finish within {timeout}s.")
    if result.get("is_error") or (proc.returncode != 0 and not result):
        detail = str(result.get("result") or stderr.strip() or f"exit code {proc.returncode}")
        raise LLMError("usage_limit" if _USAGE_RE.search(detail) else "error", detail[:500])
    return text


_PLAIN = {
    "not_installed": "Claude Code isn't installed or isn't on PATH on this computer.",
    "usage_limit": "Your Claude plan's usage limit is reached. Try again after it resets.",
    "timeout": "Claude Code didn't answer in time. Check your internet connection.",
    "bad_output": "Claude Code answered, but not in the expected format.",
}


def test_connection() -> dict:
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    try:
        res = run_llm('Reply with {"ok": true}', schema=schema, tools=[], timeout=120)
    except LLMError as exc:
        msg = _PLAIN.get(exc.kind) or f"Claude Code returned an error: {exc}"
        return {"ok": False, "message": msg}
    ok = bool(res.data and res.data.get("ok"))
    return {"ok": ok, "message": "Claude is connected and ready." if ok else "Claude answered unexpectedly."}
