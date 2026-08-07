#!/usr/bin/env python3
"""Minimal HTTP agent for ``vern record`` demos.

No Vernier imports. Speaks to an in-process fake LLM/HTTP server so the
example is self-contained.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length)
        if self.path == "/v1/chat/completions":
            body = json.dumps(
                {"id": "chat-1", "choices": [{"message": {"content": "ok"}}]}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/v1/chat/completions/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [
                b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n',
                b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
                b"data: [DONE]\n\n",
            ]
            for chunk in chunks:
                self.wfile.write(chunk)
                self.wfile.flush()
            return
        self.send_response(404)
        self.end_headers()


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://{host}:{port}"
    headers = {
        "Authorization": "Bearer SUPER_SECRET_TOKEN",
        "Content-Type": "application/json",
        "X-Api-Key": "also-secret",
        "Cookie": "session=nope",
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            non_stream = client.post(
                f"{base}/v1/chat/completions",
                headers=headers,
                json={
                    "model": "demo",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            non_stream.raise_for_status()

            reassembled = bytearray()
            with client.stream(
                "POST",
                f"{base}/v1/chat/completions/stream",
                headers=headers,
                json={
                    "model": "demo",
                    "stream": True,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            ) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    reassembled.extend(chunk)
            assert reassembled, "expected streamed body"
    finally:
        server.shutdown()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
