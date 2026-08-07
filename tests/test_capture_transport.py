"""Transport capture: streaming, parallel seq, oversize, redaction."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from vernier.canonical._normalize import MAX_VALUE_BYTES
from vernier.capture.bodies import body_digest
from vernier.capture.recorder import activate_store, deactivate, get_store
from vernier.capture.transport import install_httpx_hooks
from vernier.record.store import RecordStore


class _FakeLLMServer(BaseHTTPRequestHandler):
    hang_release = threading.Event()
    seen_hang: dict[int, bool] = {}
    seen_lock = threading.Lock()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/hang/"):
            idx = int(self.path.rsplit("/", 1)[-1])
            with self.seen_lock:
                self.seen_hang[idx] = True
            self.hang_release.wait(timeout=10)
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length)
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in (
                b"data: one\n\n",
                b"data: two\n\n",
                b"data: [DONE]\n\n",
            ):
                self.wfile.write(chunk)
                self.wfile.flush()
            return
        if self.path == "/json":
            body = b'{"msg":"hello"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/malformed":
            body = b"{not-json"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/oversize":
            body = b"x" * (MAX_VALUE_BYTES + 1024)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture()
def fake_server() -> Any:
    _FakeLLMServer.hang_release = threading.Event()
    _FakeLLMServer.seen_hang = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeLLMServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://{host}:{port}"
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture()
def record_dir(tmp_path: Path) -> Path:
    return tmp_path / "run"


def _activate(record_dir: Path, run_id: str = "test-run") -> RecordStore:
    store = RecordStore(record_dir, run_id)
    activate_store(store)
    install_httpx_hooks()
    return store


def _load_event_payloads(store: RecordStore) -> list[dict[str, Any]]:
    return [store.read_payload(event) for event in store.read_events()]


def test_streaming_chunks_and_reassembled_digest(
    fake_server: str, record_dir: Path
) -> None:
    store = _activate(record_dir)
    expected_chunks = [
        b"data: one\n\n",
        b"data: two\n\n",
        b"data: [DONE]\n\n",
    ]
    expected_body = b"".join(expected_chunks)
    expected_digest = body_digest(expected_body)

    with (
        httpx.Client() as client,
        client.stream("POST", f"{fake_server}/stream") as response,
    ):
        got = b"".join(response.iter_bytes())
    assert got == expected_body
    assert body_digest(got) == expected_digest

    payloads = _load_event_payloads(store)
    responses = [
        p for p in payloads if p.get("phase") == "response" and p.get("stream")
    ]
    assert len(responses) == 1
    resp = responses[0]
    assert resp["body_digest"] == expected_digest
    assert [c["index"] for c in resp["chunks"]] == [0, 1, 2]
    deactivate()


def test_parallel_seq_deterministic_across_20_runs(
    fake_server: str, tmp_path: Path
) -> None:
    seq_patterns: list[list[int]] = []
    for trial in range(20):
        _FakeLLMServer.hang_release = threading.Event()
        _FakeLLMServer.seen_hang = {}
        store = _activate(tmp_path / f"run-{trial}", run_id=f"run-{trial}")
        gates = [threading.Event() for _ in range(4)]
        gates[0].set()
        errors: list[BaseException] = []

        def worker(
            i: int,
            gate_list: list[threading.Event] = gates,
            err_list: list[BaseException] = errors,
        ) -> None:
            try:
                gate_list[i].wait(timeout=5)
                with httpx.Client() as client:
                    client.get(f"{fake_server}/hang/{i}", timeout=10.0)
            except BaseException as exc:  # noqa: BLE001
                err_list.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for i in range(4):
            deadline = time.time() + 5
            while i not in _FakeLLMServer.seen_hang and time.time() < deadline:
                time.sleep(0.001)
            assert i in _FakeLLMServer.seen_hang
            if i + 1 < 4:
                gates[i + 1].set()
        _FakeLLMServer.hang_release.set()
        for t in threads:
            t.join(timeout=10)
        assert not errors

        req_seqs = [
            e["seq"] for e in store.read_events() if e["kind"] == "LlmRequest"
        ]
        seq_patterns.append(req_seqs)
        deactivate()

    assert all(p == seq_patterns[0] for p in seq_patterns)
    assert seq_patterns[0] == sorted(seq_patterns[0])
    assert len(seq_patterns[0]) == 4


def test_oversize_records_truncation_marker(
    fake_server: str, record_dir: Path
) -> None:
    store = _activate(record_dir)
    with httpx.Client() as client:
        response = client.post(f"{fake_server}/oversize")
        assert response.status_code == 200
        assert len(response.content) > MAX_VALUE_BYTES

    kinds = [e["kind"] for e in store.read_events()]
    assert "CaptureGap" in kinds
    payloads = _load_event_payloads(store)
    assert any(p.get("truncation") == "body_exceeds_8mib" for p in payloads)
    deactivate()


def test_malformed_json_does_not_crash(
    fake_server: str, record_dir: Path
) -> None:
    store = _activate(record_dir)
    with httpx.Client() as client:
        response = client.post(f"{fake_server}/malformed")
        assert response.content == b"{not-json"
    assert any(e["kind"] == "LlmResponse" for e in store.read_events())
    deactivate()


def test_authorization_never_in_record_files(
    fake_server: str, record_dir: Path
) -> None:
    _activate(record_dir)
    headers = {
        "Authorization": "Bearer SECRET",
        "X-Api-Key": "SECRET_KEY",
        "Cookie": "a=b",
        "Content-Type": "application/json",
    }
    with httpx.Client() as client:
        client.post(
            f"{fake_server}/json",
            headers=headers,
            content=b'{"q":1}',
        )
    deactivate()

    for path in record_dir.rglob("*"):
        if not path.is_file():
            continue
        data = path.read_bytes()
        lower = data.lower()
        assert b"authorization" not in lower
        assert b"bearer secret" not in lower
        assert b"secret_key" not in lower
        assert b"cookie" not in lower
        assert b"api-key" not in lower
        assert b"apikey" not in lower


def test_vern_record_simple_agent_zero_edit(tmp_path: Path) -> None:
    out = tmp_path / "recorded"
    repo = Path(__file__).resolve().parents[1]
    example = repo / "examples" / "simple_agent.py"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "vern",
            "record",
            "--out",
            str(out),
            "--",
            "python",
            str(example),
        ],
        cwd=str(repo),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    runs = [p for p in out.iterdir() if p.is_dir()]
    assert runs, "expected a run directory"
    run_dir = runs[0]
    events_path = run_dir / "events.jsonl"
    assert events_path.exists()
    text = events_path.read_text(encoding="utf-8")
    assert "LlmRequest" in text
    assert "LlmResponse" in text
    for path in run_dir.rglob("*"):
        if path.is_file() and path.name != "sitecustomize.py":
            blob = path.read_bytes()
            assert b"Authorization" not in blob
            assert b"SUPER_SECRET_TOKEN" not in blob


def test_tool_decorator_records_local_call(record_dir: Path) -> None:
    from vern import tool

    store = _activate(record_dir)

    @tool
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5
    kinds = [e["kind"] for e in store.read_events()]
    assert "ToolCall" in kinds
    assert "ToolResult" in kinds
    deactivate()


def test_wrap_tools_registry(record_dir: Path) -> None:
    from vern import wrap_tools

    _activate(record_dir)

    def ping() -> str:
        return "pong"

    wrapped = wrap_tools({"ping": ping})
    assert wrapped["ping"]() == "pong"
    deactivate()


def test_shim_failure_emits_gap_without_raising(record_dir: Path) -> None:
    from vernier.capture import recorder
    from vernier.capture.recorder import emit_capture_gap

    store = _activate(record_dir)
    emit_capture_gap(store, reason="test_gap")
    assert any(e["kind"] == "CaptureGap" for e in store.read_events())
    deactivate()
    assert get_store() is None
    assert recorder.get_store() is None
