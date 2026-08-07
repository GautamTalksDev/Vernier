"""Shared local baseline LLM server for corpus agents.

Designated baseline model id: ``vernier-baseline-local-v1``.
Deterministic responses so ``vern verify`` (KT-3) holds without paid APIs.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

BASELINE_MODEL = "vernier-baseline-local-v1"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            req: dict[str, Any] = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            req = {}
        messages = req.get("messages") if isinstance(req.get("messages"), list) else []
        last = ""
        if messages:
            last = str(messages[-1].get("content", ""))
        # Deterministic, content-derived reply (stable across re-drives).
        reply = f"[{BASELINE_MODEL}] plan:{_fingerprint(last)}"
        body = json.dumps(
            {
                "id": "corpus-chat",
                "model": req.get("model") or BASELINE_MODEL,
                "choices": [{"message": {"role": "assistant", "content": reply}}],
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _fingerprint(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) > 80:
        cleaned = cleaned[:80]
    return cleaned or "empty"


def start_baseline_server() -> tuple[ThreadingHTTPServer | None, str]:
    """Start local baseline LLM, or return a replay placeholder URL."""
    if os.environ.get("VERNIER_REPLAY") == "1" or os.environ.get("VERNIER_DIFF") == "1":
        return None, "http://replay.invalid"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://{host}:{port}"


def chat(client: Any, base: str, content: str, *, model: str = BASELINE_MODEL) -> str:
    response = client.post(
        f"{base}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": content}],
        },
    )
    response.raise_for_status()
    data = response.json()
    return str(data["choices"][0]["message"]["content"])
