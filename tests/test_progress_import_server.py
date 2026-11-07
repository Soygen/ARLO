"""Tests for the MetaForge bookmarklet → localhost import bridge."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from arc_helper.progress_import_server import IMPORT_PATH
from arc_helper.progress_import_server import IMPORT_PORT
from arc_helper.progress_import_server import account_sync_last_synced_at
from arc_helper.progress_import_server import account_sync_mtime
from arc_helper.progress_import_server import bookmarklet_js
from arc_helper.progress_import_server import format_import_age
from arc_helper.progress_import_server import format_progress_status
from arc_helper.progress_import_server import install_html
from arc_helper.progress_import_server import parse_synced_at
from arc_helper.progress_import_server import start_import_server
from arc_helper.progress_import_server import stop_import_server

FIXTURE = Path(__file__).parent / "fixtures" / "metaforge_account_sync.json"
DOCS_HTML = Path(__file__).resolve().parents[1] / "docs" / "import-progress.html"


@pytest.fixture
def import_server(tmp_path: Path):
    """Spin up the import server on an ephemeral port; tear down after."""
    stop_import_server()
    imported: list = []
    out = tmp_path / "account-sync.json"
    server = start_import_server(
        on_import=imported.append,
        output_path=out,
        port=0,
    )
    assert server is not None
    host, port = server.server_address
    yield {
        "base": f"http://{host}:{port}",
        "out": out,
        "imported": imported,
        "port": port,
    }
    stop_import_server()


def _request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_docs_html_port_sync():
    assert DOCS_HTML.is_file()
    text = DOCS_HTML.read_text(encoding="utf-8")
    assert f":{IMPORT_PORT}" in text
    assert IMPORT_PATH in text
    assert "Export to ARLO" in text
    assert "javascript:" in text
    # Generated install page should match the live server HTML.
    assert text == install_html()


def test_bookmarklet_js_balanced():
    """Catch f-string brace bugs that break the bookmarklet in the browser."""
    js = bookmarklet_js()
    assert "JSON.stringify(data)," in js
    assert "targetAddressSpace:'loopback'" in js
    assert "JSON.stringify(data)}})" not in js
    # Parens appear inside alert string literals; braces must still balance.
    assert js.count("{") == js.count("}")
    assert js.count("[") == js.count("]")
    assert js.startswith("(async()=>{")
    assert js.endswith("})();")


def test_options_cors_private_network(import_server):
    status, headers, _body = _request(
        import_server["base"] + IMPORT_PATH,
        method="OPTIONS",
        headers={
            "Origin": "https://metaforge.app",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert status == 204
    assert headers.get("Access-Control-Allow-Origin") == "https://metaforge.app"
    assert "POST" in (headers.get("Access-Control-Allow-Methods") or "")
    assert headers.get("Access-Control-Allow-Private-Network") == "true"


def test_post_valid_account_sync(import_server):
    raw = FIXTURE.read_bytes()
    status, headers, body = _request(
        import_server["base"] + IMPORT_PATH,
        method="POST",
        data=raw,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(raw)),
            "Origin": "https://metaforge.app",
        },
    )
    assert status == 200
    assert headers.get("Access-Control-Allow-Origin") == "https://metaforge.app"
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["quests"] >= 1
    assert payload["workshop"] >= 1
    assert import_server["out"].is_file()
    saved = json.loads(import_server["out"].read_text(encoding="utf-8"))
    assert "resources" in saved
    assert len(import_server["imported"]) == 1
    assert "clearer-skies" in import_server["imported"][0].completed_quests


def test_post_invalid_body(import_server):
    raw = b'{"nope": true}'
    status, _headers, body = _request(
        import_server["base"] + IMPORT_PATH,
        method="POST",
        data=raw,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(raw)),
        },
    )
    assert status == 400
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is False


def test_get_install_page(import_server):
    status, headers, body = _request(import_server["base"] + "/")
    assert status == 200
    assert "text/html" in (headers.get("Content-Type") or "")
    text = body.decode("utf-8")
    assert "Export to ARLO" in text
    assert IMPORT_PATH in text


def test_format_import_age():
    now = time.time()
    assert format_import_age(None) == "not imported"
    assert format_import_age(now, now=now) == "just now"
    assert format_import_age(now - 45, now=now) == "45s ago"
    assert format_import_age(now - 180, now=now) == "3m ago"
    assert format_import_age(now - 7200, now=now) == "2h ago"


def test_parse_synced_at():
    assert parse_synced_at(None) is None
    assert parse_synced_at("") is None
    assert parse_synced_at("not-a-date") is None
    ts = parse_synced_at("2026-10-29T08:46:50.19+00:00")
    assert ts is not None
    assert abs(ts - 1793263610.19) < 1.0


def test_format_progress_status():
    now = time.time()
    assert format_progress_status(imported_mtime=None, now=now) == (
        "Progress: not imported"
    )
    assert format_progress_status(imported_mtime=now, now=now) == (
        "Progress: imported just now"
    )
    assert (
        format_progress_status(
            imported_mtime=now - 180,
            metaforge_synced_at=now - 7200,
            now=now,
        )
        == "Progress: imported 3m ago · MetaForge synced 2h ago"
    )


def test_account_sync_mtime(tmp_path: Path):
    path = tmp_path / "account-sync.json"
    assert account_sync_mtime(path) is None
    path.write_text("{}", encoding="utf-8")
    assert account_sync_mtime(path) is not None


def test_account_sync_last_synced_at(tmp_path: Path):
    path = tmp_path / "account-sync.json"
    assert account_sync_last_synced_at(path) is None
    path.write_text("{}", encoding="utf-8")
    assert account_sync_last_synced_at(path) is None
    path.write_text(
        json.dumps({"lastSyncedAt": "2026-10-29T08:46:50.19+00:00"}),
        encoding="utf-8",
    )
    ts = account_sync_last_synced_at(path)
    assert ts is not None
    assert abs(ts - 1793263610.19) < 1.0
