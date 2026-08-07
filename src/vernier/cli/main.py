"""CLI entrypoint for the ``vern`` console script."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Annotated

import typer

from vernier.record.index import (
    DEFAULT_INDEX_PATH,
    list_runs,
    resolve_run_dir,
)
from vernier.record.store import DEFAULT_RUNS_DIR, verify_run

app = typer.Typer(
    name="vern",
    help="Vernier: deterministic capture, replay, and divergence classification.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Vernier: deterministic capture, replay, and divergence classification."""


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

    # Index lives beside the runs directory: .vernier/index.sqlite
    if runs_dir.name == "runs":
        index_path = runs_dir.parent / "index.sqlite"
    else:
        index_path = runs_dir / "index.sqlite"

    shim_dir = run_dir / "_shim"
    shim_dir.mkdir(parents=True, exist_ok=True)
    sitecustomize = shim_dir / "sitecustomize.py"
    sitecustomize.write_text(
        "import vernier.capture.bootstrap as _vernier_bootstrap\n"
        "_vernier_bootstrap.install()\n",
        encoding="utf-8",
        newline="\n",
    )

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(shim_dir) if not existing else f"{shim_dir}{os.pathsep}{existing}"
    )
    env["VERNIER_CAPTURE"] = "1"
    env["VERNIER_RUN_DIR"] = str(run_dir.resolve())
    env["VERNIER_RUN_ID"] = run_id
    env["VERNIER_INDEX"] = str(index_path.resolve())
    env.setdefault("PYTHONSAFEPATH", "")
    env.pop("PYTHONNOUSERSITE", None)

    typer.echo(f"vern record: run_id={run_id}", err=True)
    typer.echo(f"vern record: out={run_dir}", err=True)

    resolved = list(command)
    if resolved[0] in {"python", "python3"}:
        resolved[0] = sys.executable

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
