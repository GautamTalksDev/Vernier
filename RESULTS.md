# RESULTS.md — Vernier kill-test evaluation

**Date:** 2026-08-07 (CP-11.5 harness repair)  
**Pre-registration:** thresholds unchanged from `README.md` (CP-0).

Prior write-up: [`RESULTS-v0-RETRACTED.md`](RESULTS-v0-RETRACTED.md)
(harness bug; do not cite).

## Kill-test status (these words are deliberate)

| Kill test | Status |
| --- | --- |
| **KT-1** | **UNEVALUATED** |
| **KT-2** | **UNEVALUATED** |
| **KT-3** | **PASS** |
| **KT-3b** | **PASS** (added CP-11.5) |
| **KT-4** | not due |

### One sentence each

- **KT-1: UNEVALUATED** — the v0 “100% behavioural” figure was a live-diff
  harness collapse (`replay.invalid`), not a model-upgrade experiment; no
  real provider pair has been run and the $100 budget remains unspent
  pending API keys.
- **KT-2: UNEVALUATED** — the 60 hand labels were 60 copies of one
  degenerate zero-tool failure mode; they do not evidence the 12-rule
  taxonomy.
- **KT-3: PASS** — full-stub re-drive of the 10 verify fixtures and 30/30
  corpus runs yields severity NONE (substrate holds).
- **KT-3b: PASS** — unchanged-variable live-diff returns severity NONE on
  verify fixtures and corpus research runs after the harness repair.

---

## 1. What broke (root cause)

Ordered elimination for one corpus research run under `vern diff`:

| Hypothesis | Result |
| --- | --- |
| **(a)** LLM leg unusable → agent exits before tools | **CONFIRMED.** `examples/_corpus_llm.start_baseline_server()` treated `VERNIER_DIFF=1` like full-stub and returned `http://replay.invalid` without starting a listener. Live HTTP → `httpx.ConnectError: Name or service not known`. Candidate events: one `LlmRequest` to `replay.invalid`, then process death. Zero `ToolCall`s. |
| (b) Tool-schema injection lost in diff mode | Ruled out for this corpus: tools are in-process `@vern.tool` wrappers, not LLM-discovered schemas. |
| (c) Record-lookup over-specified for tools | Not reached — agent never called tools. |
| (d) Stub-vs-live boundary inverted | Related to (a): diff mode incorrectly reused the replay placeholder URL. |

**Fix:** start the local stub (or `VERNIER_LLM_BASE_URL`) under live-diff;
only `VERNIER_REPLAY=1` may use `replay.invalid`.

**Guard:** `apply_harness_guard` — if baseline has tools, candidate has
zero, and the agent crashed or shows transport failure markers, emit
`harness_fault=ZERO_TOOL_COLLAPSE` with D-rules suppressed and CLI exit
**2**. Intentional zero-tool skips (exit 0, no transport failure) still
classify as D05.

---

## 2. Method (current instrument)

| Layer | Version |
| --- | --- |
| Canon | `vernier-canon-v1` |
| Record | `vernier-record-v1` |
| Align | `costs-v1` |
| Taxonomy | `divergence-v1` + harness_fault (not a D-rule) |
| Ignore-list | `ignore-v1` |

Corpus: 30 runs, 6 shapes, baseline `vernier-baseline-local-v1` (local stub).

---

## 3. KT-1 — still not executed

Required for a scored KT-1 (see `scripts/run_kt1.py`):

```bash
export VERNIER_KT1_LIVE=1
export OPENAI_API_KEY=…          # or VERNIER_LLM_API_KEY
export VERNIER_LLM_BASE_URL=https://api.openai.com
export VERNIER_KT1_CANDIDATES=gpt-4o,gpt-4o-mini,claude-…  # ≥3 real ids
uv run python scripts/run_kt1.py  # $100 hard cap; prints running cost
```

This machine has **no provider API key**. LIVE mode refuses stub
candidates (`vernier-candidate-*`, `demo-override`). Until that run
completes, **do not claim PASS or FAIL against the 20% bar.**

If the clean re-run lands **under 20%**, the project stops and publishes
the negative result as pre-registered.

---

## 4. KT-2 — void until clean divergences exist

No new hand labels. Prior `testdata/labels/labels.jsonl` describes the
retracted failure mode only. Re-label after a clean KT-1 produces
heterogeneous LOW+ divergences.

---

## 5. KT-3 / KT-3b

- **KT-3:** `scripts/verify_corpus.py` → 30/30 NONE; `testdata/verify` →
  10/10 NONE under full stub.
- **KT-3b:** `tests/test_kt3b_harness.py` — live-diff with no overrides →
  NONE; corpus research live-diff no longer dials `replay.invalid`.

---

## 6. Findings (limited)

- Full-stub verify is solid.
- Live-diff is only meaningful when the LLM transport is real.
- A 100% “behavioural” rate with identical zero-tool collapses is a
  broken-instrument signal, not hypothesis confirmation.

---

## 7. Limitations

Unchanged from pre-registration honesty: synthetic corpus, small N,
provider nondeterminism at temperature 0 (Thinking Machines batch
invariance), single labeller when KT-2 is eventually re-run, ignore-list
judgement. **Additional:** v0 RESULTS must not be cited; this file
supersedes it.
