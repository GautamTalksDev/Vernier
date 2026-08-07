"""KT-3 full-stub re-drive (``vern verify``)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from vernier.canonical._jcs import serialize_jcs
from vernier.record.store import load_run, verify_run
from vernier.replay.catalog import load_catalog
from vernier.replay.miss import ReplayMiss
from vernier.replay.project import project_run

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "testdata" / "verify"
AGENT = REPO / "examples" / "verify_fixture_agent.py"


def _fixture_dirs() -> list[Path]:
    dirs = sorted(p for p in FIXTURES.iterdir() if p.is_dir() and (p / "run").is_dir())
    assert len(dirs) == 10, f"expected 10 verify fixtures, found {len(dirs)}"
    return dirs


def _prepare_run(case: Path, dest_runs: Path) -> str:
    frozen = load_run(case / "run")
    run_id = frozen.run_id
    target = dest_runs / run_id
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(case / "run", target)
    meta = json.loads((case / "meta.json").read_text(encoding="utf-8"))
    scenario = meta["scenario"]
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["command"] = [
        "python",
        str(AGENT.relative_to(REPO)),
        "--scenario",
        scenario,
    ]
    manifest["cwd"] = str(REPO)
    manifest_path.write_bytes(serialize_jcs(manifest) + b"\n")
    return run_id


@pytest.mark.parametrize("case", _fixture_dirs(), ids=lambda p: p.name)
def test_verify_fixture_severity_none(case: Path, tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    run_id = _prepare_run(case, runs)
    meta = json.loads((case / "meta.json").read_text(encoding="utf-8"))

    # Trajectory floor: >=8 projected steps.
    traj = project_run(runs / run_id)
    assert len(traj) >= 8, f"{case.name} projected only {len(traj)} steps"

    integrity = verify_run(runs / run_id)
    assert integrity.ok, integrity.errors

    catalog = load_catalog(runs / run_id)
    assert catalog.source_run_id == run_id

    env = os.environ.copy()
    if case.name == "05_nondet":
        env["AGENT_FIXTURE_TOKEN"] = "fixture-token"

    proc = subprocess.run(
        [
            "uv",
            "run",
            "vern",
            "verify",
            run_id,
            "--runs-dir",
            str(runs),
            "--out",
            str(tmp_path / "verify-runs"),
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "severity=NONE" in proc.stdout
    assert meta["scenario"] == case.name


def test_hand_edited_record_fails_verify(tmp_path: Path) -> None:
    case = FIXTURES / "01_tools_basic"
    runs = tmp_path / "runs"
    runs.mkdir()
    run_id = _prepare_run(case, runs)
    events = runs / run_id / "events.jsonl"
    raw = events.read_bytes()
    # Flip one byte inside a CRC-prefixed line body to break integrity.
    corrupted = bytearray(raw)
    # Find first payload byte after CRC prefix on first non-empty line.
    nl = corrupted.find(b"\n")
    assert nl > 10
    corrupted[10] ^= 0x01
    events.write_bytes(bytes(corrupted))

    proc = subprocess.run(
        [
            "uv",
            "run",
            "vern",
            "verify",
            run_id,
            "--runs-dir",
            str(runs),
            "--out",
            str(tmp_path / "verify-runs"),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "integrity failed" in proc.stderr.lower() or "FAIL" in proc.stderr


def test_replay_miss_never_falls_through(tmp_path: Path) -> None:
    from vernier.capture.bootstrap import reset_for_tests
    from vernier.capture.recorder import activate_store, deactivate
    from vernier.capture.tools import tool
    from vernier.record.store import RecordStore
    from vernier.replay import session as replay_session
    from vernier.replay.catalog import ReplayCatalog

    reset_for_tests()
    store = RecordStore(tmp_path / "run", "r1")
    activate_store(store)
    catalog = ReplayCatalog(
        source_run_id="src",
        source_root=tmp_path,
        command=(),
        cwd="",
    )
    replay_session._catalog = catalog  # noqa: SLF001

    @tool
    def ghost(x: int) -> int:
        return x

    with pytest.raises(ReplayMiss) as exc:
        ghost(1)
    assert exc.value.channel == "tool"
    deactivate()
    reset_for_tests()


def test_fixtures_include_failures_and_stream() -> None:
    names = {p.name for p in _fixture_dirs()}
    assert "03_fail_http" in names
    assert "04_fail_tool" in names
    assert "02_stream" in names
    # Confirm stream metadata exists in at least one fixture.
    frozen = load_run(FIXTURES / "02_stream" / "run")
    from vernier.record.store import RecordStore

    reader = RecordStore(frozen.root, frozen.run_id)
    streamed = False
    for env in reader.read_events():
        if env["kind"] == "LlmResponse":
            payload = reader.read_payload(env)
            if payload.get("stream"):
                streamed = True
                break
    assert streamed
