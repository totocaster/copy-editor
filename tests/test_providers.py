import json
import threading

import pytest

from app import claude_cli, codex, providers, repo, runs
from app.db import connect, init_db
from app.errors import ProviderError


CATALOG = [
    {"slug": "gpt-6-astra", "display_name": "GPT-6-Astra", "description": "Most capable.", "visibility": "list",
     "priority": 1, "default_reasoning_level": "medium",
     "supported_reasoning_levels": [{"effort": e} for e in ["low", "medium", "high", "xhigh", "max", "ultra"]]},
    {"slug": "gpt-reserve", "display_name": "Reserve", "description": "hidden", "visibility": "hide", "priority": 3},
    {"slug": "gpt-5.6-sol", "display_name": "GPT-5.6-Sol", "description": "Workhorse.", "visibility": "list",
     "priority": 4, "default_reasoning_level": "low", "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}]},
    {"slug": "gpt-5.5", "display_name": "GPT-5.5", "description": "Proven previous-generation model.", "visibility": "list",
     "priority": 12},
]


def test_codex_models_keep_listed_current_generation(monkeypatch):
    monkeypatch.setattr(codex, "_raw_catalog", lambda: CATALOG)
    models = codex.models(force=True)
    assert [m["id"] for m in models] == ["gpt-6-astra", "gpt-5.6-sol"]
    assert models[0]["efforts"][-1] == "ultra" and models[1]["default_effort"] == "low"
    assert providers.efforts_for("codex", "gpt-5.6-sol") == (["low", "medium"], "low")


def test_claude_command_and_result_parsing():
    cmd = claude_cli.exec_command("{}", "claude-sonnet-5", "low")
    assert cmd[:3] == ["claude", "-p", "--output-format"] and "--bare" not in cmd
    assert cmd[cmd.index("--tools") + 1] == "" and "--json-schema" in cmd and "--no-session-persistence" in cmd
    ok = json.dumps({"is_error": False, "structured_output": {"summary": "s", "findings": []}, "result": "{}"})
    assert claude_cli.parse_result(ok) == {"summary": "s", "findings": []}
    text_only = json.dumps({"is_error": False, "result": "```json\n{\"summary\": \"t\", \"findings\": []}\n```"})
    assert claude_cli.parse_result(text_only)["summary"] == "t"
    with pytest.raises(ProviderError, match="not signed in"):
        claude_cli.parse_result(json.dumps({"is_error": True, "result": "Not logged in · Please run /login"}))


def test_parse_choice():
    assert providers.parse_choice("claude:claude-opus-5") == ("claude", "claude-opus-5")
    assert providers.parse_choice("gpt-6-astra") == ("codex", "gpt-6-astra")
    with pytest.raises(ProviderError):
        providers.parse_choice("gemini:x")


def test_run_dispatches_to_claude(monkeypatch):
    conn = connect(":memory:")
    init_db(conn)
    repo.seed_passes(conn)
    d = repo.create_document(conn, "T", {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "It illuminated a wall."}]}]})
    monkeypatch.setattr(claude_cli, "status", lambda force=False: {"installed": True, "signed_in": True, "version": "t", "message": ""})
    monkeypatch.setattr(codex, "status", lambda force=False: {"installed": True, "signed_in": False, "version": "t", "message": ""})
    calls = []

    def fake(prompt, schema, **kw):
        calls.append(kw)
        return {"summary": "ok", "findings": [{"paragraph": 1, "quote": "illuminated", "rule": None, "kind": "suggest",
                                                 "replacement": "lit", "comment": "plainer", "confidence": "high"}]}

    monkeypatch.setattr(claude_cli, "run_exec", fake)
    with pytest.raises(runs.RunError, match="Codex is not signed in"):
        runs.start_run(conn, d["id"], "copy-edit", provider="codex", model="gpt-6-astra", effort="low")
    run = runs.start_run(conn, d["id"], "copy-edit", provider="claude", model="claude-sonnet-5", effort="low")
    assert run["provider"] == "claude"
    runs.execute_run(run["id"], conn=conn)
    run = repo.get_run(conn, run["id"])
    assert run["status"] == "done" and run["findings_count"] == 1, run["error"]
    assert calls[0]["model"] == "claude-sonnet-5" and calls[0]["effort"] == "low"
    conn.close()


@pytest.mark.parametrize("provider", [codex, claude_cli])
def test_repeated_login_returns_active_state_without_locking_itself(provider, monkeypatch):
    monkeypatch.setattr(provider, "_login", {
        "proc": None, "url": "https://example.test/login", "code": "ABCD-EFGH",
        "lines": [], "done": False, "ok": False, "error": "",
    })
    monkeypatch.setattr(provider.subprocess, "Popen", lambda *a, **kw: pytest.fail("started another login"))
    result = []
    worker = threading.Thread(target=lambda: result.append(provider.start_login()), daemon=True)
    worker.start()
    worker.join(timeout=0.5)
    assert not worker.is_alive(), "start_login deadlocked while reading the active login state"
    assert result == [{"active": True, "url": "https://example.test/login", "code": "ABCD-EFGH",
                       "ok": False, "error": ""}]
