import importlib

from app import repo
from app.db import connect, init_db


def test_seed_is_complete_and_idempotent(capsys):
    seed = importlib.import_module("scripts.seed")
    conn = connect()
    init_db(conn)
    conn.execute("DELETE FROM documents WHERE title = ?", (seed.TITLE,))
    conn.execute("DELETE FROM rules")
    conn.close()

    seed.main()
    seed.main()
    out = capsys.readouterr().out
    assert "Seeded" in out and "already exists" in out

    conn = connect()
    doc = conn.execute("SELECT * FROM documents WHERE title = ?", (seed.TITLE,)).fetchone()
    anns = repo.list_annotations(conn, doc["id"])
    by_kind = {a["kind"]: 0 for a in anns}
    for a in anns:
        by_kind[a["kind"]] += 1
    assert by_kind == {"note": 2, "suggest": 3, "cut": 1, "question": 1, "check": 1}
    assert all(a["author"] == "you" and a["kind"] == "note" for a in anns if a["author"] == "you")
    assert {a["status"] for a in anns if a["author"] == "ai"} == {"open", "dismissed"}
    dismissed = next(a for a in anns if a["status"] == "dismissed")
    assert dismissed["dismiss_reason"] == "style" and dismissed["suggested_text"]
    check = next(a for a in anns if a["kind"] == "check")
    assert check["confidence"] == "low" and check["quote"] == "four hours"
    linked = [a for a in anns if a["rule_number"]]
    assert {a["rule_number"] for a in linked} == {1, 2, 4, 6}

    run = repo.latest_run(conn, doc["id"])
    assert run["status"] == "done" and run["provider"] == "codex" and run["findings_count"] == 6
    assert run["summary"].startswith("Checked 6 rules")
    assert [r["seq"] for r in repo.list_revisions(conn, doc["id"])] == [2, 1]
    assert repo.get_revision(conn, doc["id"], run["revision_id"])["is_major"] == 1
    assert len(repo.list_rules(conn)) == 6 and sum(r["fired_count"] for r in repo.list_rules(conn)) == 4
    assert {p["slug"] for p in repo.list_passes(conn)} >= {"copy-edit", "fact-check"}
    conn.close()
