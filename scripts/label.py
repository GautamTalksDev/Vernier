#!/usr/bin/env python3
"""Blind hand-labelling for KT-2 ground truth.

Presents each LOW+ divergence from the KT-1 experiment one at a time.
Shows the aligned trajectory diff only. Records COSMETIC / BEHAVIOURAL /
UNCLEAR to an append-only JSONL. Reveals the classifier verdict only
AFTER the label is committed.

Protocol invariants (enforced in code):
  - Classifier severity / rule_ids live in sealed/ and are never loaded
    into the presentation path before a label is appended.
  - No undo. No re-label. Duplicate case ids are refused.
  - Labels are append-only JSONL.

Usage:
  uv run python scripts/label.py prepare   # build queue + sealed verdicts
  uv run python scripts/label.py next      # show next blind case
  uv run python scripts/label.py commit COSMETIC|BEHAVIOURAL|UNCLEAR
  uv run python scripts/label.py status
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vernier.canonical._jcs import serialize_jcs
from vernier.report.escape import escape_untrusted
from vernier.report.json_report import diverging_region
from vernier.report.render import _fmt_event
from vernier.replay.redrive import redrive

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "testdata" / "corpus"
LABELS_DIR = ROOT / "testdata" / "labels"
QUEUE_PATH = LABELS_DIR / "queue.json"
LABELS_PATH = LABELS_DIR / "labels.jsonl"
SEALED_DIR = LABELS_DIR / "sealed"
BLIND_DIR = LABELS_DIR / "blind"
STATE_PATH = LABELS_DIR / "session.json"
SEVERITIES_LOW_PLUS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
VALID_LABELS = ("COSMETIC", "BEHAVIOURAL", "UNCLEAR")
# Tokens that must never appear in blind presentation output.
_VERDICT_LEAK_KEYS = ("severity", "rule_ids", "rule_id")


def _case_id(run_id: str, candidate_model: str) -> str:
    raw = f"{run_id}::{candidate_model}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _labelled_ids() -> set[str]:
    if not LABELS_PATH.is_file():
        return set()
    ids: set[str] = set()
    for line in LABELS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ids.add(json.loads(line)["case_id"])
    return ids


def _load_queue() -> list[dict[str, Any]]:
    if not QUEUE_PATH.is_file():
        raise SystemExit("queue missing — run: uv run python scripts/label.py prepare")
    return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"pending_case_id": None, "revealed": False}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _assert_blind_text(text: str) -> None:
    """Refuse to emit presentation text that looks like a classifier verdict."""
    lower = text.lower()
    for key in _VERDICT_LEAK_KEYS:
        if key in lower:
            raise RuntimeError(
                f"blind presentation would leak classifier field {key!r}; refused"
            )
    # Severity enum tokens as standalone words are also forbidden.
    for token in ("none", "low", "medium", "high", "critical"):
        # Allow these only if not as verdict-looking lines; hard-ban HIGH/LOW etc.
        # when appearing as `severity=` or bare severity words after a known prefix.
        if f"severity={token}" in lower or f"severity: {token}" in lower:
            raise RuntimeError(f"blind presentation would leak severity={token}")
    if "d0" in lower and any(
        f"d{i:02d}" in lower for i in range(1, 13)
    ):
        # Rule ids like D05 in blind output are contamination.
        raise RuntimeError("blind presentation would leak rule id tokens")


def cmd_prepare(csv_path: Path) -> int:
    """Build blind queue + sealed verdicts from KT-1 CSV. Never prints verdicts."""
    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    SEALED_DIR.mkdir(parents=True, exist_ok=True)
    BLIND_DIR.mkdir(parents=True, exist_ok=True)
    if not csv_path.is_file():
        print(f"missing CSV: {csv_path}", file=sys.stderr)
        return 2

    queue: list[dict[str, Any]] = []
    with csv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            severity = (row.get("severity") or "").strip()
            if severity not in SEVERITIES_LOW_PLUS:
                continue
            run_id = row["run_id"]
            candidate = row["candidate_model"]
            case_id = _case_id(run_id, candidate)
            sealed = {
                "case_id": case_id,
                "run_id": run_id,
                "baseline_model": row.get("baseline_model", ""),
                "candidate_model": candidate,
                "severity": severity,
                "rule_ids": [
                    r for r in (row.get("rule_ids") or "").split("|") if r
                ],
            }
            sealed_path = SEALED_DIR / f"{case_id}.json"
            sealed_path.write_bytes(serialize_jcs(sealed) + b"\n")
            queue.append(
                {
                    "case_id": case_id,
                    "run_id": run_id,
                    "baseline_model": row.get("baseline_model", ""),
                    "candidate_model": candidate,
                }
            )

    QUEUE_PATH.write_text(json.dumps(queue, indent=2) + "\n", encoding="utf-8")
    _save_state({"pending_case_id": None, "revealed": False})
    # Intentionally do not print severity distribution — that contaminates.
    print(f"prepared {len(queue)} LOW+ cases into {QUEUE_PATH}")
    print(f"sealed verdicts under {SEALED_DIR}/ (not shown until commit)")
    return 0


def _stage_source(run_id: str) -> Path:
    manifest = json.loads((CORPUS / "MANIFEST.json").read_text(encoding="utf-8"))
    entry = next(e for e in manifest["runs"] if e["run_id"] == run_id)
    src = CORPUS / entry["path"]
    staging = LABELS_DIR / "_staging" / "runs"
    staging.mkdir(parents=True, exist_ok=True)
    dest = staging / run_id
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    mpath = dest / "manifest.json"
    m = json.loads(mpath.read_text(encoding="utf-8"))
    m["cwd"] = str(ROOT)
    mpath.write_bytes(serialize_jcs(m) + b"\n")
    return staging


def _traj_outline(traj: list[Any]) -> list[str]:
    lines: list[str] = []
    for i, ev in enumerate(traj):
        lines.append(f"  [{i}] {_fmt_event(traj, i)}")
    return lines


def build_blind_text(run_id: str, candidate_model: str) -> str:
    """Re-drive and render alignment WITHOUT severity or rule ids."""
    runs_dir = _stage_source(run_id)
    source_dir = runs_dir / run_id
    result = redrive(
        source_dir,
        out_dir=LABELS_DIR / "_staging" / "diff-runs",
        mode="live_diff",
        model=candidate_model,
    )
    # Intentionally ignore result.verdict — never render it on this path.
    pairs = result.pairs
    traj_a = result.traj_a
    traj_b = result.traj_b
    region = diverging_region(pairs)
    lines = [
        "=== BLIND CASE (classifier verdict HIDDEN) ===",
        f"case run_id={escape_untrusted(run_id)}",
        f"candidate_model={escape_untrusted(candidate_model)}",
        f"alignment_length={len(pairs)}",
        "",
        "Baseline trajectory outline:",
        *_traj_outline(traj_a),
        "",
        "Candidate trajectory outline:",
        *_traj_outline(traj_b),
        "",
        "Diverging region (trajectory symbols only):",
        f"{'rel':<12} {'A':<36} {'B':<36}",
        "-" * 84,
    ]
    if not region:
        lines.append("(no non-Matched pairs in alignment)")
    else:
        for pair in region:
            lines.append(
                f"{pair.relation.value:<12} "
                f"{_fmt_event(traj_a, pair.a_idx):<36} "
                f"{_fmt_event(traj_b, pair.b_idx):<36}"
            )
    lines.append("")
    lines.append("Label with: uv run python scripts/label.py commit <LABEL>")
    lines.append("Labels: COSMETIC | BEHAVIOURAL | UNCLEAR")
    text = "\n".join(lines) + "\n"
    _assert_blind_text(text)
    return text


def _blind_path(case_id: str) -> Path:
    return BLIND_DIR / f"{case_id}.txt"


def _present_case(case: dict[str, Any]) -> str:
    case_id = case["case_id"]
    path = _blind_path(case_id)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        _assert_blind_text(text)
        return text
    text = build_blind_text(case["run_id"], case["candidate_model"])
    path.write_text(text, encoding="utf-8")
    return text


def cmd_next() -> int:
    queue = _load_queue()
    done = _labelled_ids()
    state = _load_state()
    if state.get("pending_case_id") and not state.get("revealed"):
        pending = next(c for c in queue if c["case_id"] == state["pending_case_id"])
        print(_present_case(pending), end="")
        print(f"(resumed pending case_id={pending['case_id']})")
        return 0

    remaining = [c for c in queue if c["case_id"] not in done]
    if not remaining:
        print("all cases labelled")
        return 0
    case = remaining[0]
    print(
        f"progress: {len(done)}/{len(queue)} labelled; "
        f"{len(remaining)} remaining"
    )
    print(_present_case(case), end="")
    _save_state({"pending_case_id": case["case_id"], "revealed": False})
    return 0


def load_sealed_after_commit(case_id: str) -> dict[str, Any]:
    """Open sealed verdict. Call only after the label JSONL append succeeded."""
    sealed_path = SEALED_DIR / f"{case_id}.json"
    return json.loads(sealed_path.read_text(encoding="utf-8"))


def cmd_commit(label: str) -> int:
    label = label.strip().upper()
    if label not in VALID_LABELS:
        print(f"invalid label {label!r}; expected {VALID_LABELS}", file=sys.stderr)
        return 2
    state = _load_state()
    case_id = state.get("pending_case_id")
    if not case_id:
        print("no pending case — run: label.py next", file=sys.stderr)
        return 2
    if state.get("revealed"):
        print("pending case already revealed; run next", file=sys.stderr)
        return 2
    if case_id in _labelled_ids():
        print(f"refusing re-label of {case_id}", file=sys.stderr)
        return 2

    queue = _load_queue()
    case = next(c for c in queue if c["case_id"] == case_id)
    record = {
        "case_id": case_id,
        "run_id": case["run_id"],
        "candidate_model": case["candidate_model"],
        "baseline_model": case.get("baseline_model", ""),
        "label": label,
        "labelled_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    # Append-only commit FIRST — before any sealed read.
    with LABELS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())

    sealed = load_sealed_after_commit(case_id)
    print("LABEL COMMITTED (append-only; no undo).")
    print(f"  label={label}")
    print("--- classifier verdict (revealed after commit) ---")
    print(f"  severity={sealed['severity']}")
    print(f"  rule_ids={sealed['rule_ids']}")
    _save_state({"pending_case_id": None, "revealed": True})
    remaining = len(_load_queue()) - len(_labelled_ids())
    print(f"remaining={remaining}")
    return 0


def cmd_status() -> int:
    if not QUEUE_PATH.is_file():
        print("not prepared")
        return 0
    queue = _load_queue()
    done = _labelled_ids()
    print(f"total_low_plus={len(queue)}")
    print(f"labelled={len(done)}")
    print(f"remaining={len(queue) - len(done)}")
    print(f"labels_path={LABELS_PATH}")
    state = _load_state()
    print(f"pending_case_id={state.get('pending_case_id')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_prep = sub.add_parser("prepare", help="Build blind queue from KT-1 CSV")
    p_prep.add_argument(
        "--csv",
        type=Path,
        default=CORPUS / "kt1_results.csv",
    )

    sub.add_parser("next", help="Show next blind case")
    p_commit = sub.add_parser("commit", help="Commit label for pending case")
    p_commit.add_argument("label", choices=VALID_LABELS)
    sub.add_parser("status", help="Labelling progress")

    args = parser.parse_args(argv)
    if args.cmd == "prepare":
        return cmd_prepare(args.csv)
    if args.cmd == "next":
        return cmd_next()
    if args.cmd == "commit":
        return cmd_commit(args.label)
    if args.cmd == "status":
        return cmd_status()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
