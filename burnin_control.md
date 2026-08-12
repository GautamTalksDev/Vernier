# Burn-in sensitivity controls

Endpoint: `http://localhost:11434/v1`
Baseline model: `qwen3:4b-instruct` (model-driven agents)
Runs per control: 5

Agents now send the full tool schema every turn and execute only
`message.tool_calls`. See `docs/CORPUS-AUDIT.md`.

## C1 — PASS

- **setup:** candidate temperature=1.0 (baseline 0)
- **expected:** at least one non-NONE severity across 5 runs
- **expected rule IDs:** (any non-NONE severity)
- **observed rule IDs:** D01, D04, D06, D07, D08
- **observed severities:** HIGH, CRITICAL
- **verdict:** PASS

| run | label | status | severity | rule_ids | tools | wall_s |
| --- | --- | --- | --- | --- | --- | ---: |
| research_01 | research_01 | completed | HIGH | D04 | 4→4 | 75.6 |
| research_02 | research_02 | completed | HIGH | D04\|D06\|D07\|D08 | 4→6 | 66.7 |
| research_03 | research_03 | completed | CRITICAL | D01\|D04\|D06 | 3→4 | 49.6 |
| research_04 | research_04 | completed | HIGH | D04 | 7→7 | 103.9 |
| research_05 | research_05 | completed | HIGH | D04 | 4→4 | 39.3 |

## C2 — PASS

- **setup:** candidate model=llama3.2:3b
- **expected:** D04 and/or D05 and/or D06 fires
- **expected rule IDs:** D04, D05, D06
- **observed rule IDs:** D04, D05, D08, D09
- **observed severities:** HIGH
- **verdict:** PASS

| run | label | status | severity | rule_ids | tools | wall_s |
| --- | --- | --- | --- | --- | --- | ---: |
| research_01 | research_01 | completed | HIGH | D04\|D09 | 4→4 | 38.7 |
| research_02 | research_02 | completed | HIGH | D04\|D09 | 4→4 | 35.1 |
| research_03 | research_03 | completed | HIGH | D04\|D05 | 3→1 | 22.5 |
| research_04 | research_04 | completed | HIGH | D04\|D05\|D08 | 7→1 | 24.5 |
| research_05 | research_05 | completed | HIGH | D04\|D09 | 4→4 | 18.7 |

## C3 — PASS

- **setup:** --prompt-file "Always call the first available tool twice."
- **expected:** D06 or D08 fires
- **expected rule IDs:** D06, D08
- **observed rule IDs:** D01, D04, D06
- **observed severities:** HIGH, CRITICAL, HARNESS_FAULT
- **verdict:** PASS

| run | label | status | severity | rule_ids | tools | wall_s |
| --- | --- | --- | --- | --- | --- | ---: |
| research_01 | research_01 | completed | HIGH | D04 | 4→4 | 42.4 |
| research_02 | research_02 | completed | HIGH | D04 | 4→4 | 31.8 |
| research_03 | research_03 | completed | CRITICAL | D01\|D04\|D06 | 3→4 | 62.6 |
| research_04 | research_04 | HARNESS_FAULT | HARNESS_FAULT | — | 7→0 | 3.4 |
| research_05 | research_05 | HARNESS_FAULT | HARNESS_FAULT | — | 4→0 | 0.8 |

## C4 — PASS

- **setup:** one tool removed from schema (`VERNIER_CONTROL_DROP_TOOL=web_search`)
- **expected:** D05 fires
- **expected rule IDs:** D05
- **observed rule IDs:** D01, D04, D05, D06, D07, D08
- **observed severities:** HIGH, CRITICAL
- **verdict:** PASS

| run | label | status | severity | rule_ids | tools | wall_s |
| --- | --- | --- | --- | --- | --- | ---: |
| research_01 | research_01 | completed | HIGH | D04\|D05 | 4→3 | 40.4 |
| research_02 | research_02 | completed | HIGH | D04\|D05 | 4→3 | 45.8 |
| research_03 | research_03 | completed | CRITICAL | D01\|D04\|D05\|D06 | 3→3 | 56.5 |
| research_04 | research_04 | completed | HIGH | D04\|D05\|D07\|D08 | 7→3 | 43.3 |
| research_05 | research_05 | completed | HIGH | D04\|D05 | 4→3 | 49.3 |
