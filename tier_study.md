# Local small-model tier study (NOT KT-1)

This is a **local tier study**, not the hosted KT-1 upgrade pair.

- Baseline (recorded corpus): `qwen3:4b-instruct`
- Endpoint: `http://localhost:11434/v1`
- temperature=`0`, stream=`false`, request_options=`null` (no num_ctx on either leg)
- Corpus: 30 runs under `testdata/corpus/`
- T1 null seeds: 42, 43, 44
- T2/T3 seed: `42`

Divergence = completed run with severity ≠ NONE. **HARNESS_FAULT is reported separately and never counted as a divergence.**

## Headline net rates

NET = pair divergence rate − T1 null rate. Headline figures are **net** (except T1, which *is* the null rate).

| Pair | Candidate | Raw div | N scored | Raw rate | Wilson 95% CI | NET vs T1 | HARNESS_FAULT |
| --- | --- | ---: | ---: | ---: | --- | ---: | ---: |
| T1 | `qwen3:4b-instruct` | 6 | 90 | 0.0667 | [0.0309, 0.1379] | 0.0667 (null) | 0 |
| T2 | `llama3.2:3b` | 30 | 30 | 1.0000 | [0.8865, 1.0000] | +0.9333 | 0 |
| T3 | `llama3.1:8b` | 30 | 30 | 1.0000 | [0.8865, 1.0000] | +0.9333 | 0 |

T1 null rate (denominator for NET): **6/90 = 0.0667**

## T1 — null (same model)

- candidate: `qwen3:4b-instruct`
- seeds: 42, 43, 44
- comparisons: 90 (scored=90, HARNESS_FAULT=0, ops_error=0)
- raw divergence: 6/90 = 0.0667
- Wilson 95% CI: [0.0309, 0.1379]
- **null rate (finding):** 0.0667
- HARNESS_FAULT count: **0** (not counted as divergences)

### Rule-ID histogram (D01–D12)

- D01: 0
- D02: 0
- D03: 0
- D04: 6
- D05: 0
- D06: 0
- D07: 0
- D08: 0
- D09: 0
- D10: 0
- D11: 0
- D12: 0

### Per-agent-domain divergence

| Domain | Diverged | Scored | Rate |
| --- | ---: | ---: | ---: |
| booking | 0 | 15 | 0.0000 |
| coding | 0 | 15 | 0.0000 |
| data | 0 | 15 | 0.0000 |
| research | 6 | 15 | 0.4000 |
| router | 0 | 15 | 0.0000 |
| support | 0 | 15 | 0.0000 |

## T2 — tier down, cross-family

- candidate: `llama3.2:3b`
- seeds: 42
- comparisons: 30 (scored=30, HARNESS_FAULT=0, ops_error=0)
- raw divergence: 30/30 = 1.0000
- Wilson 95% CI: [0.8865, 1.0000]
- **NET vs T1:** +0.9333
- HARNESS_FAULT count: **0** (not counted as divergences)

### Rule-ID histogram (D01–D12)

- D01: 0
- D02: 0
- D03: 0
- D04: 26
- D05: 0
- D06: 6
- D07: 14
- D08: 7
- D09: 15
- D10: 0
- D11: 0
- D12: 0

### Per-agent-domain divergence

| Domain | Diverged | Scored | Rate |
| --- | ---: | ---: | ---: |
| booking | 5 | 5 | 1.0000 |
| coding | 5 | 5 | 1.0000 |
| data | 5 | 5 | 1.0000 |
| research | 5 | 5 | 1.0000 |
| router | 5 | 5 | 1.0000 |
| support | 5 | 5 | 1.0000 |

## T3 — tier up, cross-family

- candidate: `llama3.1:8b`
- seeds: 42
- comparisons: 30 (scored=30, HARNESS_FAULT=0, ops_error=0)
- raw divergence: 30/30 = 1.0000
- Wilson 95% CI: [0.8865, 1.0000]
- **NET vs T1:** +0.9333
- HARNESS_FAULT count: **0** (not counted as divergences)

### Rule-ID histogram (D01–D12)

- D01: 0
- D02: 0
- D03: 0
- D04: 30
- D05: 3
- D06: 7
- D07: 9
- D08: 4
- D09: 10
- D10: 0
- D11: 0
- D12: 0

### Per-agent-domain divergence

| Domain | Diverged | Scored | Rate |
| --- | ---: | ---: | ---: |
| booking | 5 | 5 | 1.0000 |
| coding | 5 | 5 | 1.0000 |
| data | 5 | 5 | 1.0000 |
| research | 5 | 5 | 1.0000 |
| router | 5 | 5 | 1.0000 |
| support | 5 | 5 | 1.0000 |

## Method notes

- Live-diff via `vern diff --model <candidate>`; baseline events are the recorded corpus trajectory.
- Request options matched: neither leg sends `options.num_ctx` (recorded as `null`).
- Harness aborts with HARNESS_FAULT if control fields (temperature/stream/options/tools) differ from baseline before the model is called.
- HARNESS_FAULT rows are excluded from divergence numerators and denominators (N scored = completed only).
- **T3 first pass** had 10/30 transport `ZERO_TOOL_COLLAPSE` faults on research/data (see `t3_faults.md`). Re-run after warming `llama3.1:8b`: **30/30 scored, 0 faults** — table above is the re-run.
- No classifier tuning was performed for this study.
- Null floor / 50× shape: `null_floor.md`, `null_floor_replay50.json` (~49/1).
- Hosted KT-1 runner (not executed): `scripts/run_kt1.py` + `kt1_config.json` placeholders.
