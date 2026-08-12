# T3 harness faults (llama3.1:8b) — diagnosis

**Pair:** T2/T3 tier study, candidate `llama3.1:8b`, seed 42.  
**Symptom:** 10/30 runs → `ZERO_TOOL_COLLAPSE` HARNESS_FAULT on **all**
`research_*` and `data_*` (support/coding/booking/router completed normally).

## Classification: not (a), not (b), not (c)

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| **(a)** prompt+tools exceeded context | Turn-0 bodies ~1440–1460 bytes; 4 tools; Ollama runner context `-c 4096`. Re-diff of the same research_01 body against `llama3.1:8b` **succeeds** with tool_calls. | **Ruled out** |
| **(b)** tool_calls format the parser missed | No `LlmResponse` was recorded on any of the 10 faults. | **Ruled out** |
| **(c)** model returned prose instead of a tool call | Same — no response body at all. | **Ruled out** |

**Actual cause: transport failure during the live hop** (same defect class as
`replay.invalid` / unreachable LLM): the candidate never received a completion.

## Evidence from 3 fault runs

Candidates under `testdata/corpus/_tier_study_staging/diff-runs/`.

### 1. research_01 — `d29a7e5f203e4788b85c18b2129ffa1b`

**Request (full control envelope):**
```json
{
  "model": "llama3.1:8b",
  "temperature": 0.0,
  "seed": 42,
  "stream": false,
  "tool_choice": "auto",
  "options": null,
  "messages": [
    {"role": "system", "content": "You are a research agent. … Call tools…"},
    {"role": "user", "content": "Research the topic 'climate'. …"}
  ],
  "tools": ["web_search", "fetch_url", "extract_quotes", "write_summary"]
}
```
Body size: **1449** bytes.

**Raw response:** *none.*

**Event sequence:** `RunStart` → `LlmRequest` → **`CaptureGap{reason: transport_error}`** → `RunEnd`.

Wall clock: **33.96s** (model swap / load attempt after T2’s `llama3.2:3b`).
Agent exit: **2**. Tools: 4→0.

### 2. data_01 — `9ee02d2e5fd741af9c84dcc3810f762e`

Same pattern. Body **1459** bytes; tools
`sql_query` / `transform_rows` / `validate_schema` / `write_table`.  
`CaptureGap transport_error`. Wall **0.26s**. No response.

### 3. research_03 — `ab559de4a6224c58819b6db6c00f313c`

Same pattern. Body **1443** bytes. `CaptureGap transport_error`. Wall **0.30s**.

## Timing pattern (all 10)

| Runs | Wall | Interpretation |
| --- | ---: | --- |
| research_01 | ~34s | First T3 call: unload 3b / load 8b, then transport fail |
| research_02–05, data_01–05 | ~0.25–0.44s | Immediate fail while runner unhealthy |
| support_01 onward | 23–70s | Recovered; model serves tool_calls normally |

Live re-check (postmortem): `POST /v1/chat/completions` with tools to
`llama3.1:8b` returns proper OpenAI `tool_calls`. Re-diff of research_01
now scores **HIGH** with D04/D05/D06/D07/D09 — tools fire.

`CaptureGap.detail` historically lacked `error_type` (now added in
`transport.py`). Likely Ollama runner crash/OOM or connection reset during
the 3b→8b swap on this host — operational, not behavioural.

## Outcome class

Keep as **HARNESS_FAULT** (instrument / infrastructure), **not**:
- a divergence (no candidate trajectory to classify),
- “candidate refused tools” (c) — that requires a recorded completion with
  empty `tool_calls` / prose content.

Do **not** fold into the T3 net rate. Report prominently: original T3 scored
N was 20/30 because one third of runs never reached the model.

## Action

1. Record `error_type` / `error` on `transport_error` CaptureGaps (**done**).
2. Re-run **T3 only** after warming `llama3.1:8b` (**done**): **30/30 scored,
   0 HARNESS_FAULT**, raw 30/30 = 1.0, NET vs T1 **+0.9333**. Original 10
   faults were transient transport during the 3b→8b swap, not model behaviour.
3. For hosted KT-1: same failure mode must stay fail-closed as HARNESS_FAULT
   with cost/abort visibility — never silently drop into NONE or D05.
   Prefer warming / health-checking the candidate endpoint before the first
   job of each pair.
