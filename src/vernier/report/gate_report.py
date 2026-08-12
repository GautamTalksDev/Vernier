"""Corpus gate summary report + PR comment markdown (CI distribution)."""

from __future__ import annotations

from typing import Any

from vernier.canonical._jcs import serialize_jcs
from vernier.classify.types import Severity, severity_rank
from vernier.report.escape import escape_untrusted

GATE_REPORT_SCHEMA = "vernier-gate-report-v1"


def build_gate_report(
    *,
    runs: list[dict[str, Any]],
    max_severity: str,
    model: str | None,
    baseline_model: str | None,
    fail_on_miss: bool,
) -> dict[str, Any]:
    """Aggregate per-run gate results into a CI-friendly report."""
    worst = Severity.NONE
    all_rules: set[str] = set()
    for row in runs:
        sev = Severity(str(row.get("severity", "NONE")))
        if severity_rank(sev) > severity_rank(worst):
            worst = sev
        for rid in row.get("rule_ids") or []:
            all_rules.add(str(rid))
    return {
        "schema_version": GATE_REPORT_SCHEMA,
        "n_runs": len(runs),
        "worst_severity": worst.value,
        "max_severity": max_severity.upper(),
        "model": model or "",
        "baseline_model": baseline_model or "",
        "fail_on_miss": fail_on_miss,
        "rule_ids": sorted(all_rules),
        "runs": runs,
        "failed": any(bool(r.get("above_threshold")) for r in runs)
        or any(bool(r.get("harness_fault")) for r in runs)
        or any(bool(r.get("miss")) for r in runs if fail_on_miss),
    }


def gate_report_to_jcs(report: dict[str, Any]) -> bytes:
    return serialize_jcs(report) + b"\n"


def render_gate_comment(report: dict[str, Any]) -> str:
    """Markdown summary for PR comments (escaped untrusted fields)."""
    n = int(report.get("n_runs") or 0)
    worst = escape_untrusted(str(report.get("worst_severity") or "NONE"))
    threshold = escape_untrusted(str(report.get("max_severity") or ""))
    rules = report.get("rule_ids") or []
    rules_s = ", ".join(escape_untrusted(str(r)) for r in rules) or "(none)"
    failed = bool(report.get("failed"))
    status = "FAILED" if failed else "PASSED"
    lines = [
        "<!-- vernier-gate -->",
        f"### Vernier gate: **{status}**",
        "",
        f"- Runs checked: **{n}**",
        f"- Worst severity: **{worst}** (threshold `{threshold}`)",
        f"- Rule IDs fired: `{rules_s}`",
    ]
    model = report.get("model") or ""
    baseline = report.get("baseline_model") or ""
    if model:
        lines.append(f"- Candidate model: `{escape_untrusted(str(model))}`")
    if baseline:
        lines.append(f"- Baseline model: `{escape_untrusted(str(baseline))}`")
    lines.extend(
        [
            "",
            "<details>",
            "<summary>Per-run detail</summary>",
            "",
            "| Run | Severity | Rules | Above threshold | Miss |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in report.get("runs") or []:
        label = escape_untrusted(str(row.get("label") or row.get("run_id") or "?"))
        sev = escape_untrusted(str(row.get("severity") or "NONE"))
        rids = ",".join(escape_untrusted(str(x)) for x in (row.get("rule_ids") or []))
        above = "yes" if row.get("above_threshold") else "no"
        miss = "yes" if row.get("miss") else "no"
        lines.append(f"| `{label}` | {sev} | `{rids or '—'}` | {above} | {miss} |")
    lines.extend(["", "</details>", ""])
    return "\n".join(lines)
