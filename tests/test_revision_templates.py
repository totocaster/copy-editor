"""Shared history controls must also work on the page without editor.js."""

import re

from fastapi.testclient import TestClient

from app.main import app


def test_standalone_history_carries_version_for_revision_and_restore():
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50001)) as client:
        index = client.get("/")
        client.headers["X-Write-CSRF"] = re.search(
            r'name="write-csrf-token" content="([^"]+)"', index.text
        ).group(1)
        created = client.post("/documents", data={"title": "History fallback"}, headers={"HX-Request": "true"})
        doc_id = created.headers["HX-Redirect"].rsplit("/", 1)[1]
        version = client.get(f"/d/{doc_id}/content").json()["version"]

        page = client.get(f"/d/{doc_id}/revisions")
        assert page.status_code == 200
        assert f'name="base_version" value="{version}"' in page.text
        assert f"hx-vals='{{\"base_version\": {version}}}'" in page.text
        assert "hx-confirm=\"Restore revision" in page.text

        saved = client.post(f"/d/{doc_id}/revisions", data={"base_version": version, "note": "On history page"},
                            headers={"HX-Request": "true"})
        assert saved.status_code == 200 and "On history page" in saved.text

        rev_id = re.search(r'id="rev-([^"]+)"', saved.text).group(1)
        restored = client.post(f"/d/{doc_id}/revisions/{rev_id}/restore",
                               data={"base_version": version}, headers={"HX-Request": "true"})
        assert restored.status_code == 204
        assert restored.headers["HX-Redirect"] == f"/d/{doc_id}"
