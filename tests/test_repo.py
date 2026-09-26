from datetime import timedelta

import pytest

from app import repo
from app.db import connect, init_db


def para(*runs):
    return {"type": "paragraph", "content": [r if isinstance(r, dict) else {"type": "text", "text": r} for r in runs]}


def doc(*blocks):
    return {"type": "doc", "content": list(blocks)}


def marked(s, aid):
    return {"type": "text", "text": s, "marks": [{"type": "annotation", "attrs": {"id": aid, "kind": "note"}}]}


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    yield c
    c.close()


def backdate(conn, doc_id, minutes):
    old = repo.iso(repo.utcnow() - timedelta(minutes=minutes))
    conn.execute("UPDATE documents SET updated_at = ? WHERE id = ?", (old, doc_id))
    conn.execute("UPDATE revisions SET created_at = ? WHERE document_id = ?", (old, doc_id))


def test_create_makes_first_revision(conn):
    d = repo.create_document(conn, "T", doc(para("one two")))
    revs = repo.list_revisions(conn, d["id"])
    assert [r["seq"] for r in revs] == [1]
    assert revs[0]["origin"] == "create"
    assert d["word_count"] == 2


def test_archive_filters_documents_without_changing_their_content(conn):
    active = repo.create_document(conn, "Active", doc(para("keep writing")))
    archived = repo.create_document(conn, "Archived", doc(para("still editable")))

    repo.set_document_archived(conn, archived["id"], True)

    assert [row["id"] for row in repo.list_documents(conn)] == [active["id"]]
    assert [row["id"] for row in repo.list_documents(conn, archived=True)] == [archived["id"]]
    assert repo.get_document(conn, archived["id"])["content_text"] == "still editable"
    assert repo.document_counts(conn) == {"active": 1, "archived": 1}

    repo.save_content(conn, archived["id"], doc(para("edited in archive")))
    repo.set_document_archived(conn, archived["id"], False)
    assert repo.get_document(conn, archived["id"])["archived_at"] is None
    assert repo.get_document(conn, archived["id"])["content_text"] == "edited in archive"


def test_rapid_edits_do_not_snapshot(conn):
    d = repo.create_document(conn, "T", doc(para("one")))
    result = repo.save_content(conn, d["id"], doc(para("one two")))
    assert result["changed"] and result["revision"] is None
    assert len(repo.list_revisions(conn, d["id"])) == 1
    assert repo.get_document(conn, d["id"])["word_count"] == 2


def test_session_gap_snapshots_previous_state(conn):
    d = repo.create_document(conn, "T", doc(para("one")))
    repo.save_content(conn, d["id"], doc(para("one two")))
    backdate(conn, d["id"], minutes=15)
    result = repo.save_content(conn, d["id"], doc(para("one two three")))
    rev = result["revision"]
    assert rev is not None and rev["origin"] == "auto" and rev["note"] == "End of session"
    assert rev["content_text"] == "one two"  # what the document looked like before this edit
    assert repo.get_document(conn, d["id"])["content_text"] == "one two three"


def test_long_session_checkpoints(conn):
    d = repo.create_document(conn, "T", doc(para("one")))
    conn.execute("UPDATE revisions SET created_at = ? WHERE document_id = ?",
                 (repo.iso(repo.utcnow() - timedelta(minutes=45)), d["id"]))
    result = repo.save_content(conn, d["id"], doc(para("one two")))
    assert result["revision"]["note"] == "Checkpoint"
    assert result["revision"]["content_text"] == "one two"


def test_snapshot_promotes_identical_latest(conn):
    d = repo.create_document(conn, "T", doc(para("one")))
    rev = repo.snapshot(conn, d["id"], note="first", is_major=True)
    assert rev["seq"] == 1 and rev["is_major"] == 1 and rev["note"] == "first" and rev["origin"] == "manual"
    repo.save_content(conn, d["id"], doc(para("one two")))
    rev2 = repo.snapshot(conn, d["id"], note="second")
    assert rev2["seq"] == 2 and rev2["is_major"] == 0


def test_restore_keeps_current_and_records_restore(conn):
    d = repo.create_document(conn, "T", doc(para("one")))
    first = repo.latest_revision(conn, d["id"])
    repo.save_content(conn, d["id"], doc(para("one two")))
    repo.restore_revision(conn, d["id"], first["id"])
    revs = repo.list_revisions(conn, d["id"])
    assert [r["origin"] for r in revs] == ["restore", "auto", "create"]
    assert repo.get_document(conn, d["id"])["content_text"] == "one"


def test_annotation_sync_orphans_and_reopens(conn):
    d = repo.create_document(conn, "T", doc(para("keep ", marked("this", "a1"))))
    repo.create_annotation(conn, d["id"], "a1", "note", "this", 6, 10)
    repo.sync_annotations(conn, d["id"], {"a1": {"from": 6, "to": 10, "kind": "note", "quote": "this"}})
    assert repo.get_annotation(conn, d["id"], "a1")["status"] == "open"

    result = repo.save_content(conn, d["id"], doc(para("keep that")))
    assert result["annotation_changes"] == [{"id": "a1", "status": "orphaned"}]

    result = repo.save_content(conn, d["id"], doc(para("keep ", marked("this again", "a1"))))
    assert result["annotation_changes"] == [{"id": "a1", "status": "open"}]
    a = repo.get_annotation(conn, d["id"], "a1")
    assert a["quote"] == "this again" and a["anchor_from"] == 6 and a["anchor_to"] == 16


def test_closed_annotations_are_left_alone(conn):
    d = repo.create_document(conn, "T", doc(para("x")))
    repo.create_annotation(conn, d["id"], "a1", "suggest", "x", 1, 2)
    repo.set_annotation_status(conn, d["id"], "a1", "accepted")
    repo.save_content(conn, d["id"], doc(para("y")))
    assert repo.get_annotation(conn, d["id"], "a1")["status"] == "accepted"
