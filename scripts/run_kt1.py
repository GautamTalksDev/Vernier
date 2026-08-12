#!/usr/bin/env python3
"""Hosted KT-1 evaluation runner (config placeholders — do not invent model IDs).

Pairs (fill MODEL IDs from provider docs on run day):
  N0  hosted null          — model vs itself (mandatory floor)
  P1  within-vendor gen    — predecessor -> successor
  P2  within-vendor tier   — flagship -> cheap tier
  P3  cross-vendor         — vendor A model -> vendor B model

All 30 corpus runs × 3 seeds × each pair.
Request-parity guard active (Vernier live-diff).
Hard abort when estimated spend exceeds $100.
Emits kt1.csv + kt1_summary.md (same shape as tier_study.md).

Usage:
  uv run python scripts/run_kt1.py --dry-run   # validate + cost estimate only
  uv run python scripts/run_kt1.py            # execute (after filling config)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from vernier.canonical._jcs import serialize_jcs
from vernier.record.store import RecordStore, load_run
from vernier.replay.harness_guard import tool_call_count
from vernier.replay.project import project_run
from vernier.replay.request_parity import request_options_from_body

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "testdata" / "corpus" / "MANIFEST.json"
DEFAULT_CSV = ROOT / "kt1.csv"
DEFAULT_MD = ROOT / "kt1_summary.md"
DEFAULT_CONFIG = ROOT / "kt1_config.json"

SEEDS = ("42", "43", "44")
TEMPERATURE = "0"
STREAM = "false"
REQUEST_OPTIONS = "null"
PER_RUN_TIMEOUT_S = 900
COST_ABORT_USD = 100.0
RULE_IDS = [f"D{i:02d}" for i in range(1, 13)]

# ---------------------------------------------------------------------------
# Config placeholders — replace on run day from provider docs / snapshot APIs.
# Use immutable snapshot IDs (not floating aliases) when the provider offers them.
# ---------------------------------------------------------------------------

PLACEHOLDER = "REPLACE_ME"

DEFAULT_CONFIG_DOC: dict[str, object] = {
    "schema_version": "kt1-config-v1",
    "cost_abort_usd": COST_ABORT_USD,
    "temperature": 0.0,
    "stream": False,
    "seeds": list(SEEDS),
    "baseline_corpus_model": "qwen3:4b-instruct",
    "notes": (
        "Fill provider base URLs, API keys via env, and snapshot model IDs. "
        "Do not use floating aliases if a dated snapshot is available."
    ),
    "providers": {
        "vendor_a": {
            "base_url": "https://REPLACE_ME.example/v1",
            "api_key_env": "VENDOR_A_API_KEY",
        },
        "vendor_b": {
            "base_url": "https://REPLACE_ME.example/v1",
            "api_key_env": "VENDOR_B_API_KEY",
        },
    },
    "pricing_usd_per_1m_tokens": {
        # Rough planning rates; dry-run cost uses these. Update on run day.
        PLACEHOLDER: {"input": 0.0, "output": 0.0},
    },
    "pairs": [
        {
            "pair_id": "N0",
            "label": "hosted null (model vs itself)",
            "baseline_model": PLACEHOLDER,
            "candidate_model": PLACEHOLDER,
            "provider": "vendor_a",
            "kind": "null",
        },
        {
            "pair_id": "P1",
            "label": "within-vendor generational (predecessor -> successor)",
            "baseline_model": PLACEHOLDER,
            "candidate_model": PLACEHOLDER,
            "provider": "vendor_a",
            "kind": "generational",
        },
        {
            "pair_id": "P2",
            "label": "within-vendor tier (flagship -> cheap)",
            "baseline_model": PLACEHOLDER,
            "candidate_model": PLACEHOLDER,
            "provider": "vendor_a",
            "kind": "tier",
        },
        {
            "pair_id": "P3",
            "label": "cross-vendor",
            "baseline_model": PLACEHOLDER,
            "candidate_model": PLACEHOLDER,
            "provider": "vendor_b",
            "kind": "cross_vendor",
            "candidate_provider": "vendor_b",
            "baseline_provider": "vendor_a",
        },
    ],
    # Optional: map snapshot id -> human alias for the summary only.
    "snapshot_labels": {},
}

FIELDNAMES = [
    "pair_id",
    "pair_label",
    "pair_kind",
    "run_id",
    "corpus_label",
    "agent_domain",
    "baseline_model_snapshot",
    "candidate_model_snapshot",
    "provider",
    "temperature",
    "seed",
    "request_options",
    "baseline_request_options",
    "candidate_request_options",
    "stream",
    "status",
    "candidate_run_id",
    "baseline_tool_count",
    "candidate_tool_count",
    "severity",
    "rule_ids",
    "harness_fault",
    "wall_clock_s",
    "exit_code",
    "est_cost_usd",
    "running_cost_usd",
]


@dataclass(frozen=True, slots=True)
class PairSpec:
    pair_id: str
    label: str
    kind: str
    baseline_model: str
    candidate_model: str
    provider: str
    baseline_provider: str
    candidate_provider: str


def _domain(corpus_label: str) -> str:
    if "_" in corpus_label:
        return corpus_label.rsplit("_", 1)[0]
    return corpus_label or "unknown"


def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (float("nan"), float("nan"))
    phat = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = phat + z2 / (2.0 * n)
    margin = z * math.sqrt((phat * (1.0 - phat) + z2 / (4.0 * n)) / n)
    low = (centre - margin) / denom
    high = (centre + margin) / denom
    return (max(0.0, low), min(1.0, high))


def _parse_report(stdout: str) -> dict[str, object] | None:
    text = (stdout or "").strip()
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return None


def _row_key(row: dict[str, str]) -> str:
    return (
        f"{row['pair_id']}|{row['run_id']}|{row['seed']}|"
        f"{row['candidate_model_snapshot']}"
    )


def _load_config(path: Path) -> dict[str, object]:
    if not path.is_file():
        path.write_text(json.dumps(DEFAULT_CONFIG_DOC, indent=2) + "\n", encoding="utf-8")
        print(f"wrote placeholder config {path}", flush=True)
    return json.loads(path.read_text(encoding="utf-8"))


def _pairs_from_config(cfg: dict[str, object]) -> list[PairSpec]:
    out: list[PairSpec] = []
    for raw in cfg.get("pairs") or []:
        assert isinstance(raw, dict)
        provider = str(raw.get("provider") or "vendor_a")
        out.append(
            PairSpec(
                pair_id=str(raw["pair_id"]),
                label=str(raw.get("label") or raw["pair_id"]),
                kind=str(raw.get("kind") or ""),
                baseline_model=str(raw["baseline_model"]),
                candidate_model=str(raw["candidate_model"]),
                provider=provider,
                baseline_provider=str(raw.get("baseline_provider") or provider),
                candidate_provider=str(raw.get("candidate_provider") or provider),
            )
        )
    return out


def _validate_config(cfg: dict[str, object], *, dry_run: bool) -> list[str]:
    errors: list[str] = []
    pairs = _pairs_from_config(cfg)
    if not pairs:
        errors.append("config.pairs is empty")
    kinds = {p.kind for p in pairs}
    if "null" not in kinds:
        errors.append("mandatory hosted null pair (kind=null) is missing")
    providers = cfg.get("providers") or {}
    if not isinstance(providers, dict):
        errors.append("config.providers must be an object")
        providers = {}
    for p in pairs:
        for mid in (p.baseline_model, p.candidate_model):
            if mid == PLACEHOLDER or not mid.strip():
                errors.append(f"{p.pair_id}: model id still {PLACEHOLDER!r}")
        for prov in {p.baseline_provider, p.candidate_provider}:
            if prov not in providers:
                errors.append(f"{p.pair_id}: unknown provider {prov!r}")
            else:
                meta = providers[prov]
                assert isinstance(meta, dict)
                base = str(meta.get("base_url") or "")
                if PLACEHOLDER in base or not base.startswith("http"):
                    errors.append(f"provider {prov}: base_url not set")
                key_env = str(meta.get("api_key_env") or "")
                if not key_env:
                    errors.append(f"provider {prov}: api_key_env missing")
                elif not dry_run and not (os.environ.get(key_env) or "").strip():
                    errors.append(f"provider {prov}: env {key_env} unset")
    null_pair = next((x for x in pairs if x.kind == "null"), None)
    if null_pair is not None:
        if null_pair.baseline_model != null_pair.candidate_model and PLACEHOLDER not in (
            null_pair.baseline_model,
            null_pair.candidate_model,
        ):
            errors.append("null pair must use the same snapshot id on both legs")
    return errors


def _estimate_tokens_per_run(entry: dict[str, object]) -> tuple[int, int]:
    """Rough in/out token estimate from recorded corpus body sizes."""
    path = ROOT / "testdata" / "corpus" / str(entry["path"])
    if not path.is_dir():
        return (4000, 800)
    try:
        frozen = load_run(path)
        reader = RecordStore(path, frozen.run_id)
        total = 0
        n_req = 0
        for env in reader.read_events():
            if env["kind"] != "LlmRequest":
                continue
            p = reader.read_payload(env)
            total += int(p.get("body_size") or 0)
            n_req += 1
        # ~4 chars/token; assume similar completion size per request.
        inp = max(500, total // 4)
        out = max(200, 200 * max(1, n_req))
        return (inp, out)
    except (OSError, ValueError):
        return (4000, 800)


def _price_for(cfg: dict[str, object], model: str) -> tuple[float, float]:
    pricing = cfg.get("pricing_usd_per_1m_tokens") or {}
    if not isinstance(pricing, dict):
        return (0.0, 0.0)
    row = pricing.get(model) or pricing.get(PLACEHOLDER) or {}
    if not isinstance(row, dict):
        return (0.0, 0.0)
    return (float(row.get("input") or 0.0), float(row.get("output") or 0.0))


def _est_cost_usd(cfg: dict[str, object], model: str, inp: int, out: int) -> float:
    pin, pout = _price_for(cfg, model)
    return (inp * pin + out * pout) / 1_000_000.0


def _first_request_options(run_dir: Path) -> str:
    if not run_dir.is_dir():
        return ""
    try:
        frozen = load_run(run_dir)
        reader = RecordStore(run_dir, frozen.run_id)
        for env in reader.read_events():
            if env["kind"] != "LlmRequest":
                continue
            payload = reader.read_payload(env)
            ref = payload.get("body_blob_ref")
            if not ref:
                return "(missing-body)"
            return request_options_from_body(reader.read_blob(ref))
    except (OSError, ValueError):
        return "(unreadable)"
    return "(no-request)"


def _provider_env(
    cfg: dict[str, object],
    provider: str,
    *,
    model: str,
    seed: str,
    base_env: dict[str, str],
) -> dict[str, str]:
    providers = cfg["providers"]
    assert isinstance(providers, dict)
    meta = providers[provider]
    assert isinstance(meta, dict)
    env = base_env.copy()
    base_url = str(meta["base_url"]).rstrip("/")
    key_env = str(meta["api_key_env"])
    api_key = (os.environ.get(key_env) or "").strip() or "MISSING"
    env["VERNIER_LLM_BASE_URL"] = base_url
    env["OPENAI_BASE_URL"] = base_url
    env["OPENAI_API_KEY"] = api_key
    env["VERNIER_LLM_API_KEY"] = api_key
    env["VERNIER_LLM_TEMPERATURE"] = str(cfg.get("temperature", TEMPERATURE))
    env["VERNIER_LLM_STREAM"] = "false" if not cfg.get("stream") else "true"
    env["VERNIER_LLM_SEED"] = seed
    env.pop("VERNIER_LLM_NUM_CTX", None)
    env.pop("VERNIER_CONTROL_DROP_TOOL", None)
    env.setdefault("VERNIER_LLM_TIMEOUT_S", "600")
    # Snapshot string is passed as --model; agent also sees VERNIER_OVERRIDE_MODEL via vern diff.
    _ = model
    return env


def _is_divergence(row: dict[str, str]) -> bool:
    if row.get("status") != "completed":
        return False
    return (row.get("severity") or "").strip() not in {"", "NONE"}


def _emit_md(path: Path, rows: list[dict[str, str]], cfg: dict[str, object]) -> None:
    by_pair: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_pair[r["pair_id"]].append(r)

    null_rows = by_pair.get("N0", [])
    null_scored = [r for r in null_rows if r.get("status") == "completed"]
    null_div = sum(1 for r in null_scored if _is_divergence(r))
    null_n = len(null_scored)
    null_rate = (null_div / null_n) if null_n else float("nan")

    lines = [
        "# KT-1 hosted evaluation summary",
        "",
        "Hosted upgrade pairs with a **mandatory null pair (N0)**. "
        "NET = pair rate − N0 null rate. HARNESS_FAULT never counts as divergence.",
        "",
        f"- temperature=`{cfg.get('temperature', TEMPERATURE)}`, "
        f"stream=`{cfg.get('stream', False)}`, "
        f"request_options=`{REQUEST_OPTIONS}`",
        f"- seeds: {', '.join(str(s) for s in (cfg.get('seeds') or SEEDS))}",
        f"- cost abort: ${float(cfg.get('cost_abort_usd') or COST_ABORT_USD):.2f}",
        f"- total rows: {len(rows)}",
        "",
        "## Headline net rates",
        "",
        "| Pair | Kind | Candidate snapshot | Raw div | N scored | Raw rate | "
        "Wilson 95% CI | NET vs N0 | HARNESS_FAULT |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: |",
    ]

    pair_order = [str(p["pair_id"]) for p in (cfg.get("pairs") or []) if isinstance(p, dict)]
    for pid in pair_order or sorted(by_pair):
        prow = by_pair.get(pid, [])
        if not prow:
            continue
        scored = [r for r in prow if r.get("status") == "completed"]
        faults = [r for r in prow if r.get("status") == "HARNESS_FAULT"]
        k = sum(1 for r in scored if _is_divergence(r))
        n = len(scored)
        rate = (k / n) if n else float("nan")
        lo, hi = _wilson_ci(k, n)
        kind = prow[0].get("pair_kind") or ""
        cand = prow[0].get("candidate_model_snapshot") or ""
        if pid == "N0":
            net_s = f"{rate:.4f} (null)" if n else "n/a"
        else:
            net = (rate - null_rate) if n and null_n else float("nan")
            net_s = f"{net:+.4f}" if n and null_n else "n/a"
        ci = f"[{lo:.4f}, {hi:.4f}]" if n else "n/a"
        rate_s = f"{rate:.4f}" if n else "n/a"
        lines.append(
            f"| {pid} | {kind} | `{cand}` | {k} | {n} | {rate_s} | {ci} | "
            f"{net_s} | {len(faults)} |"
        )

    lines.extend(
        [
            "",
            f"N0 null rate: **{null_div}/{null_n} = {null_rate:.4f}**"
            if null_n
            else "N0 null rate: n/a (null pair missing or unscored)",
            "",
        ]
    )

    for pid in pair_order or sorted(by_pair):
        prow = by_pair.get(pid, [])
        if not prow:
            continue
        scored = [r for r in prow if r.get("status") == "completed"]
        faults = [r for r in prow if r.get("status") == "HARNESS_FAULT"]
        k = sum(1 for r in scored if _is_divergence(r))
        n = len(scored)
        rate = (k / n) if n else float("nan")
        lo, hi = _wilson_ci(k, n)
        lines.extend(
            [
                f"## {pid} — {prow[0].get('pair_label')}",
                "",
                f"- kind: `{prow[0].get('pair_kind')}`",
                f"- baseline snapshot: `{prow[0].get('baseline_model_snapshot')}`",
                f"- candidate snapshot: `{prow[0].get('candidate_model_snapshot')}`",
                f"- comparisons: {len(prow)} (scored={n}, HARNESS_FAULT={len(faults)})",
                f"- raw divergence: {k}/{n} = {rate:.4f}" if n else "- raw divergence: n/a",
                f"- Wilson 95% CI: [{lo:.4f}, {hi:.4f}]" if n else "- Wilson 95% CI: n/a",
                (
                    f"- **NET vs N0:** {(rate - null_rate):+.4f}"
                    if pid != "N0" and n and null_n
                    else (
                        f"- **null rate:** {rate:.4f}"
                        if pid == "N0" and n
                        else "- NET: n/a"
                    )
                ),
                f"- HARNESS_FAULT count: **{len(faults)}** (excluded from rates)",
                "",
                "### Rule-ID histogram (D01–D12)",
                "",
            ]
        )
        hist: Counter[str] = Counter({rid: 0 for rid in RULE_IDS})
        for r in scored:
            if not _is_divergence(r):
                continue
            for rid in (r.get("rule_ids") or "").split("|"):
                rid = rid.strip()
                if rid:
                    hist[rid] += 1
        for rid in RULE_IDS:
            lines.append(f"- {rid}: {hist.get(rid, 0)}")

        lines.extend(["", "### Per-agent-domain divergence", ""])
        lines.append("| Domain | Diverged | Scored | Rate |")
        lines.append("| --- | ---: | ---: | ---: |")
        by_dom: dict[str, list[dict[str, str]]] = defaultdict(list)
        for r in scored:
            by_dom[r.get("agent_domain") or "unknown"].append(r)
        for dom in sorted(by_dom):
            rs = by_dom[dom]
            dk = sum(1 for r in rs if _is_divergence(r))
            dn = len(rs)
            lines.append(f"| {dom} | {dk} | {dn} | {dk / dn:.4f} |")
        if faults:
            lines.extend(["", f"### HARNESS_FAULT detail ({len(faults)})", ""])
            for reason, cnt in Counter(
                (r.get("harness_fault") or "(empty)")[:120] for r in faults
            ).most_common():
                lines.append(f"- `{reason}`: {cnt}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _dry_run_report(
    cfg: dict[str, object],
    entries: list[dict[str, object]],
    pairs: list[PairSpec],
) -> int:
    seeds = [str(s) for s in (cfg.get("seeds") or SEEDS)]
    abort = float(cfg.get("cost_abort_usd") or COST_ABORT_USD)
    print("KT-1 dry-run (no provider calls)", flush=True)
    print(f"  corpus runs: {len(entries)}", flush=True)
    print(f"  seeds: {seeds}", flush=True)
    print(f"  pairs: {[p.pair_id for p in pairs]}", flush=True)
    print(f"  jobs: {len(entries) * len(seeds) * len(pairs)}", flush=True)
    print(f"  cost abort: ${abort:.2f}", flush=True)

    total = 0.0
    for p in pairs:
        pair_cost = 0.0
        for entry in entries:
            inp, out = _estimate_tokens_per_run(entry)
            for _seed in seeds:
                # Candidate live-diff dominates cost; baseline is recorded.
                pair_cost += _est_cost_usd(cfg, p.candidate_model, inp, out)
        total += pair_cost
        print(
            f"  {p.pair_id} ({p.kind}): est ${pair_cost:.2f}  "
            f"baseline=`{p.baseline_model}` candidate=`{p.candidate_model}`",
            flush=True,
        )
    print(f"  TOTAL estimated spend: ${total:.2f}", flush=True)
    if total > abort:
        print(
            f"  ABORT THRESHOLD WOULD TRIP: estimate ${total:.2f} > ${abort:.2f}",
            flush=True,
        )
        return 2
    print("  dry-run OK (under abort threshold)", flush=True)
    return 0


def _run_one(
    *,
    cfg: dict[str, object],
    pair: PairSpec,
    seed: str,
    entry: dict[str, object],
    runs_dir: Path,
    diff_out: Path,
    running_cost: float,
) -> dict[str, str]:
    run_id = str(entry["run_id"])
    corpus_label = str(entry.get("label") or "")
    src = ROOT / "testdata" / "corpus" / str(entry["path"])
    dest = runs_dir / run_id
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    manifest_path = dest / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cwd"] = str(ROOT)
    manifest_path.write_bytes(serialize_jcs(manifest) + b"\n")

    inp, out = _estimate_tokens_per_run(entry)
    est = _est_cost_usd(cfg, pair.candidate_model, inp, out)
    env = _provider_env(
        cfg,
        pair.candidate_provider,
        model=pair.candidate_model,
        seed=seed,
        base_env=os.environ.copy(),
    )

    cmd = [
        "uv",
        "run",
        "vern",
        "diff",
        run_id,
        "--model",
        pair.candidate_model,
        "--runs-dir",
        str(runs_dir),
        "--out",
        str(diff_out),
        "--json",
    ]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=PER_RUN_TIMEOUT_S,
        )
        timed_out = False
        stdout, stderr = proc.stdout or "", proc.stderr or ""
        proc_rc = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (
            exc.stdout.decode("utf-8", errors="replace")
            if isinstance(exc.stdout, (bytes, bytearray))
            else (exc.stdout or "")
        )
        stderr = (
            exc.stderr.decode("utf-8", errors="replace")
            if isinstance(exc.stderr, (bytes, bytearray))
            else (exc.stderr or "")
        )
        proc_rc = -1

    wall = f"{time.perf_counter() - t0:.3f}"
    new_running = running_cost + est
    row: dict[str, str] = {
        "pair_id": pair.pair_id,
        "pair_label": pair.label,
        "pair_kind": pair.kind,
        "run_id": run_id,
        "corpus_label": corpus_label,
        "agent_domain": _domain(corpus_label),
        "baseline_model_snapshot": pair.baseline_model,
        "candidate_model_snapshot": pair.candidate_model,
        "provider": pair.candidate_provider,
        "temperature": str(cfg.get("temperature", TEMPERATURE)),
        "seed": seed,
        "request_options": REQUEST_OPTIONS,
        "baseline_request_options": "",
        "candidate_request_options": "",
        "stream": STREAM,
        "status": "operational_error",
        "candidate_run_id": "",
        "baseline_tool_count": "",
        "candidate_tool_count": "",
        "severity": "",
        "rule_ids": "",
        "harness_fault": "",
        "wall_clock_s": wall,
        "exit_code": str(proc_rc if not timed_out else -1),
        "est_cost_usd": f"{est:.6f}",
        "running_cost_usd": f"{new_running:.6f}",
    }
    if timed_out:
        row["severity"] = "OPS_TIMEOUT"
        return row

    row["baseline_tool_count"] = str(tool_call_count(project_run(dest)))
    row["baseline_request_options"] = _first_request_options(dest)
    report = _parse_report(stdout)
    cand_dir: Path | None = None
    if report and isinstance(report.get("candidate_run_id"), str):
        row["candidate_run_id"] = str(report["candidate_run_id"])
        cand_dir = diff_out / str(report["candidate_run_id"])
    if cand_dir is not None and cand_dir.is_dir():
        row["candidate_tool_count"] = str(tool_call_count(project_run(cand_dir)))
        row["candidate_request_options"] = _first_request_options(cand_dir)
    else:
        row["candidate_tool_count"] = "0"

    if report and isinstance(report.get("verdict"), dict):
        verdict = report["verdict"]
        assert isinstance(verdict, dict)
        hf = verdict.get("harness_fault")
        if isinstance(hf, str) and hf:
            row["status"] = "HARNESS_FAULT"
            row["harness_fault"] = hf
            row["severity"] = "HARNESS_FAULT"
        else:
            row["status"] = "completed"
            row["severity"] = str(verdict.get("severity") or "")
            rules = verdict.get("rule_ids") or []
            row["rule_ids"] = (
                "|".join(str(r) for r in rules) if isinstance(rules, list) else str(rules)
            )
    elif proc_rc == 2 or "HARNESS_FAULT" in (stderr or ""):
        row["status"] = "HARNESS_FAULT"
        row["severity"] = "HARNESS_FAULT"
        row["harness_fault"] = (stderr or "")[-500:]
    elif proc_rc != 0:
        row["severity"] = "OPS_ERROR"
    else:
        row["severity"] = "PARSE_ERROR"
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--pairs",
        default="",
        help="Comma-separated pair ids (default: all from config).",
    )
    parser.add_argument(
        "--write-config",
        action="store_true",
        help="Write/overwrite placeholder kt1_config.json and exit.",
    )
    args = parser.parse_args(argv)

    if args.write_config or not args.config.is_file():
        args.config.write_text(
            json.dumps(DEFAULT_CONFIG_DOC, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {args.config}", flush=True)
        if args.write_config:
            return 0

    cfg = _load_config(args.config)
    pairs = _pairs_from_config(cfg)
    if args.pairs.strip():
        want = {p.strip() for p in args.pairs.split(",") if p.strip()}
        pairs = [p for p in pairs if p.pair_id in want]

    errors = _validate_config(cfg, dry_run=args.dry_run)
    # For dry-run, placeholders are expected — report them as warnings, still estimate.
    if args.dry_run:
        for e in errors:
            print(f"config note: {e}", flush=True)
    else:
        hard = [e for e in errors if PLACEHOLDER in e or "unset" in e or "not set" in e]
        if hard:
            print("KT-1 config incomplete:", file=sys.stderr)
            for e in hard:
                print(f"  - {e}", file=sys.stderr)
            print("Fill kt1_config.json from provider docs, then re-run.", file=sys.stderr)
            return 2

    if not MANIFEST.is_file():
        print("missing corpus MANIFEST.json", file=sys.stderr)
        return 2
    entries = list(json.loads(MANIFEST.read_text(encoding="utf-8"))["runs"])
    if len(entries) != 30:
        print(f"expected 30 corpus runs, found {len(entries)}", file=sys.stderr)
        return 2

    if args.dry_run:
        return _dry_run_report(cfg, entries, pairs)

    abort = float(cfg.get("cost_abort_usd") or COST_ABORT_USD)
    seeds = [str(s) for s in (cfg.get("seeds") or SEEDS)]

    completed_map: dict[str, dict[str, str]] = {}
    rows: list[dict[str, str]] = []
    if args.resume and args.csv.is_file():
        with args.csv.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if (row.get("status") or "") in {
                    "completed",
                    "HARNESS_FAULT",
                    "operational_error",
                }:
                    completed_map[_row_key(row)] = row
                    rows.append(row)

    staging = ROOT / "testdata" / "corpus" / "_kt1_staging"
    runs_dir = staging / "runs"
    diff_out = staging / "diff-runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    diff_out.mkdir(parents=True, exist_ok=True)

    jobs: list[tuple[PairSpec, dict[str, object], str]] = []
    for pair in pairs:
        for seed in seeds:
            for entry in entries:
                jobs.append((pair, entry, seed))

    running_cost = sum(float(r.get("running_cost_usd") or 0) for r in rows[-1:]) if rows else 0.0
    if rows:
        try:
            running_cost = float(rows[-1].get("running_cost_usd") or 0.0)
        except ValueError:
            running_cost = 0.0

    print(
        f"KT-1: jobs={len(jobs)} resume={args.resume} "
        f"skip={len(completed_map)} abort=${abort:.2f}",
        flush=True,
    )

    for i, (pair, entry, seed) in enumerate(jobs, 1):
        probe = {
            "pair_id": pair.pair_id,
            "run_id": str(entry["run_id"]),
            "seed": seed,
            "candidate_model_snapshot": pair.candidate_model,
        }
        if args.resume and _row_key(probe) in completed_map:
            continue
        if running_cost >= abort:
            print(
                f"HARD ABORT: running cost ${running_cost:.2f} >= ${abort:.2f}",
                flush=True,
            )
            break
        label = str(entry.get("label") or "")
        print(
            f"[{i}/{len(jobs)}] {pair.pair_id} seed={seed} {label} "
            f"-> {pair.candidate_model} …",
            flush=True,
        )
        row = _run_one(
            cfg=cfg,
            pair=pair,
            seed=seed,
            entry=entry,
            runs_dir=runs_dir,
            diff_out=diff_out,
            running_cost=running_cost,
        )
        running_cost = float(row["running_cost_usd"])
        rows.append(row)
        with args.csv.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(
            f"  status={row['status']} severity={row['severity']} "
            f"cost=${running_cost:.4f} wall={row['wall_clock_s']}s",
            flush=True,
        )
        if running_cost >= abort:
            print(
                f"HARD ABORT after row: running cost ${running_cost:.2f} >= ${abort:.2f}",
                flush=True,
            )
            break

    _emit_md(args.md, rows, cfg)
    print(f"wrote {args.csv} ({len(rows)} rows) and {args.md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
