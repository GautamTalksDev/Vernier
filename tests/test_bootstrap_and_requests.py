"""Bootstrap sitecustomize path and requests adapter capture."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

from vernier.capture.bootstrap import install, reset_for_tests
from vernier.capture.recorder import activate_store, deactivate, get_store
from vernier.capture.transport import install_requests_hooks
from vernier.record.store import RecordStore


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(autouse=True)
def _reset_bootstrap() -> None:
    reset_for_tests()
    yield
    reset_for_tests()


def test_bootstrap_install_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setenv("VERNIER_CAPTURE", "1")
    monkeypatch.setenv("VERNIER_RUN_DIR", str(run_dir))
    monkeypatch.setenv("VERNIER_RUN_ID", "boot-1")
    install()
    store = get_store()
    assert store is not None
    assert any(e["kind"] == "RunStart" for e in store.read_events())
    deactivate()


def test_requests_adapter_records(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        store = RecordStore(tmp_path / "req", "req-1")
        activate_store(store)
        install_requests_hooks()
        session = requests.Session()
        response = session.get(
            f"http://{host}:{port}/",
            headers={"Authorization": "Bearer NOPE"},
            timeout=5,
        )
        assert response.status_code == 200
        kinds = [e["kind"] for e in store.read_events()]
        assert "LlmRequest" in kinds
        assert "LlmResponse" in kinds
        for path in (tmp_path / "req").rglob("*"):
            if path.is_file():
                assert b"authorization" not in path.read_bytes().lower()
        deactivate()
    finally:
        server.shutdown()
        thread.join(timeout=5)
