"""Rules, prompts, and the run pipeline with Codex stubbed out."""

import pytest

from app import codex, repo, runs
from app.db import connect, init_db
from app.prompts import FINDINGS_SCHEMA, build_prompt


def para(*runs_):
    return {"type": "paragraph", "content": [r if isinstance(r, dict) else {"type": "text", "text": r} for r in runs_]}


def doc(*blocks):
    return {"type": "doc", "content": list(blocks)}


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    repo.seed_passes(c)
    yield c
    c.close()


@pytest.fixture
def signed_in(monkeypatch):
    monkeypatch.setattr(codex, "status", lambda force=False: {"installed": True, "signed_in": True, "version": "t", "message": ""})


def test_rules_import_export_and_numbering(conn):
    added = repo.import_rules(conn, "Oxford comma.\nPunctuation: No em dashes.\n\n- Oxford comma.\n")
    assert added == 2
    rules = repo.list_rules(conn)
    assert [r["number"] for r in rules] == [1, 2]
    assert rules[1]["tag"] == "Punctuation" and rules[1]["text"] == "No em dashes."
    repo.update_rule(conn, rules[0]["id"], enabled=False)
    assert repo.export_rules(conn) == "# Oxford comma.\nPunctuation: No em dashes.\n"
    repo.move_rule(conn, rules[1]["id"], -1)
    assert [r["number"] for r in repo.list_rules(conn)] == [2, 1]
    repo.delete_rule(conn, rules[0]["id"])
    third = repo.create_rule(conn, "Third")
    assert third["number"] == 3  # numbers are never reused


def test_prompt_contains_rules_dismissals_and_numbered_paragraphs(conn):
    repo.create_rule(conn, "No em dashes.")
    d = repo.create_document(conn, "T", doc(para("one — two"), para("three")))
    ann = repo.create_ai_annotation(conn, d["id"], run_id="r", kind="suggest", quote="one — two", body="x",
                                    suggested_text="one, two", paragraph_hint=1, anchor_from=1, anchor_to=10,
                                    rule_id=None, rule_number=1, confidence="high")
    repo.set_annotation_status(conn, d["id"], ann["id"], "dismissed", "deliberate")
    p = repo.get_pass(conn, "copy-edit")
    from app.content import paragraphs
    text = build_prompt(doc_title="T", pass_row=p, rules=repo.list_rules(conn), window=paragraphs(doc(para("one — two"), para("three"))),
                        window_index=1, window_total=1, dismissed_here=repo.dismissed_findings(conn, d["id"]),
                        dismissed_elsewhere=[])
    assert "R1. No em dashes." in text
    assert '"one — two" → "one, two" — author\'s reason: deliberate' in text
    assert "[1] one — two" in text and "[2] three" in text
    assert "senior professional copy editor" in text
    assert FINDINGS_SCHEMA["required"] == ["summary", "findings"]


def test_run_pipeline_places_findings_and_suppresses_repeats(conn, monkeypatch, signed_in):
    rule = repo.create_rule(conn, "No em dashes.")
    d = repo.create_document(conn, "T", doc(para("She read the letters twice — and put them away."), para("It illuminated a wall.")))
    reply = {
        "summary": "One dash, one lofty word.",
        "findings": [
            {"paragraph": 1, "quote": "twice — and", "rule": 1, "kind": "suggest", "replacement": "twice and",
             "comment": "Rule 1.", "confidence": "high"},
            {"paragraph": 2, "quote": "illuminated", "rule": None, "kind": "suggest", "replacement": "lit",
             "comment": "Plainer.", "confidence": "medium"},
            {"paragraph": 2, "quote": "not in the text", "rule": 99, "kind": "praise", "replacement": None,
             "comment": "?", "confidence": "low"},
        ],
    }
    calls = []

    def fake_exec(prompt, schema, **kw):
        calls.append((prompt, kw))
        return reply

    monkeypatch.setattr(codex, "run_exec", fake_exec)

    run = runs.start_run(conn, d["id"], "copy-edit", model="m", effort="low")
    assert run["status"] == "queued" and run["revision_id"]
    runs.execute_run(run["id"], conn=conn)

    run = repo.get_run(conn, run["id"])
    assert run["status"] == "done", run["error"]
    assert run["summary"] == "One dash, one lofty word."
    assert calls[0][1] == {"model": "m", "effort": "low", "on_start": calls[0][1]["on_start"]}
    anns = [a for a in repo.list_annotations(conn, d["id"]) if a["author"] == "ai"]
    assert len(anns) == 3 and all(a["status"] == "pending" for a in anns)
    by_quote = {a["quote"]: a for a in anns}
    assert by_quote["twice — and"]["rule_number"] == 1 and by_quote["twice — and"]["anchor_from"] == 22
    assert by_quote["illuminated"]["rule_id"] is None and by_quote["illuminated"]["suggested_text"] == "lit"
    # praise is not an allowed kind for copy edit, so it becomes a note; unknown rule 99 -> general
    assert by_quote["not in the text"]["kind"] == "note" and by_quote["not in the text"]["rule_number"] is None
    assert repo.get_rule(conn, rule["id"])["fired_count"] == 1
    assert run["findings_count"] == 3

    # Dismiss one, re-run: the dismissed finding is suppressed and appears in the prompt.
    repo.set_annotation_status(conn, d["id"], by_quote["illuminated"]["id"], "dismissed", "style")
    run2 = runs.start_run(conn, d["id"], "copy-edit", model="m", effort="low")
    runs.execute_run(run2["id"], conn=conn)
    run2 = repo.get_run(conn, run2["id"])
    assert run2["status"] == "done"
    assert run2["findings_count"] == 0 and run2["suppressed_count"] == 3  # two still open, one dismissed
    assert '"illuminated" → "lit" — author\'s reason: not my style' in calls[1][0]


def test_pending_becomes_open_when_mark_appears(conn):
    d = repo.create_document(conn, "T", doc(para("keep this")))
    ann = repo.create_ai_annotation(conn, d["id"], run_id="r", kind="note", quote="this", body="b", suggested_text="",
                                    paragraph_hint=1, anchor_from=6, anchor_to=10, rule_id=None, rule_number=None,
                                    confidence="")
    # Saving unchanged content leaves a pending annotation alone (not orphaned).
    result = repo.save_content(conn, d["id"], doc(para("keep this")))
    assert result["annotation_changes"] == []
    assert repo.get_annotation(conn, d["id"], ann["id"])["status"] == "pending"
    marked = {"type": "text", "text": "this", "marks": [{"type": "annotation", "attrs": {"id": ann["id"], "kind": "note"}}]}
    result = repo.save_content(conn, d["id"], doc(para("keep ", marked)))
    assert result["annotation_changes"] == [{"id": ann["id"], "status": "open"}]


def test_run_refuses_when_signed_out_or_busy(conn, monkeypatch):
    d = repo.create_document(conn, "T", doc(para("x")))
    monkeypatch.setattr(codex, "status", lambda force=False: {"installed": True, "signed_in": False, "version": "", "message": ""})
    with pytest.raises(runs.RunError):
        runs.start_run(conn, d["id"], "copy-edit", model="", effort="low")
    monkeypatch.setattr(codex, "status", lambda force=False: {"installed": True, "signed_in": True, "version": "", "message": ""})
    runs.start_run(conn, d["id"], "copy-edit", model="", effort="low", snapshot=False)
    with pytest.raises(runs.RunError):
        runs.start_run(conn, d["id"], "copy-edit", model="", effort="low", snapshot=False)


def test_codex_parse_and_command():
    assert codex.parse_json_output('```json\n{"a": 1}\n```') == {"a": 1}
    assert codex.parse_json_output('noise {"a": 2} trailing') == {"a": 2}
    cmd = codex.exec_command("s", "o", "/w", "gpt-x", "low")
    assert cmd[:3] == ["codex", "exec", "--ephemeral"] and "-s" in cmd and "read-only" in cmd
    assert cmd[-2:] == ["-c", 'model_reasoning_effort="low"']


def test_check_kind_keeps_correction_and_is_seeded(conn):
    p = repo.get_pass(conn, "copy-edit")
    assert "check" in p["allowed_kinds"].split(",")
    assert repo.get_pass(conn, "fact-check")["allowed_kinds"] == "check,note"
    # seeding again adds nothing and changes nothing
    before = [(r["slug"], r["is_primary"]) for r in repo.list_passes(conn)]
    repo.seed_passes(conn)
    assert [(r["slug"], r["is_primary"]) for r in repo.list_passes(conn)] == before

    d = repo.create_document(conn, "T", doc(para("Paris is the capital of Spain.")))
    from app.content import paragraphs
    paras = paragraphs(doc(para("Paris is the capital of Spain.")))
    ann = runs.materialise_finding(conn, d["id"], "run", {"paragraph": 1, "quote": "Spain", "rule": None, "kind": "check",
                                                          "replacement": "France", "comment": "Wrong country.",
                                                          "confidence": "high"},
                                   paras=paras, rules_by_number={}, allowed=["check", "note"], general_ok=True, known={})
    assert ann["kind"] == "check" and ann["suggested_text"] == "France" and ann["confidence"] == "high"
    unsure = runs.materialise_finding(conn, d["id"], "run", {"paragraph": 1, "quote": "capital", "rule": None, "kind": "check",
                                                             "replacement": None, "comment": "Verify.", "confidence": "low"},
                                      paras=paras, rules_by_number={}, allowed=["check", "note"], general_ok=True, known={})
    assert unsure["suggested_text"] == ""


def test_migration_adds_check_to_existing_copy_edit():
    from app.db import connect, init_db
    c = connect(":memory:")
    init_db(c)
    c.execute("INSERT INTO passes (id, slug, name, allowed_kinds, created_at, updated_at) VALUES ('x', 'copy-edit', 'Copy edit', 'suggest,cut,question,note', '', '')")
    init_db(c)  # migrate again
    assert c.execute("SELECT allowed_kinds FROM passes WHERE id = 'x'").fetchone()[0] == "suggest,cut,question,note,check"
    c.close()
