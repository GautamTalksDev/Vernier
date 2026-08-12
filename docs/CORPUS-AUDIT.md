# Corpus audit — CP-9 agents are not model-driven

**Date:** 2026-08-10  
**Verdict:** The KT-1 corpus built from these agents cannot measure model-upgrade
drift. Tool selection is scripted in Python; the LLM is used only for narration.

## Why the old corpus is invalid

Sensitivity controls against Ollama (`scripts/burnin_control.py`) showed:

| Control | Perturbation | Result |
| --- | --- | --- |
| C1 | candidate `temperature=1.0` | 5/5 NONE, tools 6→6 |
| C2 | candidate `llama3.2:3b` (different family) | 5/5 NONE, tools 6→6 |
| C3 | system prompt forcing extra tool use | 5/5 NONE, tools 6→6 |
| C4 | harness-side drop of one `@tool` | PASS — D05 (tools 6→5) |

C2 is decisive: swapping model families cannot yield an identical 6-tool
trajectory by chance if the model chooses tools.

Arithmetic from the burn-in candidate records: **120 LLM turns / 30 runs =
4 turns/run**, but **6 tool calls/run**. More tools than LLM turns means tools
are not gated on `tool_calls` from the model.

This is the same class of defect as live-diff dialing `replay.invalid`: the
instrument measures something other than model behaviour.

**Disposition:** `testdata/corpus/` contents moved to
`testdata/corpus-v0-invalid/` (not deleted). Rebuild only after model-driven
agents land and C1–C4 all pass.

---

## Per-agent audit

All six agents share `examples/_corpus_llm.chat()`, which POSTs a single user
message and returns **text `content` only** — it never sends a tools schema and
never reads `message.tool_calls`.

### `research_agent.py`

| Item | Finding |
| --- | --- |
| Tool choice | **Hardcoded sequence:** `web_search` → `fetch_url` → `extract_quotes` → `write_summary` → `fetch_url` → `write_summary`. URLs taken from prior tool return values in Python. |
| LLM calls | **4** per run. Used only as narration (`research topic…`, `choose sources…`, `draft summary…`, `finalize…`). Return value ignored. |
| Tools | 4 (`web_search`, `fetch_url`, `extract_quotes`, `write_summary`) |

### `data_agent.py`

| Item | Finding |
| --- | --- |
| Tool choice | **Hardcoded sequence:** `sql_query` → `transform_rows` → `validate_schema` → `write_table` → `sql_query` → `write_table`. |
| LLM calls | **4** per run. Narration only; return value ignored. |
| Tools | 4 (`sql_query`, `transform_rows`, `validate_schema`, `write_table`) |

### `support_agent.py`

| Item | Finding |
| --- | --- |
| Tool choice | **Hardcoded sequence** with args from Python string-split of `--input` (`customer:ticket`): `lookup_customer` → `lookup_ticket` → `decide_action` → `apply_action` → `lookup_ticket` → `apply_action`. |
| LLM calls | **4** per run. Narration only. |
| Tools | 4 (`lookup_customer`, `lookup_ticket`, `decide_action`, `apply_action`) |

### `coding_agent.py`

| Item | Finding |
| --- | --- |
| Tool choice | **Hardcoded sequence:** `read_file` → `edit_file` → `git_status` → `run_tests` → `read_file` → `run_tests`. |
| LLM calls | **4** per run. Narration only. |
| Tools | 4 (`read_file`, `edit_file`, `run_tests`, `git_status`) |

### `booking_agent.py`

| Item | Finding |
| --- | --- |
| Tool choice | **Hardcoded sequence** plus Python `sorted(...price)` to pick the cheap option: `search_flights` → `select_option` → `hold_seat` → `confirm_booking` → `search_flights` → `confirm_booking`. |
| LLM calls | **4** per run. Narration only. |
| Tools | 4 (`search_flights`, `select_option`, `hold_seat`, `confirm_booking`) |

### `router_agent.py`

| Item | Finding |
| --- | --- |
| Tool choice | **Mixed:** `classify_intent` is always called in Python; then **`if/else` on the tool’s return value** (`weather` / `math` / else) selects `tool_weather` / `tool_math` / `tool_search`. Further calls (`route_log`, second `classify_intent`, `tool_search`, `route_log`) are hardcoded. Still not model-driven. |
| LLM calls | **4** per run. Narration only. |
| Tools | 5 (`classify_intent`, `tool_weather`, `tool_math`, `tool_search`, `route_log`) |

---

## Shared helper defect

`examples/_corpus_llm.py`:

- Local stub returns `{content: "[model] plan:…"}` with **no `tool_calls`**.
- `chat()` never passes `tools` / `tool_choice` and never executes model-selected tools.

Until both the helper and the agents are rewritten so that (1) the full tool
schema is sent every turn, (2) only returned `tool_calls` are executed, and
(3) the loop continues until the model stops calling tools (or a step cap),
no corpus derived from these agents is valid for KT-1.

---

## Remediation status (post-audit)

Agents and `_corpus_llm.run_tool_agent` were rewritten to be model-driven.
Sensitivity controls C1–C4 then passed (C2 fires D04/D05/D08/D09 on
`llama3.2:3b`).

**Full-stub KT-3 pitfall:** ToolResult blobs are stored as JCS (sorted keys).
On replay the stub returns that dict; if the agent embeds tool results in the
next LLM body with insertion-order `json.dumps`, request digests diverge and
verify raises `ReplayMiss`. Agents now embed
`json.dumps(..., sort_keys=True, separators=(",", ":"))`. Any corpus recorded
before that fix is invalid for KT-3 even if agents were already model-driven.

Sampling fields default to `temperature=0`, `seed=42`, `stream=false` so
full-stub redrive does not depend on a polluted shell env.
