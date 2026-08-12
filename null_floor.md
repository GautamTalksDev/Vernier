# Null-pair floor root cause (NOT KT-1)

**Question:** T1 produced D04 fires in the research domain. Agent bug or model
nondeterminism?

**Verdict:** **Model nondeterminism (b).** After removing the `num_ctx`
confound and adding a request-parity guard, the corrected null floor is
**unchanged** at **6/90 = 6.7%** (Wilson 95% CI [0.031, 0.138]). All six
fires remain research-only D04. The research agent is deterministic.

---

## Confound removed (2026-08-12)

**Problem:** Tier-study T1 candidates sent `options.num_ctx=16384` while the
recorded corpus did not. That leaked a control variable into every comparison.

**Fix:**
- Tier study / burn-in no longer set `VERNIER_LLM_NUM_CTX` (both legs:
  `request_options=null`).
- Live-diff transport now **aborts with HARNESS_FAULT** if control fields
  (`temperature`, `stream`, `options`, `tools`, `tool_choice`) differ from
  the baseline recording before the model is called. Allowed overrides:
  `/model` when `VERNIER_OVERRIDE_MODEL` is set; `/seed` when
  `VERNIER_LLM_SEED` is set. See `src/vernier/replay/request_parity.py`.

**Result:** Corrected T1 still **6/90** divergences — the prior 6.7% was not
inflated by `num_ctx`. It is genuine model-level variance on research free-text
tool arguments.

Prior contaminated run archived: `tier_study-v0-num_ctx-contaminated.csv.bak`.

---

## Corrected T1 null floor

| Metric | Value |
| --- | --- |
| Comparisons | 90 (30 runs × seeds 42/43/44) |
| Scored | 90 |
| Divergences | **6** (all D04) |
| Raw rate | **0.0667** |
| Wilson 95% CI | **[0.0309, 0.1379]** |
| HARNESS_FAULT | 0 |
| Domains | research only (6/15 = 40%) |

Same six cells as before confound removal:

| Seed | Run | Tool(s) with D04 |
| --- | --- | --- |
| 42 | research_01 | `write_summary` (bullets paraphrase) |
| 42 | research_03 | `fetch_url` URL + cascade |
| 42 | research_04 | `web_search` query + cascade |
| 42 | research_05 | `web_search` query + cascade |
| 43 | research_01 | full chain from `web_search` |
| 44 | research_01 | full chain from `web_search` |

Example D04 (research_01 / seed=42 / `write_summary`):

- `/bullets/0`: `…atmospheric, oceans, and ecosystems…` vs `…atmospheric,
  oceanic, and land processes…`
- `/bullets/2–4`: IPCC framing vs drivers/impacts framing

---

## 50× exact-body replay (corrected, options matched)

Re-POSTed the **recorded** request body at the first D04-producing turn,
50 trials each, to `http://localhost:11434/v1/chat/completions`.
Artifact: `null_floor_replay50.json`.

| Case | Turn | Tool | Distribution (50 trials) | Shape |
| --- | ---: | --- | --- | --- |
| research_01 seed=42 | 3 | `write_summary` | **49** / **1** | 98% dominant attractor |
| research_03 seed=42 | 1 | `fetch_url` | **49** / **1** | 98% dominant |
| research_04 seed=42 | 0 | `web_search` | **49** / **1** | 98% dominant |
| research_05 seed=42 | 0 | `web_search` | **49** / **1** | 98% dominant |
| research_01 seed=43 | 0 | `web_search` | **49** / **1** | 98% dominant |
| research_01 seed=44 | 0 | `web_search` | **50** / **0** | singleton (this prompt) |

**Not 25/25 bimodal.** The model occupies a **strongly skewed two-attractor**
distribution (~98% / ~2%) on most prompts, not a fair coin. Seed 44 /
research_01 turn-0 was a singleton in 50 trials (recorded corpus draw may have
been the rare arm).

Dominant vs rare arms (examples):

- `web_search` query: `urban-heat research sources` (49×) vs `urban-heat
  definition and causes` (1×)
- `fetch_url`: `…llms-features-doc-1` (49×) vs `…llms-key-features-1` (1×)
- `write_summary`: two distinct 5-bullet climate summaries (49× vs 1×)

---

## Why not agent nondeterminism

`examples/research_agent.py` tools are pure functions of arguments (no time,
UUID, dict iteration, or live search). Tool results are embedded with JCS
(`sort_keys=True`). D04 differing values are model-authored strings.

No agent fix. No corpus rebuild warranted for this finding.

---

## Implications

1. **6.7% is the corrected instrument noise floor** on this stack
   (qwen3:4b-instruct, temp 0, batch 1, local CPU, options matched).
2. Tier-study **NET rates vs T1** are unchanged for T1/T2; T3 scored 20/20
   diverged with 10 separate HARNESS_FAULT zero-tool collapses on
   data/research (llama3.1:8b — report separately).
3. D04 on paraphrastic tool args is the operational divergence class; D04
   labelling quality remains the critical path.

Taxonomy untouched. `RESULTS.md` untouched.
