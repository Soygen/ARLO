"""Localhost MetaForge → ARLO progress import (bookmarklet bridge).

Listens on 127.0.0.1 only. The bookmarklet runs on metaforge.app (session
cookies), fetches account-sync, and POSTs the JSON here.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import TYPE_CHECKING
from typing import Any
from urllib.parse import urlparse

from arc_helper.config import logger
from arc_helper.progress import ProgressProfile
from arc_helper.progress import _is_account_sync
from arc_helper.progress import import_account_sync
from arc_helper.progress import save_account_sync

if TYPE_CHECKING:
    from pathlib import Path

# Keep in sync with docs/import-progress.html (tests assert the port).
IMPORT_HOST = "127.0.0.1"
IMPORT_PORT = 17832
IMPORT_PATH = "/import-progress"
MAX_BODY_BYTES = 20 * 1024 * 1024
CORS_ORIGIN = "https://metaforge.app"
ACCOUNT_SYNC_URL = "https://metaforge.app/api/arc-raiders/account-sync"

_JUST_NOW_SEC = 10
_TIME_UNIT = 60
_MAX_HOURS_AS_HOURS = 48

OnImport = Callable[[ProgressProfile], None]


class _ImportServerState:
    """Mutable process-wide state for the singleton import server."""

    server: ThreadingHTTPServer | None = None
    thread: threading.Thread | None = None
    on_import: OnImport | None = None
    output_path: Path | None = None


_state = _ImportServerState()


def bookmarklet_js() -> str:
    """Bookmarklet body (without the ``javascript:`` prefix)."""
    # Use plain .replace (not an f-string) so `{` / `}` in JS stay literal.
    # targetAddressSpace:'loopback' is required by Chrome/Firefox Local Network
    # Access so https://metaforge.app may POST to http://127.0.0.1 (mixed content
    # exemption + permission prompt).
    return (
        (
            "(async()=>{"
            "const dl=data=>{"
            "const blob=new Blob([JSON.stringify(data,null,2)],"
            "{type:'application/json'});"
            "const a=document.createElement('a');"
            "a.href=URL.createObjectURL(blob);"
            "a.download='account-sync.json';"
            "document.body.appendChild(a);a.click();a.remove();"
            "URL.revokeObjectURL(a.href);"
            "};"
            "try{"
            "if(navigator.permissions&&navigator.permissions.query){"
            "try{await navigator.permissions.query({name:'loopback-network'});}"
            "catch(_p){}"
            "}"
            "const r=await fetch('__SYNC__',{credentials:'include'});"
            "if(!r.ok){"
            "alert('MetaForge account-sync failed ('+r.status+'). "
            "Log in and Sync your Raider Profile first.');"
            "return;"
            "}"
            "const data=await r.json();"
            "try{"
            "const ir=await fetch('http://127.0.0.1:__PORT____PATH__',{"
            "method:'POST',"
            "headers:{'Content-Type':'application/json'},"
            "body:JSON.stringify(data),"
            "targetAddressSpace:'loopback'"
            "});"
            "if(!ir.ok){alert('ARLO import failed ('+ir.status+')');return;}"
            "const s=await ir.json();"
            "alert('ARLO updated\\nQuests: '+s.quests"
            "+'\\nWorkshop: '+s.workshop"
            "+'\\nProjects: '+s.projects"
            "+'\\nBlueprints: '+s.blueprints);"
            "}catch(e){"
            "dl(data);"
            "alert('Could not reach ARLO on 127.0.0.1:__PORT__.\\n'"
            "+'1) Start ARLO\\n'"
            "+'2) If the browser asks to allow local/loopback network "
            "access for metaforge.app, choose Allow\\n'"
            "+'Downloaded account-sync.json as a fallback.');"
            "}"
            "}catch(e){"
            "alert('Could not reach MetaForge. "
            "Open your Raider Profile while logged in.');"
            "}"
            "})();"
        )
        .replace("__SYNC__", ACCOUNT_SYNC_URL)
        .replace("__PORT__", str(IMPORT_PORT))
        .replace("__PATH__", IMPORT_PATH)
    )


def bookmarklet_href() -> str:
    """Full ``javascript:`` URL suitable for an ``<a href>`` drag target."""
    return "javascript:" + bookmarklet_js()


def install_html() -> str:
    """Install page HTML with a draggable bookmarklet link."""
    href = (
        bookmarklet_href()
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>ARLO — Import MetaForge progress</title>
  <style>
    :root {{
      color-scheme: dark light;
      --bg: #12141a;
      --fg: #e8eaef;
      --muted: #9aa3b2;
      --accent: #5ad4a0;
      --card: #1c2030;
    }}
    body {{
      margin: 0;
      font-family: "Segoe UI", system-ui, sans-serif;
      background: var(--bg);
      color: var(--fg);
      line-height: 1.5;
    }}
    main {{
      max-width: 40rem;
      margin: 0 auto;
      padding: 2.5rem 1.25rem 4rem;
    }}
    h1 {{ font-size: 1.6rem; margin: 0 0 0.5rem; }}
    p {{ color: var(--muted); }}
    ol {{ color: var(--muted); padding-left: 1.25rem; }}
    li {{ margin: 0.4rem 0; }}
    .card {{
      background: var(--card);
      border-radius: 12px;
      padding: 1.25rem;
      margin: 1.5rem 0;
      text-align: center;
    }}
    a.bookmarklet {{
      display: inline-block;
      background: var(--accent);
      color: #0b1210;
      font-weight: 700;
      text-decoration: none;
      padding: 0.85rem 1.4rem;
      border-radius: 999px;
      cursor: grab;
    }}
    .hint {{ font-size: 0.9rem; margin-top: 0.75rem; }}
    code {{
      background: #0d1018;
      padding: 0.1rem 0.35rem;
      border-radius: 4px;
      color: var(--fg);
    }}
  </style>
</head>
<body>
  <main>
    <h1>Export MetaForge progress to ARLO</h1>
    <p>
      Drag the button below onto your bookmarks bar (Chrome or Firefox).
      Then open your MetaForge Raider Profile (after Sync) and click the bookmark.
    </p>
    <div class="card">
      <a class="bookmarklet" href="{href}">Export to ARLO</a>
      <p class="hint">Drag this → bookmarks bar</p>
    </div>
    <ol>
      <li>Start ARLO (import server on <code>127.0.0.1:{IMPORT_PORT}</code>).</li>
      <li>On MetaForge, Sync your Raider Profile.</li>
      <li>Click <strong>Export to ARLO</strong> while on metaforge.app.</li>
    </ol>
    <p>
      ARLO must be running for one-click import. If it is not, the bookmark
      downloads <code>account-sync.json</code> instead.
    </p>
  </main>
</body>
</html>
"""


def format_import_age(mtime: float | None, *, now: float | None = None) -> str:
    """Human age for status flash, e.g. ``just now`` / ``3m ago``."""
    if mtime is None:
        return "not imported"
    import time

    ref = time.time() if now is None else now
    seconds = max(0, int(ref - mtime))
    if seconds < _JUST_NOW_SEC:
        return "just now"
    if seconds < _TIME_UNIT:
        return f"{seconds}s ago"
    minutes = seconds // _TIME_UNIT
    if minutes < _TIME_UNIT:
        return f"{minutes}m ago"
    hours = minutes // _TIME_UNIT
    if hours < _MAX_HOURS_AS_HOURS:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def parse_synced_at(value: object) -> float | None:
    """Parse MetaForge ``lastSyncedAt`` (ISO-8601) to a Unix timestamp."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def account_sync_mtime(path: Path | None = None) -> float | None:
    """mtime of ``account-sync.json``, or None if missing."""
    from arc_helper.progress import _default_path

    target = path if path is not None else _default_path()
    try:
        return target.stat().st_mtime
    except OSError:
        return None


def account_sync_last_synced_at(path: Path | None = None) -> float | None:
    """Unix time of MetaForge ``lastSyncedAt`` in ``account-sync.json``."""
    from arc_helper.progress import _default_path

    target = path if path is not None else _default_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return parse_synced_at(data.get("lastSyncedAt"))


def format_progress_status(
    *,
    imported_mtime: float | None,
    metaforge_synced_at: float | None = None,
    now: float | None = None,
) -> str:
    """Status-pill flash for import age + MetaForge sync age."""
    imported = format_import_age(imported_mtime, now=now)
    if imported == "not imported":
        return "Progress: not imported"
    msg = f"Progress: imported {imported}"
    if metaforge_synced_at is not None:
        synced = format_import_age(metaforge_synced_at, now=now)
        if synced != "not imported":
            msg += f" · MetaForge synced {synced}"
    return msg


def _cors_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
    handler.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Private-Network", "true")
    handler.send_header("Vary", "Origin")


class _ImportHandler(BaseHTTPRequestHandler):
    """HTTP handler for install page + progress import."""

    server_version = "ARLOProgressImport/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug(
            "progress-import [%s]: " + format,
            self.address_string(),
            *args,
        )

    def _send_bytes(
        self,
        code: int,
        body: bytes,
        *,
        content_type: str,
        cors: bool = False,
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cors:
            _cors_headers(self)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload: dict[str, Any], *, cors: bool) -> None:
        body = (json.dumps(payload) + "\n").encode("utf-8")
        self._send_bytes(code, body, content_type="application/json", cors=cors)

    def do_OPTIONS(self) -> None:
        path = urlparse(self.path).path
        if path != IMPORT_PATH:
            self.send_error(404)
            return
        self.send_response(204)
        _cors_headers(self)
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            body = install_html().encode("utf-8")
            self._send_bytes(200, body, content_type="text/html; charset=utf-8")
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != IMPORT_PATH:
            self._send_json(404, {"ok": False, "error": "not found"}, cors=True)
            return

        length_hdr = self.headers.get("Content-Length", "")
        try:
            length = int(length_hdr)
        except ValueError:
            self._send_json(
                400, {"ok": False, "error": "missing Content-Length"}, cors=True
            )
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._send_json(413, {"ok": False, "error": "body too large"}, cors=True)
            return

        raw_bytes = self.rfile.read(length)
        try:
            data = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"ok": False, "error": "invalid JSON"}, cors=True)
            return

        if not _is_account_sync(data):
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": 'expected MetaForge account-sync (top-level "resources")',
                },
                cors=True,
            )
            return

        assert isinstance(data, dict)
        try:
            profile = import_account_sync(data)
            save_account_sync(data, _state.output_path)
        except OSError as exc:
            logger.exception("progress-import: failed to write account-sync.json")
            self._send_json(
                500, {"ok": False, "error": f"write failed: {exc}"}, cors=True
            )
            return

        if _state.on_import is not None:
            try:
                _state.on_import(profile)
            except Exception:  # ruff: ignore[blind-except] - never fail the HTTP response
                logger.exception("progress-import: on_import callback failed")

        imported_at = profile.imported_at or datetime.now(UTC).isoformat()
        self._send_json(
            200,
            {
                "ok": True,
                "quests": len(profile.completed_quests),
                "workshop": len(profile.workshop_levels),
                "projects": len(profile.project_items),
                "blueprints": len(profile.owned_blueprints),
                "imported_at": imported_at,
            },
            cors=True,
        )


def start_import_server(
    *,
    on_import: OnImport | None = None,
    output_path: Path | None = None,
    port: int = IMPORT_PORT,
) -> ThreadingHTTPServer | None:
    """Start the loopback import server in a daemon thread. None if bind fails."""
    if _state.server is not None:
        return _state.server

    _state.on_import = on_import
    _state.output_path = output_path

    try:
        server = ThreadingHTTPServer((IMPORT_HOST, port), _ImportHandler)
    except OSError as exc:
        logger.warning(
            "Progress import server could not bind %s:%s (%s) — "
            "bookmarklet import disabled",
            IMPORT_HOST,
            port,
            exc,
        )
        return None

    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever,
        name="arlo-progress-import",
        daemon=True,
    )
    thread.start()
    _state.server = server
    _state.thread = thread
    logger.info(
        "Progress import server listening on http://%s:%s/ "
        "(POST %s from MetaForge bookmarklet)",
        IMPORT_HOST,
        port,
        IMPORT_PATH,
    )
    return server


def stop_import_server() -> None:
    """Shut down the import server if running."""
    server = _state.server
    _state.server = None
    _state.thread = None
    _state.on_import = None
    _state.output_path = None
    if server is None:
        return
    try:
        server.shutdown()
    except OSError:
        logger.debug("progress-import: shutdown error", exc_info=True)
    try:
        server.server_close()
    except OSError:
        logger.debug("progress-import: server_close error", exc_info=True)
