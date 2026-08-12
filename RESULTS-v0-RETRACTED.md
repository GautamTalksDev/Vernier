# RESULTS-v0-RETRACTED.md

> **RETRACTED (2026-08-07).** This document reported KT-1/KT-2 PASS on a
> broken live-diff harness. Corpus agents short-circuited under
> `VERNIER_DIFF=1` to `http://replay.invalid`, every candidate trajectory
> collapsed to zero tools, and D05 was misread as behavioural model-upgrade
> pain. **No real model pair was ever run; the $100 budget was unspent.**
> Kept in-repo as a public correction. Authoritative status: `RESULTS.md`.

---

# RESULTS.md — Vernier kill-test evaluation (divergence-v1) [RETRACTED]

**Date:** 2026-08-07  
**Pre-registration:** thresholds are exactly those in `README.md` (CP-0). They were
not adjusted. Rules were not refitted to this data; reported numbers are
divergence-v1 as shipped.

**Kill-test one-liners**

- **KT-1: PASS** — behavioural divergence under a single-variable model-id change
  in **30/30** corpus runs (100%, 95% Wilson CI **[88.7%, 100%]**), above the **20%** bar.
- **KT-2: PASS** — hand-label agreement **60/60 (100%)** (≥ **85%**) and BEHAVIOURAL
  recall **100%** / precision **100%** (catch-all bar **100%**).
- **KT-3: PASS** — full-stub re-drive matched at severity NONE on **30/30** corpus
  runs and on the **10** `testdata/verify` fixtures (≥8 projected steps each).
- **KT-4:** not due (three months post-launch); not scored here.

**Continuation (honest, not a moved bar):** the numerical bars are all PASS, but
KT-1’s “pain” is almost certainly a **live-diff harness collapse**, not evidence
that production agents flip behaviour on model upgrades. Do not cite the 100%
rate as product validation. Fix the corpus live-diff path, re-run KT-1 on a
clean single-variable design, and only then treat KT-1 as informative. Taxonomy
changes belong in v2 (`TAXONOMY-V2-NOTES.md`); they are **not** applied here.

---

## 1. Method

### Corpus

| Item | Value |
| --- | --- |
| Manifest | `testdata/corpus/MANIFEST.json` (`corpus-v1`) |
| Runs | **30** recorded baseline runs |
| Agent shapes | 6 × 5 inputs: research, data, support, coding, booking, router |
| Depth | each run ≥8 projected trajectory steps, ≥4 tools |
| Baseline model | `vernier-baseline-local-v1` (local deterministic HTTP stub) |
| Candidates | `vernier-candidate-a`, `vernier-candidate-b` (model-id override only) |
| Comparisons | 30 × 2 = **60** `vern diff` rows in `kt1_results.csv` |

Agents are scripted multi-step examples (`examples/*_agent.py`), not production
traffic. LLM replies during **capture** come from the local stub; during
**live-diff** the same helpers refuse to bind a real server when
`VERNIER_DIFF=1` and point at `http://replay.invalid`
(`examples/_corpus_llm.py`).

### Versions under test

| Layer | Version |
| --- | --- |
| Package | `vernier` `0.0.1a1` |
| Canon | `vernier-canon-v1` |
| Record | `vernier-record-v1` |
| Align costs | `costs-v1` (Needleman–Wunsch maximization) |
| Taxonomy | `divergence-v1` (`SPEC-DIVERGENCE.md`) |
| Ignore-list | `ignore-v1` (`src/vernier/classify/ignore-v1.toml`) |

### Ignore-list contents (`ignore-v1`)

- JSON pointers stripped: `/request_id`, `/trace_id`, `/nonce`,
  `/headers/request_id`, `/headers/trace_id`, `/headers/x-request-id`
- `strip_uuid_values = true`
- `strip_iso_timestamps = true`

### Labelling (KT-2)

- Tool: `scripts/label.py` (blind `next` / append-only `commit`; sealed
  severity/rule_ids revealed only after label write).
- Labels: `COSMETIC` \| `BEHAVIOURAL` \| `UNCLEAR` in
  `testdata/labels/labels.jsonl`.
- Scope: every LOW+ row from KT-1 (here: all 60).
- Lab notebook: `LAB-NOTEBOOK.md`.

### Classifier → label-class mapping (fixed, not fitted)

Used only to score KT-2 agreement; not used to change rules.

- **BEHAVIOURAL** if any of D01–D09 fired.
- **COSMETIC** if only D10–D12 fired.
- **NONE** if no rules fired.

(D05 is HIGH “skipped tool” and maps to BEHAVIOURAL under this mapping.)

### Spend

KT-1 budget cap: **$100**. Driver: `scripts/run_kt1.py`. This run used the
default local stub / dry path (`VERNIER_KT1_LIVE` unset), so billed spend was
**$0.00**. No paid provider calls were made.

---

## 2. KT-1 — behavioural divergence rate

**Pre-registered bar (README):** if behavioural divergence appears in
**fewer than 20%** of **30+** recorded runs under a single-variable model
change → project stops.

**Unit of analysis:** a corpus **run** counts as behavioural if **any**
candidate comparison for that run was hand-labelled `BEHAVIOURAL`.

| Metric | Value |
| --- | ---: |
| Runs | 30 |
| Runs with behavioural divergence | 30 |
| Rate | **100%** |
| 95% Wilson CI | **[88.7%, 100%]** |
| Diff-level (60 comparisons) | 60/60 BEHAVIOURAL; classifier all `HIGH` / `D05` |

Lower CI bound (88.7%) is still above 20%.

**KT-1: PASS** against the 20% bar.

**Validity caveat (does not change the PASS/FAIL line):** the independent
variable was not a clean model-version swap against a live provider. Candidate
re-drives under `VERNIER_DIFF=1` hit a non-listening LLM URL; trajectories
collapsed to roughly `LlmTurn` + `Terminal` with **zero** tool calls. The
measured 100% is therefore best read as “live-diff corpus path always
diverges,” not “upgrading the model string changes agent behaviour 100% of
the time.” See §5–§6. We still report the pre-registered numerical result
without moving the threshold.

---

## 3. KT-2 — agreement with hand labels

**Pre-registered bar (README):** agree on **≥85%** of divergences **and**
catch **100%** of behavioural divergences (else stop).

| Metric | Value |
| --- | ---: |
| Divergences labelled | 60 |
| Overall agreement (mapped class vs human) | **60/60 = 100%** |
| Human label mix | BEHAVIOURAL 60, COSMETIC 0, UNCLEAR 0 |
| Per rule ID | **D05 only** (n=60): agreement 60/60 |
| BEHAVIOURAL precision | **100%** (TP=60, FP=0) |
| BEHAVIOURAL recall | **100%** (TP=60, FN=0) |

**KT-2: PASS** against the 85% agreement and 100% behavioural-recall bars.

**Caveat:** with only D05 / only BEHAVIOURAL in this corpus, KT-2 does not
stress cosmetic rules (D11/D12) or mixed verdicts. The PASS is real for the
data on hand; it is not a broad proof of taxonomy quality. No rules were
refit to improve these numbers.

---

## 4. KT-3 — full-stub verify substrate

**Pre-registered bar (README):** full-stub re-drive of **10** recorded runs of
**≥8** steps each must produce a **100%** action match (else substrate broken).

| Suite | Result |
| --- | --- |
| `testdata/verify` (10 fixtures) | all `vern verify` → severity **NONE** (pytest `tests/test_verify.py`) |
| Real KT-1 corpus (30 runs) | `scripts/verify_corpus.py` → **30/30** severity **NONE** |

**KT-3: PASS.**

---

## 5. Findings (the paper section)

### What fired

- **Only D05** (HIGH — tool call in A absent in B) fired across all 60
  candidate diffs. No D01–D04, D06–D12 in the KT-1 CSV.
- Severity was uniformly **HIGH**; ignore-list applications did not erase the
  action-sequence gap (there were no tools on the candidate side to reconcile).

### What diverged

- **Both** candidate model ids diverged on **every** run the same way: baseline
  kept a full ~6-tool pipeline; candidate kept essentially no tools.
- No model-pair contrast: `vernier-candidate-a` and `vernier-candidate-b` are
  interchangeable under this harness failure mode.

### Which agent shapes were fragile

- Under the measured protocol, **all six shapes** were equally “fragile”
  (5/5 runs each). That uniformity is itself a smell: true model-sensitivity
  usually varies by tool graph; identical total collapse across research /
  data / support / coding / booking / router points at shared infrastructure
  (`start_baseline_server` + live-diff), not shape-specific planning.

### What this means for the thesis

Vernier’s substrate (capture → record → align → classify → verify) works:
KT-3 holds, and when trajectories actually differ in tools, D05 and human
BEHAVIOURAL labels agree.

Vernier’s **product kill question**—“do everyday model upgrades create
behavioural pain often enough to care?”—is **not answered** by this KT-1
number. The experiment as executed measured “does breaking the live LLM
endpoint abort the scripted agent before tools?” Answer: yes, always.

That is still a useful systems finding: **live-diff evaluation is only as
good as the LLM transport under `VERNIER_DIFF`.** Until corpus agents serve
(or stub) chat correctly in diff mode, KT-1 will not speak to model drift.

### Future work (v2 — not scored here)

See `TAXONOMY-V2-NOTES.md`: harness/setup annotations, action-sequence
collapse detailing, cosmetic fixture pack. Implement after a clean KT-1
re-run; do not rewrite divergence-v1 post hoc and call it pre-registered.

---

## 6. Limitations

1. **Corpus is synthetic-ish.** Six hand-written agents with deterministic
   local stubs are not a sample of production agent workloads.
2. **N is small.** Thirty runs / sixty diffs; Wilson intervals are wide in
   principle (here the point estimate sits on the boundary of 100%).
3. **Providers are non-deterministic even at temperature 0.** Batch size and
   kernel reduction order can change greedy decoding under load; see Thinking
   Machines Lab, [*Defeating Nondeterminism in LLM Inference*](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/)
   (He et al.; batch-invariant kernels / `batch_invariant_ops`). This study
   did not use paid providers, but any follow-up with real APIs inherits that
   limit.
4. **Single labeller.** One person (this lab session) produced all ground-truth
   labels. There is no second annotator, no adjudication, and no inter-rater
   reliability. KT-2’s 100% agreement is agreement with **that one labeller**,
   not with a consensus truth. That is a real threat to validity, not a minor
   caveat.
5. **Ignore-list is a judgement call.** `ignore-v1` pointers and UUID/timestamp
   stripping encode author taste about which fields are “the same action.”
   Another lab would ship a different list and move some D04 boundary cases.
6. **KT-1 confound.** Model-id override was entangled with live-diff LLM
   unavailability (`replay.invalid`). Labels judge observed trajectories
   honestly; causal attribution to “model upgrade” does not follow.
7. **Blind labelling contamination note.** Aggregate CSV severity was peeked
   before the blind tool existed (`LAB-NOTEBOOK.md`). Per-case labels were
   still taken from blind trajectory renders; treat residual bias as possible.

---

## Appendix — raw pointers

| Artefact | Path |
| --- | --- |
| KT-1 CSV | `testdata/corpus/kt1_results.csv` |
| Labels | `testdata/labels/labels.jsonl` |
| Sealed verdicts | `testdata/labels/sealed/` |
| Lab notebook | `LAB-NOTEBOOK.md` |
| Taxonomy v2 notes (unimplemented) | `TAXONOMY-V2-NOTES.md` |
| Pre-registered bars | `README.md` § Kill Tests |
