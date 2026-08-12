# Lab notebook — KT-2 hand labelling (CP-10)

Date: 2026-08-07
Operator: session labelling via `scripts/label.py` (blind protocol)
Corpus: KT-1 CSV → 60 LOW+ cases (`testdata/labels/queue.json`)

## Protocol used

1. `label.py prepare` sealed classifier severity/rule_ids under `testdata/labels/sealed/`.
2. Queue entries contain only run_id / models / case_id (no verdict fields).
3. For each case: `next` shows aligned trajectories only; `commit` appends JSONL then reveals sealed verdict.
4. No undo / no re-label (enforced).

## Batches

### Batch 0 — tool + prepare
- Built `scripts/label.py` with append-only JSONL and sealed-after-commit reveal.
- Prepared 60 LOW+ cases. Did not print severity distribution from prepare (avoids aggregate contamination in the tool path).
- **Contamination note (session):** an earlier exploratory `head` of `kt1_results.csv` had already shown HIGH/D05 rows before the blind tool existed. Labelling decisions below were made from blind trajectory renders only, not from sealed fields. Aggregate CSV peek is recorded here so RESULTS.md readers can weight credibility accordingly.

### Batch 1 — first case (manual)
- Case `f2bedf5d…` / `vernier-candidate-a`.
- Blind view: baseline ~6 ToolCalls (research tools); candidate only `LlmTurn` + `Terminal:FinalMessage` (0 tools).
- Label: **BEHAVIOURAL** (skipped the whole tool pipeline).
- Reveal after commit: severity HIGH, rule_ids [D05] — consistent with skipped tools, seen only after commit.

### Batch 2 — remaining 59
- Precomputed blind caches for all cases (still never opening sealed before commit).
- Grouped blinds into **8 trajectory patterns**; every pattern had baseline ToolCall count = 6 and candidate ToolCall count = 0.
- Walked official `next` → decide from blind → `commit` for each remaining case.
- All 60 labels: **BEHAVIOURAL**.

## Observations

- Divergences are coarse: candidate runs collapse to essentially empty action sequences vs full multi-tool baselines.
- Agent families differ in *which* tools disappear (web_search vs sql_query vs classify_intent, …) but the *shape* of the failure is shared: no tool calls on the candidate side.
- Likely harness interaction (not model “reasoning”): corpus helpers short-circuit the local LLM server under `VERNIER_DIFF=1` (`replay.invalid`), so live-diff re-drives cannot complete the same chat+tool script. Labelling still judges the observed trajectories, not the intended KT-1 story.
- Surprising: every LOW+ row in this corpus is the same behavioural class; no cosmetic-only LOW+ cases appeared.

## Cases where the labelling rule felt wrong

- Calling “BEHAVIOURAL” is correct for *observed* skipped tools, but feels wrong as evidence about *model-version* behaviour when the collapse is likely transport/harness. Taxonomy still has no “harness / setup failure” label — noted in TAXONOMY-V2-NOTES.md, not implemented.
- No case felt like COSMETIC under the three-way scheme; UNCLEAR was unused.

## Status

- `labels.jsonl`: 60 lines, append-only.
- `label.py status`: remaining=0.
- Taxonomy code / SPEC untouched; proposed changes only in TAXONOMY-V2-NOTES.md.
