"""CLI entrypoint for the ``vern`` console script."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Annotated

import typer

from vernier.align import Relation, align
from vernier.classify import classify_pair
from vernier.record.index import (
    DEFAULT_INDEX_PATH,
    list_runs,
    resolve_run_dir,
)
from vernier.record.store import DEFAULT_RUNS_DIR, load_run, verify_run
from vernier.replay.project import project_align_events, project_run

app = typer.Typer(
    name="vern",
    help="Vernier: deterministic capture, replay, and divergence classification.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Vernier: deterministic capture, replay, and divergence classification."""


def _index_beside(runs_dir: Path) -> Path:
    if runs_dir.name == "runs":
        return runs_dir.parent / "index.sqlite"
    return runs_dir / "index.sqlite"


def _write_shim(run_dir: Path) -> Path:
    shim_dir = run_dir / "_shim"
    shim_dir.mkdir(parents=True, exist_ok=True)
    sitecustomize = shim_dir / "sitecustomize.py"
    sitecustomize.write_text(
        "import vernier.capture.bootstrap as _vernier_bootstrap\n"
        "_vernier_bootstrap.install()\n",
        encoding="utf-8",
        newline="\n",
    )
    return shim_dir


def _resolve_command(command: list[str]) -> list[str]:
    resolved = list(command)
    if resolved and resolved[0] in {"python", "python3"}:
        resolved[0] = sys.executable
    return resolved


@app.command(
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
    },
)
def record(
    ctx: typer.Context,
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Runs directory (contains <run_id>/)."),
    ] = DEFAULT_RUNS_DIR,
) -> None:
    """Record LLM/HTTP traffic for a command without editing its source.

    Usage: ``vern record -- python examples/simple_agent.py``
    """
    command = list(ctx.args)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        typer.echo(
            "Usage: vern record -- <command> [args...]",
            err=True,
        )
        raise typer.Exit(code=2)

    run_id = uuid.uuid4().hex
    runs_dir = out
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    index_path = _index_beside(runs_dir)
    shim_dir = _write_shim(run_dir)

    resolved = _resolve_command(command)
    cwd = str(Path.cwd().resolve())

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(shim_dir) if not existing else f"{shim_dir}{os.pathsep}{existing}"
    )
    env["VERNIER_CAPTURE"] = "1"
    env["VERNIER_RUN_DIR"] = str(run_dir.resolve())
    env["VERNIER_RUN_ID"] = run_id
    env["VERNIER_INDEX"] = str(index_path.resolve())
    env["VERNIER_COMMAND_JSON"] = json.dumps(resolved)
    env["VERNIER_CWD"] = cwd
    env.setdefault("PYTHONSAFEPATH", "")
    env.pop("PYTHONNOUSERSITE", None)

    typer.echo(f"vern record: run_id={run_id}", err=True)
    typer.echo(f"vern record: out={run_dir}", err=True)

    completed = subprocess.run(resolved, env=env, check=False)

    if completed.returncode != 0:
        raise typer.Exit(code=completed.returncode)

    typer.echo(f"vern record: complete -> {run_dir / 'events.jsonl'}", err=True)


@app.command("verify-record")
def verify_record_cmd(
    run_id: Annotated[str, typer.Argument(help="Run id to verify.")],
    runs_dir: Annotated[
        Path,
        typer.Option("--runs-dir", help="Directory containing run folders."),
    ] = DEFAULT_RUNS_DIR,
    index: Annotated[
        Path,
        typer.Option("--index", help="SQLite run index path."),
    ] = DEFAULT_INDEX_PATH,
) -> None:
    """Recompute every CRC and the root digest; report tampering."""
    try:
        run_dir = resolve_run_dir(run_id, runs_dir=runs_dir, index_path=index)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    result = verify_run(run_dir)
    if result.ok:
        typer.echo(f"OK {result.run_id}")
        raise typer.Exit(code=0)

    typer.echo(f"FAIL {result.run_id}", err=True)
    for error in result.errors:
        typer.echo(f"  - {error}", err=True)
    raise typer.Exit(code=1)


@app.command()
def verify(
    run_id: Annotated[str, typer.Argument(help="Run id to re-drive.")],
    runs_dir: Annotated[
        Path,
        typer.Option("--runs-dir", help="Directory containing run folders."),
    ] = DEFAULT_RUNS_DIR,
    index: Annotated[
        Path,
        typer.Option("--index", help="SQLite run index path."),
    ] = DEFAULT_INDEX_PATH,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Directory for the re-drive run."),
    ] = None,
) -> None:
    """Full-stub re-drive: replay recorded stubs and assert identical trajectory.

    Exit codes: 0 identical, 1 divergence, 2 operational error.
    """
    try:
        source_dir = resolve_run_dir(run_id, runs_dir=runs_dir, index_path=index)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    integrity = verify_run(source_dir)
    if not integrity.ok:
        typer.echo(f"vern verify: record integrity failed for {run_id}", err=True)
        for error in integrity.errors:
            typer.echo(f"  - {error}", err=True)
        raise typer.Exit(code=1)

    frozen = load_run(source_dir)
    command = list(frozen.manifest.command)
    cwd = frozen.manifest.cwd or str(Path.cwd())
    if not command:
        # Fall back to RunStart payload.
        from vernier.record.store import RecordStore

        reader = RecordStore(source_dir, frozen.run_id)
        for env_row in reader.read_events():
            if env_row["kind"] == "RunStart":
                payload = reader.read_payload(env_row)
                command = [str(x) for x in (payload.get("command") or [])]
                cwd = str(payload.get("cwd") or cwd)
                break
    if not command:
        typer.echo(
            "vern verify: recorded run has no command; cannot re-drive",
            err=True,
        )
        raise typer.Exit(code=2)

    replay_runs = out or (runs_dir.parent / "verify-runs")
    replay_id = uuid.uuid4().hex
    replay_dir = replay_runs / replay_id
    replay_dir.mkdir(parents=True, exist_ok=True)
    index_path = _index_beside(replay_runs)
    shim_dir = _write_shim(replay_dir)

    resolved = _resolve_command(command)
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(shim_dir) if not existing else f"{shim_dir}{os.pathsep}{existing}"
    )
    env["VERNIER_REPLAY"] = "1"
    env["VERNIER_REPLAY_SOURCE"] = str(source_dir.resolve())
    env["VERNIER_RUN_DIR"] = str(replay_dir.resolve())
    env["VERNIER_RUN_ID"] = replay_id
    env["VERNIER_INDEX"] = str(index_path.resolve())
    env["VERNIER_COMMAND_JSON"] = json.dumps(resolved)
    env["VERNIER_CWD"] = cwd
    # Capture stays off; replay session opens the store.
    env.pop("VERNIER_CAPTURE", None)
    env.setdefault("PYTHONSAFEPATH", "")
    env.pop("PYTHONNOUSERSITE", None)

    typer.echo(f"vern verify: source={run_id}", err=True)
    typer.echo(f"vern verify: replay_run={replay_id}", err=True)

    try:
        completed = subprocess.run(
            resolved,
            env=env,
            cwd=cwd if Path(cwd).is_dir() else None,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        typer.echo(f"vern verify: failed to launch command: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    combined = (completed.stderr or "") + (completed.stdout or "")
    if "ReplayMiss" in combined or completed.returncode == 3:
        typer.echo("vern verify: ReplayMiss during re-drive", err=True)
        if combined.strip():
            typer.echo(combined.strip(), err=True)
        raise typer.Exit(code=1)

    try:
        original_align = project_align_events(source_dir)
        replay_align = project_align_events(replay_dir)
        pairs = align(original_align, replay_align)
        original_traj = project_run(source_dir)
        replay_traj = project_run(replay_dir)
        verdict = classify_pair(original_traj, replay_traj)
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"vern verify: comparison failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    all_matched = bool(pairs) and all(p.relation is Relation.MATCHED for p in pairs)
    # Empty both sides is also "identical".
    if not original_align and not replay_align:
        all_matched = True

    if all_matched and verdict.severity.value == "NONE" and not verdict.rule_ids:
        typer.echo(
            f"OK {run_id} severity=NONE matched={len(pairs)}",
        )
        raise typer.Exit(code=0)

    typer.echo(
        f"FAIL {run_id} severity={verdict.severity.value} "
        f"rules={list(verdict.rule_ids)} matched_all={all_matched}",
        err=True,
    )
    if combined.strip():
        typer.echo(combined.strip()[-2000:], err=True)
    raise typer.Exit(code=1)


@app.command("ls")
def ls_cmd(
    index: Annotated[
        Path,
        typer.Option("--index", help="SQLite run index path."),
    ] = DEFAULT_INDEX_PATH,
) -> None:
    """List runs from the SQLite index (index only; JSONL is source of truth)."""
    rows = list_runs(index)
    if not rows:
        typer.echo("(no runs)")
        raise typer.Exit(code=0)
    for row in rows:
        typer.echo(
            f"{row.run_id}  events={row.event_count}  "
            f"created={row.created_at}  root={row.root_digest}"
        )
