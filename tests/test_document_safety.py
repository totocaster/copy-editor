"""Document versions protect edits, restores, exports, and queued AI inputs."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

from fastapi.testclient import TestClient

from app import repo
from app.db import connect, init_db
from app.main import app
from test_app import client, make_doc, save_doc, version_data


def content(text):
    return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


def test_stale_tab_is_rejected_without_overwriting_text_or_title(client):
    doc_id = make_doc(client)
    stale = version_data(client, doc_id, content=content("Second tab"), title="Old title")
    saved = save_doc(client, doc_id, content("First tab's new work"), title="New title")
    assert saved.status_code == 200 and saved.json()["version"] == stale["base_version"] + 1
    conflict = client.put(f"/d/{doc_id}/content", json=stale)
    assert conflict.status_code == 409
    assert conflict.json()["version"] == saved.json()["version"]
    state = client.get(f"/d/{doc_id}/content").json()
    assert state["content"] == content("First tab's new work") and state["title"] == "New title"
    assert client.put(f"/d/{doc_id}/title", data={"title": "Stale title", "base_version": stale["base_version"]}).status_code == 409


def test_simultaneous_saves_have_one_winner(client):
    doc_id = make_doc(client)
    version = version_data(client, doc_id)["base_version"]
    def write(text):
        with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000),
                        headers=dict(client.headers)) as tab:
            return tab.put(f"/d/{doc_id}/content", json={"content": content(text), "base_version": version})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(write, ["Tab one", "Tab two"]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert client.get(f"/d/{doc_id}/content").json()["version"] == version + 1


def test_missing_version_cannot_silently_overwrite(client):
    doc_id = make_doc(client)
    assert client.put(f"/d/{doc_id}/content", json={"content": content("No version")}).status_code == 422
    assert client.put(f"/d/{doc_id}/content", json={"content": content("Bad version"), "base_version": True}).status_code == 422
    assert client.put(f"/d/{doc_id}/title", data={"title": "No version"}).status_code == 422


def test_recovering_draft_preserves_replaced_text(client):
    doc_id = make_doc(client)
    save_doc(client, doc_id, content("Saved in another tab"))
    response = save_doc(client, doc_id, content("Recovered writing"), preserve_before=True)
    assert response.status_code == 200
    with closing(connect()) as conn:
        rows = conn.execute("SELECT content_text, note FROM revisions WHERE document_id = ?", (doc_id,)).fetchall()
    assert any(row["content_text"] == "Saved in another tab" and row["note"] == "Before recovering a draft" for row in rows)


def test_restore_invalidates_open_tabs_and_stale_actions_are_rejected(client):
    doc_id = make_doc(client)
    with closing(connect()) as conn:
        first = repo.latest_revision(conn, doc_id)["id"]
    old = save_doc(client, doc_id, content("Before restore")).json()["version"]
    restored = client.post(f"/d/{doc_id}/revisions/{first}/restore", data={"base_version": old}, follow_redirects=False)
    assert restored.status_code == 303
    assert client.get(f"/d/{doc_id}/content").json()["version"] == old + 1
    assert client.put(f"/d/{doc_id}/content", json={"content": content("Stale tab"), "base_version": old}).status_code == 409
    assert client.post(f"/d/{doc_id}/revisions", data={"base_version": old}).status_code == 409
    assert client.post(f"/d/{doc_id}/revisions/{first}/restore", data={"base_version": old}).status_code == 409
    assert client.post(f"/d/{doc_id}/runs", data={"base_version": old, "pass_id": "copy-edit"}).status_code == 409
    assert client.get(f"/d/{doc_id}/export.md", params={"base_version": old}).status_code == 409


def test_version_migration_preserves_existing_documents(tmp_path):
    conn = connect(tmp_path / "legacy.db")
    conn.executescript("""
        CREATE TABLE documents (id TEXT PRIMARY KEY, title TEXT NOT NULL, content_json TEXT NOT NULL,
          content_text TEXT NOT NULL, word_count INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    """)
    now = repo.iso(repo.utcnow())
    conn.execute("INSERT INTO documents VALUES ('old', 'Old draft', ?, 'Keep this', 2, ?, ?)",
                 (json.dumps(content("Keep this")), now, now))
    init_db(conn)
    init_db(conn)
    row = repo.get_document(conn, "old")
    assert row["content_text"] == "Keep this" and row["version"] == 0 and row["archived_at"] is None
    assert repo.save_content(conn, "old", content("Updated"), expected_version=0)["version"] == 1
    conn.close()
