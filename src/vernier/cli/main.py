"""CLI entrypoint for the ``vern`` console script."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Annotated

import typer

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
        typer.Option("--out", "-o", help="Directory for the run record."),
    ] = Path(".vernier/run"),
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
    run_dir = out if out.name == run_id else out / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

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
    # Ensure site customization is imported.
    env["PYTHONPATH"] = env["PYTHONPATH"]
    env.setdefault("PYTHONSAFEPATH", "")
    # Force sitecustomize discovery on PYTHONPATH.
    env.pop("PYTHONNOUSERSITE", None)

    typer.echo(f"vern record: run_id={run_id}", err=True)
    typer.echo(f"vern record: out={run_dir}", err=True)

    # Prefer the same Python that provides vern when command starts with python.
    resolved = list(command)
    if resolved[0] in {"python", "python3"}:
        resolved[0] = sys.executable

    try:
        completed = subprocess.run(resolved, env=env, check=False)
    finally:
        # Keep shim for debugging; remove only if empty failure?
        pass

    if completed.returncode != 0:
        raise typer.Exit(code=completed.returncode)

    typer.echo(f"vern record: complete -> {run_dir / 'events.jsonl'}", err=True)
