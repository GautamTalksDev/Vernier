"""Shared re-drive helpers for verify / diff / gate."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from vernier.align import Relation, align
from vernier.align.types import AlignedPair
from vernier.classify import classify_pair
from vernier.classify.types import TrajectoryEvent, Verdict
from vernier.record.store import RecordStore, load_run
from vernier.replay.harness_guard import apply_harness_guard
from vernier.replay.project import project_align_events, project_run


@dataclass(frozen=True, slots=True)
class RedriveResult:
    source_run_id: str
    candidate_run_id: str
    source_dir: Path
    candidate_dir: Path
    traj_a: list[TrajectoryEvent]
    traj_b: list[TrajectoryEvent]
    pairs: tuple[AlignedPair, ...]
    verdict: Verdict
    agent_exit: int
    agent_output: str


def resolve_command(source_dir: Path) -> tuple[list[str], str]:
    frozen = load_run(source_dir)
    command = list(frozen.manifest.command)
    cwd = frozen.manifest.cwd or str(Path.cwd())
    if not command:
        reader = RecordStore(source_dir, frozen.run_id)
        for env_row in reader.read_events():
            if env_row["kind"] == "RunStart":
                payload = reader.read_payload(env_row)
                command = [str(x) for x in (payload.get("command") or [])]
                cwd = str(payload.get("cwd") or cwd)
                break
    if not command:
        raise ValueError("recorded run has no command; cannot re-drive")
    if command[0] in {"python", "python3"}:
        command = [sys.executable, *command[1:]]
    return command, cwd


def write_shim(run_dir: Path) -> Path:
    shim_dir = run_dir / "_shim"
    shim_dir.mkdir(parents=True, exist_ok=True)
    (shim_dir / "sitecustomize.py").write_text(
        "import vernier.capture.bootstrap as _vernier_bootstrap\n"
        "_vernier_bootstrap.install()\n",
        encoding="utf-8",
        newline="\n",
    )
    return shim_dir


def index_beside(runs_dir: Path) -> Path:
    if runs_dir.name == "runs":
        return runs_dir.parent / "index.sqlite"
    return runs_dir / "index.sqlite"


def _llm_base_from_run(source_dir: Path) -> str | None:
    """Origin used during capture so full-stub URL keys match the record."""
    from urllib.parse import urlsplit

    frozen = load_run(source_dir)
    reader = RecordStore(source_dir, frozen.run_id)
    for env_row in reader.read_events():
        if env_row["kind"] != "LlmRequest":
            continue
        payload = reader.read_payload(env_row)
        url = str(payload.get("url") or "")
        for suffix in ("/v1/chat/completions", "/chat/completions"):
            if url.endswith(suffix):
                return url[: -len(suffix)]
        parts = urlsplit(url)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    return None


def redrive(
    source_dir: Path,
    *,
    out_dir: Path,
    mode: str,
    model: str | None = None,
    prompt_file: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> RedriveResult:
    """Re-drive ``source_dir`` into a new run under ``out_dir``.

    ``mode`` is ``full_stub`` (verify) or ``live_diff`` (diff).
    """
    command, cwd = resolve_command(source_dir)
    candidate_id = uuid.uuid4().hex
    candidate_dir = out_dir / candidate_id
    candidate_dir.mkdir(parents=True, exist_ok=True)
    shim_dir = write_shim(candidate_dir)
    index_path = index_beside(out_dir)

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(shim_dir) if not existing else f"{shim_dir}{os.pathsep}{existing}"
    )
    env["VERNIER_REPLAY_SOURCE"] = str(source_dir.resolve())
    env["VERNIER_RUN_DIR"] = str(candidate_dir.resolve())
    env["VERNIER_RUN_ID"] = candidate_id
    env["VERNIER_INDEX"] = str(index_path.resolve())
    env["VERNIER_COMMAND_JSON"] = json.dumps(command)
    env["VERNIER_CWD"] = cwd
    env.pop("VERNIER_CAPTURE", None)
    env.setdefault("PYTHONSAFEPATH", "")
    env.pop("PYTHONNOUSERSITE", None)

    if mode == "live_diff":
        env["VERNIER_DIFF"] = "1"
        env.pop("VERNIER_REPLAY", None)
        env.pop("VERNIER_REPLAY_LLM_BASE", None)
        if model:
            env["VERNIER_OVERRIDE_MODEL"] = model
        if prompt_file is not None:
            env["VERNIER_OVERRIDE_PROMPT_FILE"] = str(prompt_file.resolve())
    elif mode == "full_stub":
        env["VERNIER_REPLAY"] = "1"
        env.pop("VERNIER_DIFF", None)
        env.pop("VERNIER_OVERRIDE_MODEL", None)
        env.pop("VERNIER_OVERRIDE_PROMPT_FILE", None)
        # Point the agent at the same origin recorded during capture so HTTP
        # stub keys (method+url+body) match. Transport never dials in replay.
        replay_base = _llm_base_from_run(source_dir)
        if replay_base:
            env["VERNIER_REPLAY_LLM_BASE"] = replay_base
        else:
            env.pop("VERNIER_REPLAY_LLM_BASE", None)
    else:
        raise ValueError(f"unknown redrive mode: {mode!r}")

    if extra_env:
        env.update(extra_env)

    completed = subprocess.run(
        command,
        env=env,
        cwd=cwd if Path(cwd).is_dir() else None,
        check=False,
        capture_output=True,
        text=True,
    )
    output = (completed.stderr or "") + (completed.stdout or "")

    traj_a = project_run(source_dir)
    traj_b = project_run(candidate_dir)
    pairs = tuple(
        align(project_align_events(source_dir), project_align_events(candidate_dir))
    )
    classified = classify_pair(traj_a, traj_b)
    frozen_src = load_run(source_dir)
    provisional = RedriveResult(
        source_run_id=frozen_src.run_id,
        candidate_run_id=candidate_id,
        source_dir=source_dir,
        candidate_dir=candidate_dir,
        traj_a=traj_a,
        traj_b=traj_b,
        pairs=pairs,
        verdict=classified,
        agent_exit=completed.returncode,
        agent_output=output,
    )
    guarded = apply_harness_guard(provisional)
    if guarded is classified:
        return provisional
    return RedriveResult(
        source_run_id=provisional.source_run_id,
        candidate_run_id=provisional.candidate_run_id,
        source_dir=provisional.source_dir,
        candidate_dir=provisional.candidate_dir,
        traj_a=provisional.traj_a,
        traj_b=provisional.traj_b,
        pairs=provisional.pairs,
        verdict=guarded,
        agent_exit=provisional.agent_exit,
        agent_output=provisional.agent_output,
    )


def all_matched(pairs: tuple[AlignedPair, ...] | list[AlignedPair]) -> bool:
    if not pairs:
        return True
    return all(p.relation is Relation.MATCHED for p in pairs)
