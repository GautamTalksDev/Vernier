#!/usr/bin/env python3
"""Deterministic multi-step agent used to build ``testdata/verify`` fixtures.

Scenarios are selected with ``--scenario`` (or ``VERNIER_FIXTURE_SCENARIO``).
Each scenario performs >=8 capture steps (LLM and/or tool events).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from vern import tool


@tool
def lookup(query: str) -> dict[str, str]:
    return {"query": query, "answer": f"result:{query}"}


@tool
def score(value: int) -> int:
    return value * 2


@tool
def boom(msg: str) -> str:
    raise RuntimeError(msg)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            req = {}
        path = self.path
        if path.endswith("/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            content = str(req.get("n", "stream"))
            chunks = [
                f'data: {{"choices":[{{"delta":{{"content":"{content[:2]}"}}}}]}}\n\n',
                f'data: {{"choices":[{{"delta":{{"content":"{content[2:]}"}}}}]}}\n\n',
                "data: [DONE]\n\n",
            ]
            for chunk in chunks:
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
            return
        if path.endswith("/fail"):
            body = json.dumps({"error": "upstream"}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # Default chat completion.
        msg = "ok"
        if isinstance(req.get("messages"), list) and req["messages"]:
            msg = str(req["messages"][-1].get("content", "ok"))
        body = json.dumps(
            {"id": "chat", "choices": [{"message": {"content": msg}}]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wofile.write(body) if False else self.wfile.write(body)


def _base_url() -> tuple[ThreadingHTTPServer | None, str]:
    if os.environ.get("VERNIER_REPLAY") == "1":
        return None, "http://replay.invalid"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://{host}:{port}"


def _chat(client: httpx.Client, base: str, content: str) -> str:
    response = client.post(
        f"{base}/v1/chat/completions",
        json={
            "model": "demo",
            "messages": [{"role": "user", "content": content}],
        },
    )
    response.raise_for_status()
    data = response.json()
    return str(data["choices"][0]["message"]["content"])


def _stream(client: httpx.Client, base: str, token: str) -> bytes:
    reassembled = bytearray()
    with client.stream(
        "POST",
        f"{base}/v1/chat/completions/stream",
        json={"model": "demo", "stream": True, "n": token},
    ) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            reassembled.extend(chunk)
    return bytes(reassembled)


def run_scenario(name: str) -> int:
    server, base = _base_url()
    try:
        with httpx.Client(timeout=10.0) as client:
            if name == "01_tools_basic":
                _chat(client, base, "step1")
                lookup("alpha")
                _chat(client, base, "step2")
                score(3)
                _chat(client, base, "step3")
                lookup("beta")
                score(4)
                _chat(client, base, "done")
                return 0

            if name == "02_stream":
                _chat(client, base, "s1")
                _stream(client, base, "hello")
                lookup("stream")
                _chat(client, base, "s2")
                score(1)
                _stream(client, base, "world")
                lookup("tail")
                _chat(client, base, "done")
                return 0

            if name == "03_fail_http":
                _chat(client, base, "before")
                lookup("x")
                score(2)
                _chat(client, base, "mid")
                try:
                    client.post(
                        f"{base}/v1/chat/completions/fail",
                        json={"model": "demo", "messages": []},
                    ).raise_for_status()
                except httpx.HTTPStatusError:
                    lookup("after-fail")
                    score(9)
                    _chat(client, base, "recovered")
                    return 1
                return 2

            if name == "04_fail_tool":
                _chat(client, base, "t1")
                lookup("ok")
                score(5)
                _chat(client, base, "t2")
                try:
                    boom("nope")
                except RuntimeError:
                    lookup("after-boom")
                    score(6)
                    _chat(client, base, "t3")
                    return 1
                return 2

            if name == "05_nondet":
                os.environ.setdefault("VERNIER_CAPTURE_NONDET", "1")
                _ = time.time()
                _ = random.random()
                _ = os.getenv("AGENT_FIXTURE_TOKEN", "missing")
                _chat(client, base, "n1")
                lookup("n")
                score(7)
                _ = time.time()
                _chat(client, base, "n2")
                lookup("m")
                score(8)
                _chat(client, base, "done")
                return 0

            if name == "06_deep_tools":
                for i in range(4):
                    _chat(client, base, f"d{i}")
                    lookup(f"q{i}")
                    score(i)
                return 0

            if name == "07_mixed":
                _chat(client, base, "m1")
                _stream(client, base, "mix")
                lookup("a")
                score(1)
                lookup("b")
                _chat(client, base, "m2")
                score(2)
                _chat(client, base, "m3")
                return 0

            if name == "08_many_llm":
                for i in range(8):
                    _chat(client, base, f"llm-{i}")
                return 0

            if name == "09_tool_heavy":
                _chat(client, base, "start")
                for i in range(6):
                    lookup(f"item-{i}")
                    score(i + 1)
                _chat(client, base, "end")
                return 0

            if name == "10_stream_tools":
                _stream(client, base, "abc")
                lookup("s")
                _chat(client, base, "x")
                score(10)
                _stream(client, base, "def")
                lookup("t")
                score(11)
                _chat(client, base, "z")
                return 0

            raise SystemExit(f"unknown scenario: {name}")
    finally:
        if server is not None:
            server.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        default=os.environ.get("VERNIER_FIXTURE_SCENARIO", "01_tools_basic"),
    )
    args = parser.parse_args(argv)
    return run_scenario(args.scenario)


if __name__ == "__main__":
    raise SystemExit(main())
