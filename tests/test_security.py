import re

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _client(base_url="http://localhost:8000", client=("127.0.0.1", 50000)):
    return TestClient(app, base_url=base_url, client=client)


def _token(client):
    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    return re.search(r'<meta name="write-csrf-token" content="([^"]+)">', page.text).group(1)


def test_local_hosts_and_clients():
    for host, peer in [("localhost:8000", "127.0.0.1"),
                       ("127.0.0.1:8000", "127.0.0.1"),
                       ("[::1]:8000", "::1")]:
        with _client(client=(peer, 50000)) as client:
            assert client.get("/", headers={"Host": host}).status_code == 200
    with _client() as client:
        for host in ("evil.example:8000", "localhost.evil.example:8000", "127.0.0.2:8000",
                     "[::ffff:127.0.0.1]:8000", "localhost:0", "localhost:99999"):
            assert client.get("/", headers={"Host": host}).status_code == 400
        assert client.get("/", headers=[("Host", "localhost:8000"), ("Host", "localhost:8000")]).status_code == 400
    with _client(client=("192.0.2.4", 50000)) as client:
        assert client.get("/").status_code == 400


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": "null"},
    {"Origin": "http://localhost:8001"},
    {"Origin": "http://127.0.0.1:8000"},
    {"Origin": "https://localhost:8000"},
    {"Origin": "http://localhost:8000/path"},
    {"Referer": "http://evil.example/page"},
    {"Referer": "http://localhost:8001/page"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Sec-Fetch-Site": "none"},
])
def test_write_rejects_foreign_browser_context(headers):
    with _client() as client:
        token = _token(client)
        response = client.post("/documents", data={"title": "Must not exist"},
                               headers={"X-Write-CSRF": token, **headers}, follow_redirects=False)
        assert response.status_code == 403


def test_write_requires_token_and_allows_same_origin_and_ordinary_form():
    with _client() as client:
        token = _token(client)
        assert client.post("/documents", data={"title": "No token"}).status_code == 403
        assert client.post("/documents", data={"title": "Bad token"},
                           headers={"X-Write-CSRF": "invalid"}).status_code == 403
        assert client.post("/documents", data={"title": "Unicode token", "_csrf": "é"}).status_code == 403
        response = client.post("/documents", data={"title": "Normal form", "_csrf": token},
                               headers={"Origin": "http://localhost:8000", "Sec-Fetch-Site": "same-origin"},
                               follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/d/")
        response = client.post("/documents", data={"title": "API caller"},
                               headers={"X-Write-CSRF": token}, follow_redirects=False)
        assert response.status_code == 303
        response = client.post("/documents", data={"title": "With referer"},
                               headers={"X-Write-CSRF": token,
                                        "Referer": "http://localhost:8000/settings"}, follow_redirects=False)
        assert response.status_code == 303
