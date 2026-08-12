# TAXONOMY-V2-NOTES.md

Proposed changes only. **Do not implement** while CP-10 / KT-2 labelling is in flight.
Nothing here has been applied to `SPEC-DIVERGENCE.md` or `src/vernier/classify/`.

## Context from hand labelling (2026-08-07)

All 60 LOW+ KT-1 divergences were hand-labelled **BEHAVIOURAL** from blind
aligned diffs: baseline retained a full tool pipeline; candidate retained
`LlmTurn` + `Terminal` with **zero** `ToolCall`s.

## Proposed taxonomy / product notes

### 1. Harness / transport collapse (new meta-class or report flag)

**Problem.** D05 (tool in A absent in B) fires correctly on the trajectories,
but the human label “BEHAVIOURAL” conflates “model skipped a step” with
“re-drive never reached tools because live LLM transport failed.”

**Proposal.** Add a non-severity annotation (not a D-rule) such as
`setup_failure: llm_unreachable` when candidate trajectory has no ToolCalls
and recorded LLM HTTP errors / connection failures exist in the run log.
Keep it off the max-severity path, or gate it behind an explicit report
section, so KT scores stay about agent behaviour when setup is valid.

**Do not implement yet.** Needs a deterministic, fixture-backed definition.

### 2. Terminal-only candidate short-circuit

**Problem.** Several blinds showed candidate length 2 (`LlmTurn`,
`Terminal:FinalMessage`) vs baseline length 17. D05 alone does not
communicate “total action-sequence collapse.”

**Proposal.** Optional compound detail string or companion rule that fires when
action-sequence length ratio (or absolute candidate tool count == 0 with
baseline tools >= N) exceeds a fixed, pre-registered threshold — still
deterministic, no similarity scores.

**Risk.** Thresholds need pre-registration; avoid inventing them mid-labelling.

### 3. Cosmetic channel still unused in this corpus

**Observation.** With current KT-1 candidates, no LOW+ case was Cosmetics-only
(wording / LlmTurn text). V2 may need a dedicated cosmetic fixture pack so
precision/recall on COSMETIC is estimable — methodology, not a D-rule change.

### 4. Labelling vocabulary vs severity vocabulary

**Proposal.** Keep human labels `{COSMETIC, BEHAVIOURAL, UNCLEAR}` separate from
classifier severity. Document mapping expectations in RESULTS (e.g. D05/D04
expected BEHAVIOURAL; D11/D12 expected COSMETIC) without changing rule ranks
until after RESULTS lock.

## Explicitly not changing now

- No edits to D01–D12 ranks or predicates.
- No ignore-list updates aimed at masking these KT-1 collapses.
- No LLM/embedding assist for classification.
