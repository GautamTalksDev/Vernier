"""Failing-then-fixed tests for untrusted-input hardening (pass 2)."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest

from vernier.align import align
from vernier.align.costs import AlignCosts, load_costs
from vernier.align.types import AlignEvent, AlignKind
from vernier.canonical import digest
from vernier.canonical._normalize import normalize_tree
from vernier.capture.redaction import redact_headers
from vernier.classify import ClassificationTooLongError, classify_pair
from vernier.classify.types import TrajectoryEvent
from vernier.record.index import resolve_run_dir, upsert_run
from vernier.record.store import (
    RecordStore,
    encode_event_line,
    load_run,
)
from vernier.report.escape import escape_untrusted

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 2. Terminal injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "x\x1b[31mRED\x1b[0m",
        "title\x1b]8;;https://evil.example/x\x07link",
        "before\u202e\u202doverride",
        "line1\rOVERWRITE",
        "x" * (10 * 1024 * 1024),
    ],
    ids=["ansi", "osc8", "bidi", "cr", "10mib"],
)
def test_escape_untrusted_blocks_terminal_injection(payload: str) -> None:
    out = escape_untrusted(payload)
    assert "\x1b" not in out
    assert "\x07" not in out
    assert "\r" not in out
    assert "\u202e" not in out
    assert "\u202d" not in out
    # Must not hang or emit unbounded control-laden text.
    assert len(out) <= 256 * 1024
    assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", out)


def test_render_human_and_gate_paths_use_escape() -> None:
    from vernier.classify.types import FiredRule, Severity, Verdict
    from vernier.report import render_human
    from vernier.report.gate_report import render_gate_comment

    dirty = "ok\x1b[31m\u202e\r"
    verdict = Verdict(
        spec_version="divergence-v1",
        severity=Severity.HIGH,
        rule_ids=("D04",),
        fired=(FiredRule("D04", Severity.HIGH, dirty),),
        applied_ignores=(),
        harness_fault=dirty,
    )
    human = render_human(
        source_run_id=dirty,
        candidate_run_id=dirty,
        verdict=verdict,
        pairs=[],
        traj_a=[],
        traj_b=[],
        color=False,
    )
    assert "\x1b" not in human and "\r" not in human and "\u202e" not in human
    comment = render_gate_comment(
        {
            "worst_severity": "HIGH",
            "rule_ids": ["D04"],
            "n_runs": 1,
            "baseline_model": dirty,
            "candidate_model": dirty,
            "runs": [{"label": dirty, "severity": "HIGH", "rule_ids": ["D04"]}],
        }
    )
    assert "\x1b" not in comment and "\u202e" not in comment


def test_format_harness_fault_none_is_explicit_not_bypass() -> None:
    """None must not reach escape_untrusted; present text must still be escaped."""
    from vernier.cli.main import _format_harness_fault_for_display

    assert _format_harness_fault_for_display(None) == "<none>"
    dirty = "x\x1b[31mRED\x1b[0m\u202e\r"
    out = _format_harness_fault_for_display(dirty)
    assert out != "<none>"
    assert "\x1b" not in out
    assert "\u202e" not in out
    assert "\r" not in out
    assert "RED" in out


# ---------------------------------------------------------------------------
# 3. Record store path traversal
# ---------------------------------------------------------------------------


def test_run_id_traversal_cannot_escape_runs_dir(tmp_path: Path) -> None:
    runs = tmp_path / ".vernier" / "runs"
    runs.mkdir(parents=True)
    index = tmp_path / ".vernier" / "index.sqlite"
    outside = tmp_path / "etc"
    outside.mkdir()
    (outside / "passwd").write_text("root", encoding="utf-8")

    with pytest.raises((ValueError, FileNotFoundError, OSError)):
        resolve_run_dir("../../etc", runs_dir=runs, index_path=index)

    # Poisoned index path must still be rejected when resolving under runs_dir.
    upsert_run(
        index,
        run_id="evil",
        path=str(outside),
        created_at="2020-01-01T00:00:00Z",
        event_count=0,
        root_digest="sha256:" + "ab" * 32,
    )
    with pytest.raises(ValueError, match="escape|under|confine|path"):
        resolve_run_dir("evil", runs_dir=runs, index_path=index)


def test_blob_digest_traversal_rejected(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / "run", "run1")
    victim = tmp_path / "secret.txt"
    victim.write_bytes(b"top-secret")
    for bad in ("../secret.txt", "..\\secret.txt", "ab/cd", "ab\\cd", "abc\x00def"):
        with pytest.raises(ValueError):
            store.read_blob(f"sha256:{bad}")


def test_blob_path_windows_semantics_rejected() -> None:
    from vernier.record.store import validate_blob_digest_hex

    for bad in (
        "..\\..\\Windows\\System32",
        "deadbeef/../escape",
        "x" * 64 + "/y",
        "C:\\windows",
    ):
        with pytest.raises(ValueError):
            validate_blob_digest_hex(bad)
    # PureWindowsPath must not treat a hex digest as leaving blobs/.
    digest_hex = "a" * 64
    base = PureWindowsPath(r"C:\proj\.vernier\runs\r\blobs")
    joined = base / digest_hex
    assert ".." not in joined.parts
    validate_blob_digest_hex(digest_hex)


# ---------------------------------------------------------------------------
# 4. Decompression / resource bombs
# ---------------------------------------------------------------------------


def test_gzip_bomb_rejected(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / "run", "bomb")
    # Tiny gzip that expands past the decompressed-size cap.
    huge = b"A" * (16 * 1024 * 1024)
    compressed = gzip.compress(huge, mtime=0)
    digest_hex = hashlib.sha256(huge).hexdigest()
    gz_path = store.blobs_dir / f"{digest_hex}.gz"
    gz_path.write_bytes(compressed)
    with pytest.raises(ValueError, match="decompressed|too large|bomb|limit"):
        store.read_blob(f"sha256:{digest_hex}")


def test_events_jsonl_line_and_count_caps(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    (root / "blobs").mkdir()
    # Oversized line.
    long_body = (
        b'{"run_id":"r","seq":0,"kind":"RunStart","x":"'
        + (b"Z" * (2 * 1024 * 1024))
        + b'"}'
    )
    (root / "events.jsonl").write_bytes(encode_event_line(long_body))
    with pytest.raises(ValueError, match="line|length|too large"):
        load_run(root)

    # Too many events (bypass attempt via tiny envelopes).
    root2 = tmp_path / "run2"
    root2.mkdir()
    (root2 / "blobs").mkdir()
    lines = []
    for i in range(5001):
        body = (
            f'{{"run_id":"r","seq":{i},"logical_parent_seq":null,'
            f'"kind":"RunStart","payload_digest":"sha256:{"ab" * 32}",'
            f'"blob_ref":"sha256:{"cd" * 32}"}}'
        ).encode()
        lines.append(encode_event_line(body))
    (root2 / "events.jsonl").write_bytes(b"".join(lines))
    with pytest.raises(ValueError, match="event.count|too many|max_events|5000"):
        load_run(root2)


def test_max_events_not_bypassed_by_crafted_manifest_or_costs(tmp_path: Path) -> None:
    # classify_pair uses trajectory length, not manifest.event_count.
    big = [
        TrajectoryEvent(kind="ToolCall", tool_name="t", arguments={})
        for _ in range(5001)
    ]
    with pytest.raises(ClassificationTooLongError):
        classify_pair(big, big)

    # Alignment costs with an inflated max_events must not raise the ceiling.
    packaged = load_costs()
    costs = AlignCosts(
        version="evil",
        max_events=10_000_000,
        gap=packaged.gap,
        match=packaged.match,
        mismatch_default=packaged.mismatch_default,
        mismatch_pairs=packaged.mismatch_pairs,
    )
    left = [AlignEvent(AlignKind.LLM_TURN)] * 5001
    right = [AlignEvent(AlignKind.LLM_TURN)] * 5001
    with pytest.raises(Exception, match="5000|max_events|too long"):
        align(left, right, costs=costs)

    # Packaged costs stay at 5000; vernier.toml must not raise them.
    assert load_costs().max_events == 5000


# ---------------------------------------------------------------------------
# 5. JSON nesting depth
# ---------------------------------------------------------------------------


def test_deeply_nested_json_errors_not_stackoverflow() -> None:
    depth = 10_000
    nested: object = {"v": 1}
    for _ in range(depth):
        nested = {"a": nested}
    with pytest.raises((ValueError, RecursionError), match="depth|nest|complex"):
        normalize_tree(nested)
    # Canonical digest path must also refuse.
    with pytest.raises((ValueError, RecursionError), match="depth|nest|complex"):
        digest(nested)


# ---------------------------------------------------------------------------
# 6. sitecustomize / PYTHONPATH hijack
# ---------------------------------------------------------------------------


def test_record_sitecustomize_not_hijacked_by_cwd(tmp_path: Path) -> None:
    """Malicious cwd sitecustomize.py must not win over Vernier's shim."""
    from vernier.replay.redrive import write_shim

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    shim = write_shim(run_dir)
    marker = tmp_path / "marker.txt"
    # Attacker file in cwd.
    (tmp_path / "sitecustomize.py").write_text(
        f"open(r'{marker}', 'w').write('pwned')\n",
        encoding="utf-8",
    )
    # Probe script: if our shim ran, vernier bootstrap import works; marker absent.
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\n"
        "print('ok')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    # Simulate vern record env construction via the shared helper.
    from vernier.replay.redrive import capture_python_env

    env.update(capture_python_env(shim_dir=shim, run_dir=run_dir, run_id="t"))
    env["VERNIER_CAPTURE"] = "0"  # don't require full capture activate
    # Remove capture flag so bootstrap may no-op, but sitecustomize still loads.
    env.pop("VERNIER_CAPTURE", None)
    completed = subprocess.run(
        [sys.executable, "-P", str(probe)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert not marker.exists(), "cwd sitecustomize.py hijacked the interpreter"


def test_capture_python_env_forces_safepath_and_shim_first(tmp_path: Path) -> None:
    from vernier.replay.redrive import capture_python_env, write_shim

    shim = write_shim(tmp_path / "run")
    env = capture_python_env(
        shim_dir=shim,
        run_dir=tmp_path / "run",
        run_id="abc",
        base_environ={"PYTHONPATH": str(tmp_path / "evil"), "PYTHONSAFEPATH": ""},
    )
    assert env["PYTHONSAFEPATH"] == "1"
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(shim)


# ---------------------------------------------------------------------------
# 7. Secret redaction completeness
# ---------------------------------------------------------------------------

CANARY = "CANARY_SECRET_9f3c2e1a_DO_NOT_LEAK"


def test_redact_headers_extended_names() -> None:
    headers = {
        "Authorization": f"Bearer {CANARY}",
        "x-api-key": CANARY,
        "openai-organization": CANARY,
        "anthropic-version": "2023-06-01",
        "proxy-authorization": f"Basic {CANARY}",
        "set-cookie": f"session={CANARY}",
        "X-Auth-Token": CANARY,
        "X-My-Secret": CANARY,
        "X-Access-Key": CANARY,
        "Content-Type": "application/json",
    }
    out = redact_headers(headers)
    assert "Content-Type" in out
    for k, v in out.items():
        assert CANARY not in v
        assert not re.search(r"token|secret|key|auth", k, re.I)


def test_redact_url_and_body_secrets() -> None:
    from vernier.capture.redaction import redact_json_value, redact_url

    url = f"https://api.example/v1?api_key={CANARY}&q=1"
    assert CANARY not in redact_url(url)
    assert "api_key=" in redact_url(url)

    body = {
        "api_key": CANARY,
        "token": CANARY,
        "secret": CANARY,
        "password": CANARY,
        "nested": {"key": CANARY, "ok": "keep"},
        "list": [{"token": CANARY}],
    }
    red = redact_json_value(body)
    blob = json.dumps(red)
    assert CANARY not in blob
    assert red["nested"]["ok"] == "keep"


def test_recorded_corpus_run_has_no_canary(tmp_path: Path) -> None:
    from vernier.capture.recorder import (
        activate_store,
        deactivate,
        record_http_request,
        record_http_response,
    )

    store = RecordStore(tmp_path / "run", "canary-run")
    activate_store(store)
    try:
        body = json.dumps(
            {"password": CANARY, "messages": [{"role": "user", "content": "hi"}]}
        ).encode()
        record_http_request(
            store,
            seq=store.next_seq(),
            method="POST",
            url=f"https://api.example/v1/chat?api_key={CANARY}",
            headers={
                "Authorization": f"Bearer {CANARY}",
                "x-api-key": CANARY,
                "openai-organization": CANARY,
                "Content-Type": "application/json",
            },
            body=body,
        )
        record_http_response(
            store,
            request_seq=0,
            status=200,
            headers={"set-cookie": f"s={CANARY}", "Content-Type": "application/json"},
            body=b'{"ok":true}',
            chunks=None,
        )
        store.finalize()
    finally:
        deactivate()

    for path in (tmp_path / "run").rglob("*"):
        if path.is_file() and path.name != "sitecustomize.py":
            data = path.read_bytes()
            assert CANARY.encode() not in data, f"canary found in {path}"


# ---------------------------------------------------------------------------
# 8. vernier.toml sandbox
# ---------------------------------------------------------------------------


def test_vernier_toml_cannot_set_env_or_escape_store(tmp_path: Path) -> None:
    from vernier.config import VernierConfigError, load_vernier_toml

    evil = tmp_path / "vernier.toml"
    evil.write_text(
        """
[vernier]
corpus_path = "../../etc"
runs_dir = "/tmp/evil-store"
max_severity = "MEDIUM"

[vernier.env]
AWS_SECRET_ACCESS_KEY = "pwned"
PATH = "/evil/bin"

[vernier.exec]
cmd = "rm -rf /"
""",
        encoding="utf-8",
    )
    with pytest.raises(VernierConfigError):
        load_vernier_toml(evil, project_root=tmp_path)

    good = tmp_path / "good.toml"
    good.write_text(
        """
[vernier]
corpus_path = "vernier-corpus"
max_severity = "HIGH"

[divergence]
version = "divergence-v1"
[divergence.ignore]
pointers = ["/id"]
""",
        encoding="utf-8",
    )
    cfg = load_vernier_toml(good, project_root=tmp_path)
    assert cfg.corpus_path == (tmp_path / "vernier-corpus").resolve()
    assert cfg.max_severity == "HIGH"
    assert not hasattr(cfg, "env") or not getattr(cfg, "env", None)


def test_vernier_toml_tomllib_does_not_execute(tmp_path: Path) -> None:
    """TOML has no code execution; loader must use tomllib only."""
    import vernier.config as cfg_mod

    src = Path(cfg_mod.__file__).read_text(encoding="utf-8")
    assert "tomllib" in src
    assert "eval(" not in src
    assert "exec(" not in src
    assert "os.system" not in src
