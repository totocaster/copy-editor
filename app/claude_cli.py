"""Claude Code CLI as a model runner.

`claude -p` under the user's claude.ai sign-in; no API key. Each pass window is
one subprocess with a JSON schema for the reply, no tools, and no session kept.
Not `--bare`: that mode skips the credential store and reports "not logged in".
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable

from .codex import parse_json_output
from .errors import ProviderError

ID = "claude"
LABEL = "Anthropic · Claude Code"
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
STATUS_TTL = 30.0
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
MODELS: list[dict[str, Any]] = [
    {"id": "claude-fable-5-1", "label": "Claude Fable 5.1", "description": "Most capable; the Mythos-class model."},
    {"id": "claude-opus-5", "label": "Claude Opus 5", "description": "Deep reasoning for demanding edits."},
    {"id": "claude-sonnet-5", "label": "Claude Sonnet 5", "description": "Fast and balanced for everyday passes."},
    {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5", "description": "Quickest and lightest."},
]
SYSTEM_PROMPT = ("You are a senior professional copy editor working for the author. Follow the instructions in the "
                 "message exactly and reply only with JSON matching the required schema.")


class ClaudeError(ProviderError):
    pass


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)  # allow running from inside a Claude Code session
    return env


def which() -> str | None:
    return shutil.which(CLAUDE_BIN)


def _run(args: list[str], timeout: float = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run([CLAUDE_BIN, *args], capture_output=True, text=True, timeout=timeout, env=_env(),
                          stdin=subprocess.DEVNULL)


def version() -> str:
    try:
        out = _run(["--version"], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (out.stdout or out.stderr).strip().split(" ")[0]


_status_cache: dict[str, Any] = {"at": 0.0, "value": None}
_status_lock = threading.Lock()


def invalidate_status() -> None:
    with _status_lock:
        _status_cache["at"] = 0.0


def status(force: bool = False) -> dict[str, Any]:
    with _status_lock:
        cached = _status_cache["value"]
        if cached is not None and not force and time.monotonic() - _status_cache["at"] < STATUS_TTL:
            return cached
    if which() is None:
        value = {"installed": False, "signed_in": False, "version": "", "message": "Claude Code CLI not found on PATH."}
    else:
        try:
            out = _run(["auth", "status", "--json"])
            data = json.loads(out.stdout[out.stdout.find("{"):]) if "{" in out.stdout else {}
            signed_in = bool(data.get("loggedIn"))
            method = data.get("authMethod") or "none"
            message = "Signed in with Claude" if signed_in else "Not signed in"
            if signed_in and method != "claude.ai":
                message = f"Signed in via {method}"
            value = {"installed": True, "signed_in": signed_in, "version": version(), "message": message}
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            value = {"installed": True, "signed_in": False, "version": "", "message": f"Could not query Claude Code: {exc}"}
    with _status_lock:
        _status_cache.update(at=time.monotonic(), value=value)
    return value


def config_defaults() -> dict[str, str]:
    return {"model": "", "effort": ""}


def models(force: bool = False) -> list[dict[str, Any]]:
    return [{**m, "efforts": list(EFFORTS), "default_effort": "medium"} for m in MODELS]


def suggested_models() -> list[str]:
    return [m["id"] for m in MODELS]


def exec_command(schema_json: str, model: str = "", effort: str = "", system_prompt: str = SYSTEM_PROMPT) -> list[str]:
    cmd = [CLAUDE_BIN, "-p", "--output-format", "json", "--json-schema", schema_json, "--tools", "",
           "--no-session-persistence", "--max-turns", "1"]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    return cmd


def parse_result(stdout: str) -> dict[str, Any]:
    """The schema-shaped reply from `claude -p --output-format json`."""
    start = stdout.find("{")
    if start < 0:
        raise ClaudeError(f"Claude Code returned no JSON: {stdout[:200]}")
    data = json.loads(stdout[start:])
    if data.get("is_error"):
        message = str(data.get("result") or data.get("error") or "unknown error")
        low = message.lower()
        if "not logged in" in low:
            invalidate_status()
            raise ClaudeError("Claude Code is not signed in. Sign in from Settings → Account.")
        if "rate limit" in low or "usage limit" in low or "limit reached" in low:
            raise ClaudeError("Usage limit reached on your Claude plan. Wait a while, then retry.")
        raise ClaudeError(f"Claude Code error: {message[:300]}")
    structured = data.get("structured_output")
    if isinstance(structured, dict):
        return structured
    result = data.get("result")
    if isinstance(result, dict):
        return result
    if isinstance(result, str) and result.strip():
        return parse_json_output(result)
    raise ClaudeError("Claude Code returned an empty result")


def run_exec(prompt: str, schema: dict[str, Any], *, model: str = "", effort: str = "", timeout: float = 900,
             on_start: Callable[[subprocess.Popen[str]], None] | None = None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="write-pass-") as tmp:
        cmd = exec_command(json.dumps(schema), model, effort)
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, env=_env(), cwd=tmp)
        except OSError as exc:
            raise ClaudeError(f"Could not start Claude Code: {exc}") from exc
        if on_start:
            on_start(proc)
        try:
            stdout, stderr = proc.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise ClaudeError(f"Claude Code timed out after {int(timeout)}s")
        if not (stdout or "").strip():
            tail = "\n".join((stderr or "").strip().splitlines()[-6:])
            raise ClaudeError(f"Claude Code exited with {proc.returncode}: {tail or 'no output'}")
        return parse_result(stdout)


# ----------------------------------------------------------------------------
# Sign in / out
# ----------------------------------------------------------------------------

_login: dict[str, Any] = {"proc": None, "url": "", "code": "", "lines": [], "done": True, "ok": False, "error": ""}
_login_lock = threading.Lock()
_URL_RE = re.compile(r"https?://\S+")


def _read_login(proc: subprocess.Popen[str]) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        with _login_lock:
            _login["lines"].append(line)
            if not _login["url"]:
                m = _URL_RE.search(line)
                if m:
                    _login["url"] = m.group(0).rstrip(".,)")
    proc.wait()
    with _login_lock:
        _login["done"] = True
        _login["ok"] = proc.returncode == 0
        if proc.returncode != 0:
            _login["error"] = "\n".join(_login["lines"][-4:]) or f"claude auth login exited with {proc.returncode}"
    invalidate_status()


def start_login() -> dict[str, Any]:
    with _login_lock:
        if not _login["done"]:
            return login_state()
        _login.update(proc=None, url="", code="", lines=[], done=False, ok=False, error="")
    try:
        proc = subprocess.Popen([CLAUDE_BIN, "auth", "login", "--claudeai"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=_env())
    except OSError as exc:
        with _login_lock:
            _login.update(done=True, ok=False, error=f"Could not start claude auth login: {exc}")
        return login_state()
    with _login_lock:
        _login["proc"] = proc
    threading.Thread(target=_read_login, args=(proc,), daemon=True).start()
    time.sleep(1.5)
    return login_state()


def login_state() -> dict[str, Any]:
    with _login_lock:
        return {"active": not _login["done"], "url": _login["url"], "code": _login["code"],
                "ok": _login["ok"], "error": _login["error"]}


def cancel_login() -> None:
    with _login_lock:
        proc = _login["proc"]
    if proc is not None and proc.poll() is None:
        proc.terminate()


def logout() -> str:
    try:
        out = _run(["auth", "logout"], timeout=20)
        msg = (out.stdout + out.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = str(exc)
    invalidate_status()
    return msg
