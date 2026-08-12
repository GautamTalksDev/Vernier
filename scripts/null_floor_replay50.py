#!/usr/bin/env python3
"""50× replay of exact baseline bodies for T1 divergences (null floor shape)."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import urllib.request
from collections import Counter
from pathlib import Path

from vernier.record.store import RecordStore, load_run

ROOT = Path(__file__).resolve().parents[1]
TIER_CSV = ROOT / "tier_study.csv"
MANIFEST = ROOT / "testdata" / "corpus" / "MANIFEST.json"
OUT_JSON = ROOT / "null_floor_replay50.json"
OLLAMA = "http://localhost:11434"
N_TRIALS = 50


def _arg_fingerprint(resp: dict) -> tuple[str, list[dict[str, object]]]:
    try:
        tcs = resp["choices"][0]["message"].get("tool_calls") or []
    except (KeyError, IndexError, TypeError):
        tcs = []
    norm: list[dict[str, object]] = []
    for tc in tcs:
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                pass
        norm.append({"name": fn.get("name"), "arguments": args})
    blob = json.dumps(norm, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:16], norm


def _llm_bodies(run_dir: Path) -> list[bytes]:
    frozen = load_run(run_dir)
    reader = RecordStore(run_dir, frozen.run_id)
    out: list[bytes] = []
    for env in reader.read_events():
        if env["kind"] == "LlmRequest":
            p = reader.read_payload(env)
            ref = p.get("body_blob_ref")
            if ref:
                out.append(reader.read_blob(ref))
    return out


def _llm_responses(run_dir: Path) -> list[dict]:
    frozen = load_run(run_dir)
    reader = RecordStore(run_dir, frozen.run_id)
    out: list[dict] = []
    for env in reader.read_events():
        if env["kind"] == "LlmResponse":
            p = reader.read_payload(env)
            ref = p.get("body_blob_ref")
            if ref:
                out.append(json.loads(reader.read_blob(ref)))
    return out


def _first_d04_turn(baseline_dir: Path, candidate_dir: Path) -> int | None:
    ra, rb = _llm_responses(baseline_dir), _llm_responses(candidate_dir)
    for i, (a, b) in enumerate(zip(ra, rb)):
        _, na = _arg_fingerprint(a)
        _, nb = _arg_fingerprint(b)
        if na != nb:
            return i
    return None


def _replay(body: bytes) -> tuple[str, list[dict[str, object]]]:
    req = urllib.request.Request(
        OLLAMA + "/v1/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer ollama",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        data = json.loads(resp.read().decode())
    return _arg_fingerprint(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=TIER_CSV)
    parser.add_argument("--staging", type=Path, default=ROOT / "testdata" / "corpus" / "_tier_study_staging")
    parser.add_argument("--trials", type=int, default=N_TRIALS)
    args = parser.parse_args()

    divergences = [
        r
        for r in csv.DictReader(args.csv.open(encoding="utf-8"))
        if r.get("pair_id") == "T1"
        and r.get("status") == "completed"
        and (r.get("severity") or "") not in {"", "NONE"}
    ]
    if not divergences:
        print("no T1 divergences in csv", flush=True)
        return 0

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    by_id = {e["run_id"]: e for e in manifest["runs"]}
    diff_out = args.staging / "diff-runs"
    runs_dir = args.staging / "runs"

    results = []
    for row in divergences:
        run_id = row["run_id"]
        label = row["corpus_label"]
        seed = row["seed"]
        entry = by_id.get(run_id)
        if not entry:
            continue
        baseline_dir = runs_dir / run_id
        if not baseline_dir.is_dir():
            baseline_dir = ROOT / "testdata" / "corpus" / entry["path"]
        cand_id = (row.get("candidate_run_id") or "").strip()
        candidate_dir = diff_out / cand_id if cand_id else None
        bodies = _llm_bodies(baseline_dir)
        turn = 0
        if candidate_dir is not None and candidate_dir.is_dir():
            found = _first_d04_turn(baseline_dir, candidate_dir)
            if found is not None:
                turn = found
        if turn >= len(bodies):
            turn = max(0, len(bodies) - 1)
        body = bodies[turn]
        obj = json.loads(body)
        if str(obj.get("seed")) != seed:
            obj["seed"] = int(seed)
            body = json.dumps(obj, separators=(",", ":")).encode()

        print(f"{label} seed={seed} turn={turn} n_trials={args.trials} …", flush=True)
        counts: Counter[str] = Counter()
        examples: dict[str, list[dict[str, object]]] = {}
        for i in range(args.trials):
            fp, norm = _replay(body)
            counts[fp] += 1
            examples.setdefault(fp, norm)
        dist = {fp: counts[fp] for fp in sorted(counts, key=counts.get, reverse=True)}
        results.append(
            {
                "label": label,
                "run_id": run_id,
                "seed": seed,
                "turn": turn,
                "n_trials": args.trials,
                "distribution": dist,
                "examples": {fp: examples[fp] for fp in dist},
            }
        )
        print(f"  distribution: {dist}", flush=True)

    OUT_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"wrote {OUT_JSON} ({len(results)} cases)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
