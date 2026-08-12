"""CLI entrypoint for the ``vern`` console script."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Annotated

import typer

from vernier.classify.types import Severity, severity_rank
from vernier.init_cmd import run_init
from vernier.record.index import (
    DEFAULT_INDEX_PATH,
    list_runs,
    resolve_run_dir,
)
from vernier.record.store import DEFAULT_RUNS_DIR, verify_run
from vernier.replay.redrive import (
    all_matched,
    index_beside,
    redrive,
    resolve_command,
    write_shim,
)
from vernier.report import (
    build_gate_report,
    build_report,
    gate_report_to_jcs,
    render_gate_comment,
    render_human,
    report_to_jcs,
)

NETWORK_GUARANTEE = (
    "Network guarantee: vern makes ZERO network requests other than to the "
    "model provider you have already configured. No telemetry, no version "
    "check, no analytics, no phone-home."
)

app = typer.Typer(
    name="vern",
    help=(
        "Vernier: deterministic capture, replay, and divergence classification.\n\n"
        + NETWORK_GUARANTEE
    ),
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Vernier: deterministic capture, replay, and divergence classification.

    Network guarantee: vern makes ZERO network requests other than to the
    model provider you have already configured. No telemetry, no version
    check, no analytics, no phone-home.
    """


def _resolve_python(command: list[str]) -> list[str]:
    import sys

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
    index_path = index_beside(runs_dir)
    shim_dir = write_shim(run_dir)

    resolved = _resolve_python(command)
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

    try:
        resolve_command(source_dir)
    except ValueError as exc:
        typer.echo(f"vern verify: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    replay_runs = out or (runs_dir.parent / "verify-runs")
    typer.echo(f"vern verify: source={run_id}", err=True)

    try:
        result = redrive(source_dir, out_dir=replay_runs, mode="full_stub")
    except OSError as exc:
        typer.echo(f"vern verify: failed to launch command: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"vern verify: comparison failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"vern verify: replay_run={result.candidate_run_id}", err=True)

    if (
        "ReplayMiss" in result.agent_output
        and "ReplayToolMiss" not in result.agent_output
    ):
        typer.echo("vern verify: ReplayMiss during re-drive", err=True)
        if result.agent_output.strip():
            typer.echo(result.agent_output.strip()[-2000:], err=True)
        raise typer.Exit(code=1)

    if (
        all_matched(result.pairs)
        and result.verdict.severity is Severity.NONE
        and not result.verdict.rule_ids
    ):
        typer.echo(
            f"OK {run_id} severity=NONE matched={len(result.pairs)}",
        )
        raise typer.Exit(code=0)

    typer.echo(
        f"FAIL {run_id} severity={result.verdict.severity.value} "
        f"rules={list(result.verdict.rule_ids)} "
        f"matched_all={all_matched(result.pairs)}",
        err=True,
    )
    raise typer.Exit(code=1)


@app.command()
def diff(
    run_id: Annotated[str, typer.Argument(help="Baseline run id.")],
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override model on live LLM requests."),
    ] = None,
    prompt_file: Annotated[
        Path | None,
        typer.Option(
            "--prompt-file",
            help="Override system prompt from a UTF-8 file.",
        ),
    ] = None,
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
        typer.Option("--out", help="Directory for the live re-drive run."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit vernier-report-v1 JSON (JCS)."),
    ] = False,
) -> None:
    """Live re-drive: LLM live, tools from the record; align and report.

    Exit codes: 0 no divergence (severity NONE, no rules), 1 divergence,
    2 operational error.
    """
    try:
        source_dir = resolve_run_dir(run_id, runs_dir=runs_dir, index_path=index)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    integrity = verify_run(source_dir)
    if not integrity.ok:
        typer.echo(f"vern diff: record integrity failed for {run_id}", err=True)
        for error in integrity.errors:
            typer.echo(f"  - {error}", err=True)
        raise typer.Exit(code=2)

    if prompt_file is not None and not prompt_file.is_file():
        typer.echo(f"vern diff: prompt file not found: {prompt_file}", err=True)
        raise typer.Exit(code=2)

    try:
        resolve_command(source_dir)
    except ValueError as exc:
        typer.echo(f"vern diff: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    live_runs = out or (runs_dir.parent / "diff-runs")
    typer.echo(f"vern diff: source={run_id}", err=True)
    if model:
        typer.echo(f"vern diff: model={model}", err=True)

    try:
        result = redrive(
            source_dir,
            out_dir=live_runs,
            mode="live_diff",
            model=model,
            prompt_file=prompt_file,
        )
    except OSError as exc:
        typer.echo(f"vern diff: failed to launch command: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"vern diff: failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"vern diff: candidate={result.candidate_run_id}", err=True)

    report = build_report(
        source_run_id=result.source_run_id,
        candidate_run_id=result.candidate_run_id,
        verdict=result.verdict,
        pairs=result.pairs,
        traj_a=result.traj_a,
        traj_b=result.traj_b,
    )

    if as_json:
        typer.echo(report_to_jcs(report).decode("utf-8"), nl=False)
    else:
        typer.echo(
            render_human(
                source_run_id=result.source_run_id,
                candidate_run_id=result.candidate_run_id,
                verdict=result.verdict,
                pairs=result.pairs,
                traj_a=result.traj_a,
                traj_b=result.traj_b,
            ),
            nl=False,
        )

    if result.verdict.harness_fault:
        typer.echo(
            f"vern diff: HARNESS_FAULT: {result.verdict.harness_fault}",
            err=True,
        )
        raise typer.Exit(code=2)

    if (
        result.verdict.severity is Severity.NONE
        and not result.verdict.rule_ids
        and all_matched(result.pairs)
    ):
        raise typer.Exit(code=0)
    raise typer.Exit(code=1)


def _has_replay_miss(agent_output: str) -> bool:
    return "ReplayMiss" in agent_output and "ReplayToolMiss" not in agent_output


@app.command()
def gate(
    run_id: Annotated[
        str | None,
        typer.Argument(help="Run id to gate (omit with --corpus)."),
    ] = None,
    max_severity: Annotated[
        str,
        typer.Option(
            "--max-severity",
            help="Fail if verdict severity is above this level.",
        ),
    ] = "MEDIUM",
    corpus: Annotated[
        Path | None,
        typer.Option(
            "--corpus",
            help="Directory of run folders (or scenario/run fixtures) to gate.",
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override model for live re-drives."),
    ] = None,
    baseline_model: Annotated[
        str | None,
        typer.Option(
            "--baseline-model",
            help="Label for the recorded baseline model (report metadata only).",
        ),
    ] = None,
    prompt_file: Annotated[
        Path | None,
        typer.Option("--prompt-file", help="Override system prompt file."),
    ] = None,
    fail_on_miss: Annotated[
        bool,
        typer.Option(
            "--fail-on-miss/--no-fail-on-miss",
            help="Exit 2 when a hard ReplayMiss occurs during re-drive.",
        ),
    ] = True,
    json_out: Annotated[
        Path | None,
        typer.Option(
            "--json-out",
            help="Write vernier-gate-report-v1 JSON (JCS) to this path.",
        ),
    ] = None,
    comment_out: Annotated[
        Path | None,
        typer.Option(
            "--comment-out",
            help="Write PR-comment markdown summary to this path.",
        ),
    ] = None,
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
        typer.Option("--out", help="Directory for live re-drive runs."),
    ] = None,
) -> None:
    """Gate a run (or corpus) by max severity after a live re-drive.

    Exit codes: 0 at or below threshold, 1 above, 2 operational error.
    """
    try:
        threshold = Severity(max_severity.upper())
    except ValueError as exc:
        typer.echo(
            f"vern gate: invalid severity {max_severity!r}; "
            f"expected one of {[s.value for s in Severity]}",
            err=True,
        )
        raise typer.Exit(code=2) from exc

    targets: list[tuple[str, Path]] = []
    if corpus is not None:
        if not corpus.is_dir():
            typer.echo(f"vern gate: corpus not found: {corpus}", err=True)
            raise typer.Exit(code=2)
        for child in sorted(corpus.iterdir()):
            if not child.is_dir():
                continue
            # Fixture layout: <name>/run/ or plain <run_id>/
            if (child / "run" / "events.jsonl").is_file():
                targets.append((child.name, child / "run"))
            elif (child / "events.jsonl").is_file():
                targets.append((child.name, child))
        if not targets:
            typer.echo("vern gate: corpus has no runs", err=True)
            raise typer.Exit(code=2)
    else:
        if not run_id:
            typer.echo(
                "Usage: vern gate <run_id> --max-severity MEDIUM\n"
                "   or: vern gate --corpus <dir> --max-severity MEDIUM",
                err=True,
            )
            raise typer.Exit(code=2)
        try:
            source_dir = resolve_run_dir(run_id, runs_dir=runs_dir, index_path=index)
        except FileNotFoundError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc
        targets.append((run_id, source_dir))

    live_runs = out or (runs_dir.parent / "gate-runs")
    worst: Severity = Severity.NONE
    worst_label = ""
    failures = 0
    miss_failures = 0
    run_rows: list[dict[str, object]] = []

    for label, source_dir in targets:
        integrity = verify_run(source_dir)
        if not integrity.ok:
            typer.echo(f"vern gate: integrity failed for {label}", err=True)
            raise typer.Exit(code=2)
        try:
            result = redrive(
                source_dir,
                out_dir=live_runs,
                mode="live_diff",
                model=model,
                prompt_file=prompt_file,
            )
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"vern gate: failed on {label}: {exc}", err=True)
            raise typer.Exit(code=2) from exc

        sev = result.verdict.severity
        miss = _has_replay_miss(result.agent_output)
        harness = bool(result.verdict.harness_fault)
        above = severity_rank(sev) > severity_rank(threshold)
        typer.echo(
            f"{label}: severity={sev.value} rules={list(result.verdict.rule_ids)}"
            + (" miss=1" if miss else "")
            + (" HARNESS_FAULT" if harness else "")
        )
        if harness:
            typer.echo(f"  {result.verdict.harness_fault}", err=True)
        run_rows.append(
            {
                "label": label,
                "run_id": result.source_run_id,
                "candidate_run_id": result.candidate_run_id,
                "severity": sev.value,
                "rule_ids": list(result.verdict.rule_ids),
                "above_threshold": above,
                "miss": miss,
                "harness_fault": result.verdict.harness_fault,
            }
        )
        if severity_rank(sev) > severity_rank(worst):
            worst = sev
            worst_label = label
        if above:
            failures += 1
        if (miss and fail_on_miss) or harness:
            miss_failures += 1

    report = build_gate_report(
        runs=run_rows,
        max_severity=threshold.value,
        model=model,
        baseline_model=baseline_model,
        fail_on_miss=fail_on_miss,
    )
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_bytes(gate_report_to_jcs(report))
        typer.echo(f"vern gate: wrote {json_out}", err=True)
    if comment_out is not None:
        comment_out.parent.mkdir(parents=True, exist_ok=True)
        comment_out.write_text(render_gate_comment(report), encoding="utf-8")
        typer.echo(f"vern gate: wrote {comment_out}", err=True)

    typer.echo(
        f"vern gate: worst={worst.value}"
        + (f" ({worst_label})" if worst_label else "")
        + f" threshold={threshold.value}"
        + f" n={len(run_rows)}"
    )
    if miss_failures:
        typer.echo(
            "vern gate: ReplayMiss or HARNESS_FAULT (fail closed)",
            err=True,
        )
        raise typer.Exit(code=2)
    if failures:
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


@app.command("init")
def init_cmd(
    path: Annotated[
        Path,
        typer.Argument(help="Repository root to scaffold into."),
    ] = Path("."),
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite existing vernier.toml / workflow."),
    ] = False,
    action_ref: Annotated[
        str,
        typer.Option(
            "--action-ref",
            help="GitHub Action uses: value (owner/repo@ref).",
        ),
    ] = "gautamtalksdev/Vernier@main",
    corpus_path: Annotated[
        str,
        typer.Option("--corpus-path", help="Default corpus path for the workflow."),
    ] = "vernier-corpus",
    max_severity: Annotated[
        str,
        typer.Option("--max-severity", help="Default max severity for the workflow."),
    ] = "MEDIUM",
) -> None:
    """Scaffold vernier.toml and a .github/workflows vernier-gate example."""
    try:
        written = run_init(
            path,
            force=force,
            action_ref=action_ref,
            corpus_path=corpus_path,
            max_severity=max_severity,
        )
    except FileExistsError as exc:
        typer.echo(f"vern init: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    for item in written:
        typer.echo(f"wrote {item}")
    typer.echo(
        f"Next: record baseline runs into {corpus_path}/ then push. See docs/CI.md."
    )


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
