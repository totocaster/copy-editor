"""Codex CLI as the model runner.

The CLI holds the ChatGPT sign-in; we never see a token. Each pass window is
one `codex exec` subprocess with a JSON output schema, an ephemeral session,
and a read-only sandbox in an empty temp directory.
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
import tomllib
from pathlib import Path
from typing import Any, Callable

from .errors import ProviderError

ID = "codex"
LABEL = "OpenAI · Codex CLI"
CODEX_BIN = os.environ.get("CODEX_BIN", "codex")
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
STATUS_TTL = 30.0
CATALOG_TTL = 600.0
FALLBACK_EFFORTS = ["low", "medium", "high"]


class CodexError(ProviderError):
    pass


def which() -> str | None:
    return shutil.which(CODEX_BIN)


def _run(args: list[str], timeout: float = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run([CODEX_BIN, *args], capture_output=True, text=True, timeout=timeout)


def version() -> str:
    try:
        out = _run(["--version"], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (out.stdout or out.stderr).strip().replace("codex-cli", "").strip()


_status_cache: dict[str, Any] = {"at": 0.0, "value": None}
_status_lock = threading.Lock()


def invalidate_status() -> None:
    with _status_lock:
        _status_cache["at"] = 0.0


def status(force: bool = False) -> dict[str, Any]:
    """Whether Codex is installed and signed in. Cached briefly; it shells out."""
    with _status_lock:
        cached = _status_cache["value"]
        if cached is not None and not force and time.monotonic() - _status_cache["at"] < STATUS_TTL:
            return cached
    if which() is None:
        value = {"installed": False, "signed_in": False, "version": "", "message": "Codex CLI not found on PATH."}
    else:
        try:
            out = _run(["login", "status"])
            text = (out.stdout + "\n" + out.stderr).strip()
            low = text.lower()
            signed_in = "logged in" in low and "not logged in" not in low
            value = {"installed": True, "signed_in": signed_in, "version": version(), "message": text.splitlines()[0] if text else ""}
        except (OSError, subprocess.TimeoutExpired) as exc:
            value = {"installed": True, "signed_in": False, "version": "", "message": f"Could not query Codex: {exc}"}
    with _status_lock:
        _status_cache.update(at=time.monotonic(), value=value)
    return value


def config_defaults() -> dict[str, str]:
    """Model and reasoning effort from ~/.codex/config.toml, if present."""
    path = CODEX_HOME / "config.toml"
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {"model": "", "effort": ""}
    return {"model": str(data.get("model") or ""), "effort": str(data.get("model_reasoning_effort") or "")}


_catalog_cache: dict[str, Any] = {"at": 0.0, "value": None}


def _raw_catalog() -> list[dict[str, Any]]:
    """The model catalog as Codex reports it, falling back to its on-disk cache."""
    text = ""
    try:
        out = _run(["debug", "models"], timeout=25)
        text = out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        text = ""
    for candidate in (text, _cached_catalog_text()):
        if not candidate:
            continue
        starts = [i for i in (candidate.find("{"), candidate.find("[")) if i >= 0]
        if not starts:
            continue
        try:
            data = json.loads(candidate[min(starts):])
        except json.JSONDecodeError:
            continue
        models = data.get("models") if isinstance(data, dict) else data
        if isinstance(models, list):
            return [m for m in models if isinstance(m, dict) and m.get("slug")]
    return []


def _cached_catalog_text() -> str:
    try:
        return (CODEX_HOME / "models_cache.json").read_text()
    except OSError:
        return ""


def models(force: bool = False) -> list[dict[str, Any]]:
    """Current models on this account: listed ones, newest generation only, by priority."""
    if not force and _catalog_cache["value"] is not None and time.monotonic() - _catalog_cache["at"] < CATALOG_TTL:
        return _catalog_cache["value"]
    out: list[dict[str, Any]] = []
    for m in sorted(_raw_catalog(), key=lambda m: m.get("priority", 999)):
        if m.get("visibility") != "list":
            continue
        description = str(m.get("description") or "")
        if "previous-generation" in description.lower():
            continue
        efforts = [lvl.get("effort") for lvl in m.get("supported_reasoning_levels") or [] if lvl.get("effort")]
        out.append({
            "id": m["slug"],
            "label": m.get("display_name") or m["slug"],
            "description": description,
            "efforts": efforts or list(FALLBACK_EFFORTS),
            "default_effort": m.get("default_reasoning_level") or "medium",
        })
    if not out:
        default = config_defaults()["model"]
        if default:
            out.append({"id": default, "label": default, "description": "From your Codex config.",
                        "efforts": list(FALLBACK_EFFORTS), "default_effort": "medium"})
    _catalog_cache.update(at=time.monotonic(), value=out)
    return out


def suggested_models() -> list[str]:
    return [m["id"] for m in models()]


def _tail(text: str, n: int = 12) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def parse_json_output(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
        raise CodexError(f"Codex did not return JSON: {raw[:200]}")


def exec_command(schema_path: str, out_path: str, workdir: str, model: str = "", effort: str = "") -> list[str]:
    cmd = [CODEX_BIN, "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "-C", workdir,
           "--output-schema", schema_path, "-o", out_path]
    if model:
        cmd += ["-m", model]
    if effort:
        cmd += ["-c", f'model_reasoning_effort="{effort}"']
    return cmd


def run_exec(prompt: str, schema: dict[str, Any], *, model: str = "", effort: str = "", timeout: float = 900,
             on_start: Callable[[subprocess.Popen[str]], None] | None = None) -> dict[str, Any]:
    """Run one prompt through `codex exec` and return the schema-shaped reply."""
    with tempfile.TemporaryDirectory(prefix="write-pass-") as tmp:
        schema_path = Path(tmp) / "schema.json"
        out_path = Path(tmp) / "out.json"
        schema_path.write_text(json.dumps(schema))
        cmd = exec_command(str(schema_path), str(out_path), tmp, model, effort)
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except OSError as exc:
            raise CodexError(f"Could not start Codex: {exc}") from exc
        if on_start:
            on_start(proc)
        try:
            stdout, stderr = proc.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()  # drain pipes and reap the terminated child
            raise CodexError(f"Codex timed out after {int(timeout)}s")
        combined = (stderr or "") + "\n" + (stdout or "")
        low = combined.lower()
        if proc.returncode != 0:
            if "rate limit" in low or "usage limit" in low or "too many requests" in low:
                raise CodexError("Rate limited by your ChatGPT plan. Wait a while, then retry.")
            if "not logged in" in low or "login" in low and "required" in low:
                invalidate_status()
                raise CodexError("Codex is not signed in. Sign in from Settings → Account.")
            raise CodexError(f"Codex exited with {proc.returncode}: {_tail(combined)}")
        if not out_path.exists():
            raise CodexError(f"Codex produced no output: {_tail(combined)}")
        return parse_json_output(out_path.read_text())


# ----------------------------------------------------------------------------
# Sign-in by device code, sign-out
# ----------------------------------------------------------------------------

_login: dict[str, Any] = {"proc": None, "url": "", "code": "", "lines": [], "done": True, "ok": False, "error": ""}
_login_lock = threading.Lock()
_URL_RE = re.compile(r"https?://\S+")
_CODE_RE = re.compile(r"\b[A-Z0-9]{4,8}-[A-Z0-9]{4,8}\b")


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
            if not _login["code"]:
                m = _CODE_RE.search(line)
                if m:
                    _login["code"] = m.group(0)
    proc.wait()
    with _login_lock:
        _login["done"] = True
        _login["ok"] = proc.returncode == 0
        if proc.returncode != 0:
            _login["error"] = _tail("\n".join(_login["lines"]), 4) or f"codex login exited with {proc.returncode}"
    invalidate_status()


def start_login() -> dict[str, Any]:
    with _login_lock:
        active = not _login["done"]
        if not active:
            _login.update(proc=None, url="", code="", lines=[], done=False, ok=False, error="")
    if active:
        return login_state()
    try:
        proc = subprocess.Popen([CODEX_BIN, "login", "--device-auth"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except OSError as exc:
        with _login_lock:
            _login.update(done=True, ok=False, error=f"Could not start codex login: {exc}")
        return login_state()
    with _login_lock:
        _login["proc"] = proc
    threading.Thread(target=_read_login, args=(proc,), daemon=True).start()
    time.sleep(1.5)  # give it a moment to print the code
    return login_state()


start_device_login = start_login


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
        out = _run(["logout"], timeout=20)
        msg = (out.stdout + out.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = str(exc)
    invalidate_status()
    return msg
