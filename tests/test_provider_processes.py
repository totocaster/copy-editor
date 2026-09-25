"""Exercise the real Popen drivers with disposable stand-in CLIs."""

import json
import textwrap

import pytest

from app import claude_cli, codex


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    script = tmp_path / "fake_cli"
    script.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import json
        import os
        import sys
        import time
        from pathlib import Path

        args = sys.argv[1:]
        prompt = sys.stdin.read()
        record = {
            "args": args, "prompt": prompt, "cwd": os.getcwd(),
            "claudecode": os.environ.get("CLAUDECODE"),
        }
        if "--output-schema" in args:
            schema_path = Path(args[args.index("--output-schema") + 1])
            record["schema"] = json.loads(schema_path.read_text())
            record["workdir_exists"] = Path(args[args.index("-C") + 1]).is_dir()
        if "--json-schema" in args:
            record["schema"] = json.loads(args[args.index("--json-schema") + 1])
        Path(os.environ["FAKE_RECORD"]).write_text(json.dumps(record))

        mode = os.environ.get("FAKE_MODE", "ok")
        if mode == "sleep":
            time.sleep(30)
        elif mode == "error":
            # Valid-looking JSON must never turn a failed CLI into a successful pass.
            print(json.dumps({"structured_output": {"summary": "false success"}}))
            print("deliberate failure", file=sys.stderr)
            sys.exit(9)
        elif "--output-schema" in args:
            Path(args[args.index("-o") + 1]).write_text(json.dumps({"summary": "codex ok", "findings": []}))
        else:
            print(json.dumps({"is_error": False, "structured_output": {"summary": "claude ok", "findings": []}}))
        """))
    script.chmod(0o755)
    record_path = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_RECORD", str(record_path))
    monkeypatch.setattr(codex, "CODEX_BIN", str(script))
    monkeypatch.setattr(claude_cli, "CLAUDE_BIN", str(script))
    return record_path


@pytest.mark.parametrize("provider,expected", [(codex, "codex ok"), (claude_cli, "claude ok")])
def test_driver_executes_with_schema_prompt_and_isolation(fake_cli, monkeypatch, provider, expected):
    monkeypatch.setenv("CLAUDECODE", "nested session")
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
    started = []
    result = provider.run_exec("Edit this sentence.", schema, model="model-test", effort="high",
                               on_start=started.append)
    record = json.loads(fake_cli.read_text())
    args = record["args"]
    assert result["summary"] == expected
    assert record["prompt"] == "Edit this sentence."
    assert record["schema"] == schema
    assert len(started) == 1 and started[0].returncode == 0
    if provider is codex:
        assert args[:2] == ["exec", "--ephemeral"]
        assert args[args.index("-s") + 1] == "read-only"
        assert args[args.index("-m") + 1] == "model-test"
        assert args[args.index("-c") + 1] == 'model_reasoning_effort="high"'
        assert record["workdir_exists"]
    else:
        assert args[:4] == ["-p", "--output-format", "json", "--json-schema"]
        assert args[args.index("--tools") + 1] == ""
        assert args[args.index("--model") + 1] == "model-test"
        assert args[args.index("--effort") + 1] == "high"
        assert "--no-session-persistence" in args
        assert "--max-turns" not in args
        assert record["claudecode"] is None


@pytest.mark.parametrize("provider", [codex, claude_cli])
def test_driver_rejects_nonzero_exit_even_with_valid_json(fake_cli, monkeypatch, provider):
    monkeypatch.setenv("FAKE_MODE", "error")
    with pytest.raises(provider.CodexError if provider is codex else provider.ClaudeError,
                       match="deliberate failure"):
        provider.run_exec("Prompt", {"type": "object"})


@pytest.mark.parametrize("provider", [codex, claude_cli])
def test_driver_reaps_timed_out_process(fake_cli, monkeypatch, provider):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    started = []
    with pytest.raises(provider.CodexError if provider is codex else provider.ClaudeError,
                       match="timed out"):
        provider.run_exec("Prompt", {"type": "object"}, timeout=0.2, on_start=started.append)
    assert len(started) == 1
    assert started[0].poll() is not None
