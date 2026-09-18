import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def make_doc(client):
    r = client.post("/documents", data={"title": "Draft"}, headers={"HX-Request": "true"})
    assert r.status_code == 204
    return r.headers["HX-Redirect"].rsplit("/", 1)[1]


def test_index_and_create(client):
    doc_id = make_doc(client)
    r = client.get("/")
    assert r.status_code == 200 and "Draft" in r.text
    r = client.get(f"/d/{doc_id}")
    assert r.status_code == 200 and 'id="editor"' in r.text and "workshop-data" in r.text


def test_content_save_and_annotation_lifecycle(client):
    doc_id = make_doc(client)
    content = {"type": "doc", "content": [{"type": "paragraph", "content": [
        {"type": "text", "text": "hello "},
        {"type": "text", "text": "world", "marks": [{"type": "annotation", "attrs": {"id": "abcdefgh1234", "kind": "suggest"}}]},
    ]}]}
    r = client.post(f"/d/{doc_id}/annotations", json={"id": "abcdefgh1234", "kind": "suggest", "quote": "world",
                                                      "anchor_from": 7, "anchor_to": 12})
    assert r.status_code == 201 and r.json()["kind"] == "note"  # authors write notes
    r = client.put(f"/d/{doc_id}/content", json={"content": content})
    assert r.status_code == 200 and r.json()["word_count"] == 2

    r = client.get(f"/d/{doc_id}/annotations", params={"editing": "abcdefgh1234"})
    assert r.status_code == 200 and "is-editing" in r.text

    r = client.put(f"/d/{doc_id}/annotations/abcdefgh1234",
                   data={"kind": "suggest", "body": "Too plain", "suggested_text": "planet"})
    assert r.status_code == 200 and "Too plain" in r.text and "planet" not in r.text
    assert "annotation:changed" in r.headers["HX-Trigger"]

    r = client.post(f"/d/{doc_id}/annotations/abcdefgh1234/status", data={"status": "resolved"})
    assert r.status_code == 200 and "Closed · 1" in r.text
    assert '"status": "resolved"' in r.headers["HX-Trigger"]

    r = client.delete(f"/d/{doc_id}/annotations/abcdefgh1234")
    assert r.status_code == 200 and "Closed" not in r.text


def test_revisions_flow(client):
    doc_id = make_doc(client)
    r = client.put(f"/d/{doc_id}/content", json={"content": {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "second draft"}]}]}})
    assert r.status_code == 200
    r = client.post(f"/d/{doc_id}/revisions", data={"note": "Big rewrite", "is_major": "1"}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "Big rewrite" in r.text and "Major" in r.text

    r = client.get(f"/d/{doc_id}/revisions", headers={"HX-Request": "true"})
    assert "Revision 2" in r.text
    rev_id = r.text.split('id="rev-')[1].split('"')[0]

    r = client.post(f"/d/{doc_id}/revisions/{rev_id}/major")
    assert r.status_code == 200 and 'aria-pressed="false"' in r.text

    r = client.get(f"/d/{doc_id}/r/{rev_id}", params={"against": "prev"})
    assert r.status_code == 200 and "<ins>" in r.text and "Compared with revision 1" in r.text

    r = client.post(f"/d/{doc_id}/revisions/{rev_id}/restore", headers={"HX-Request": "true"})
    assert r.status_code == 204 and r.headers["HX-Redirect"] == f"/d/{doc_id}"
    r = client.get(f"/d/{doc_id}/revisions", headers={"HX-Request": "true"})
    assert "Revision 3" in r.text and "restored" in r.text


def test_delete_document(client):
    doc_id = make_doc(client)
    assert client.delete(f"/documents/{doc_id}").status_code == 200
    assert client.get(f"/d/{doc_id}").status_code == 404


def test_settings_and_rules_routes(client):
    r = client.get("/settings?tab=rules")
    assert r.status_code == 200 and "Rules" in r.text
    r = client.post("/settings/rules", data={"text": "No em dashes.", "tag": "Punctuation"})
    assert r.status_code == 200 and "No em dashes." in r.text
    rule_id = r.text.split('id="rule-')[1].split('"')[0]
    r = client.post(f"/settings/rules/{rule_id}/toggle")
    assert 'is-off' in r.text
    r = client.put(f"/settings/rules/{rule_id}", data={"text": "No em dashes, ever."})
    assert "No em dashes, ever." in r.text
    r = client.get("/settings/rules/export")
    assert r.text == "# Punctuation: No em dashes, ever.\n"
    r = client.get("/settings/passes", headers={"HX-Request": "true"})
    assert "Copy edit" in r.text and 'name="instructions"' in r.text
    r = client.get("/settings/account", headers={"HX-Request": "true"})
    assert r.status_code == 200 and "Account" in r.text


def test_run_menu_and_start(client, monkeypatch):
    from app import codex, runs
    monkeypatch.setattr(codex, "status", lambda force=False: {"installed": True, "signed_in": True, "version": "t", "message": ""})
    monkeypatch.setattr(runs, "launch", lambda run_id: None)
    doc_id = make_doc(client)
    monkeypatch.setattr(codex, "models", lambda force=False: [{"id": "gpt-x", "label": "GPT X", "description": "", "efforts": ["low", "high"], "default_effort": "low"}])
    r = client.get(f"/d/{doc_id}/runs/menu")
    assert r.status_code == 200 and "Run pass" in r.text and 'name="pass_id"' in r.text
    assert '<optgroup label="OpenAI · Codex CLI">' in r.text and 'value="codex:gpt-x"' in r.text
    assert "Anthropic · Claude Code" in r.text
    pass_id = r.text.split('name="pass_id" value="')[1].split('"')[0]
    r = client.post(f"/d/{doc_id}/runs", data={"pass_id": pass_id, "choice": "codex:gpt-x", "effort": "medium", "snapshot": "1"})
    assert r.status_code == 200 and "starting" in r.text and "run:started" in r.headers["HX-Trigger"]
    assert "gpt-x · low" in r.text  # medium is not offered by this model, so its default applies
    run_id = r.text.split('data-run="')[1].split('"')[0]
    r = client.get(f"/d/{doc_id}/runs/{run_id}/strip")
    assert 'hx-trigger="every 2s"' in r.text
    r = client.post(f"/d/{doc_id}/runs/{run_id}/cancel")
    assert "cancelled" in r.text and "every 2s" not in r.text
    r = client.get("/settings/runs", headers={"HX-Request": "true"})
    assert "cancelled" in r.text
    r = client.post(f"/d/{doc_id}/annotations/bulk_status", json={"ids": ["nope"], "status": "orphaned"})
    assert r.json() == {"done": []}


def test_export_routes(client):
    doc_id = make_doc(client)
    client.put(f"/d/{doc_id}/content", json={"content": {"type": "doc", "content": [
        {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Part one"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "Hello ", }, {"type": "text", "text": "there", "marks": [{"type": "bold"}]}]}]}})
    client.put(f"/d/{doc_id}/title", data={"title": "My Draft: Ünïcode"})
    r = client.get(f"/d/{doc_id}/export.md")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert r.headers["content-disposition"].startswith('attachment; filename="my-draft-n-code.md"')
    assert r.text == "# My Draft: Ünïcode\n\n## Part one\n\nHello **there**\n"
    r = client.get(f"/d/{doc_id}/export.txt", params={"inline": 1})
    assert r.status_code == 200 and "content-disposition" not in r.headers
    assert r.text == "My Draft: Ünïcode\n\nPart one\n\nHello there\n"
    assert client.get(f"/d/{doc_id}/export.pdf").status_code == 404


def test_pass_findings_are_not_editable(client, monkeypatch):
    from app import repo
    from app.db import connect
    doc_id = make_doc(client)
    conn = connect()
    ann = repo.create_ai_annotation(conn, doc_id, run_id="r", kind="suggest", quote="x", body="b", suggested_text="y",
                                    paragraph_hint=1, anchor_from=1, anchor_to=2, rule_id=None, rule_number=None,
                                    confidence="high")
    conn.close()
    assert client.get(f"/d/{doc_id}/annotations/{ann['id']}/edit").status_code == 403
    assert client.put(f"/d/{doc_id}/annotations/{ann['id']}", data={"body": "nope"}).status_code == 403
    r = client.get(f"/d/{doc_id}/annotations")
    assert "Edit" not in r.text.split('id="ann-' + ann["id"])[1].split("</div>\n</div>")[0]
