# SPEC-DIVERGENCE — divergence-v1

**Version:** `divergence-v1`  
**Status:** normative for Vernier classification  
**Audience:** an engineer implementing the classifier without reading Vernier source

This specification defines how two agent runs are compared to produce a
deterministic severity verdict. Classification uses only: (1) the typed
trajectory model below, (2) global sequence alignment as defined by
Vernier’s aligner (Needleman–Wunsch over the alignment alphabet), (3) the
rules D01–D12, and (4) the ignore-list. No model, embedding, probability,
or similarity score may be used except the plain word-level edit distance
defined for D11/D12.

---

## 1. Severity

Ordered from lowest to highest:

| Level | Rank |
| --- | ---: |
| `NONE` | 0 |
| `LOW` | 1 |
| `MEDIUM` | 2 |
| `HIGH` | 3 |
| `CRITICAL` | 4 |

**Run-pair severity** = maximum severity among all rules that fire.  
If no rule fires, severity is `NONE` with an empty rule-id list.

Every verdict **MUST** list the rule IDs that fired (e.g. `["D04","D06"]`),
sorted lexicographically. No opaque scores are permitted in the verdict.

---

## 2. Input model

### 2.1 Trajectory event

A run is an ordered sequence of **trajectory events**. Each event has:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `kind` | string enum | yes | One of: `LlmTurn`, `ToolCall`, `ToolResult`, `Terminal` |
| `tool_name` | string or null | if `ToolCall` | Non-empty for `ToolCall`; null otherwise |
| `arguments` | JSON object or null | if `ToolCall` | Tool arguments (JSON values only) |
| `terminal_kind` | string or null | if `Terminal` | Exactly one of: `FinalMessage`, `ToolCall` |
| `final_message` | string or null | if Terminal/`FinalMessage` | Final user-visible text |
| `is_error` | boolean | if `ToolResult` | Whether the tool returned an error |
| `destructive` | boolean | if `ToolCall` | True if schema annotates destructive/write |
| `parallel_group` | string or null | no | Same non-null id ⇒ parallel; see D07 |
| `retry_index` | integer ≥ 0 | no | 0 = first attempt; see D08 |

Unknown fields MUST be ignored by classifiers conforming to this version.

### 2.2 Alignment alphabet projection

For alignment only, project each trajectory event to an align symbol:

- `kind` preserved
- for `ToolCall`, `tool_name` preserved
- all other fields ignored by the aligner

Alignment produces an ordered list of pairs
`(a_idx | null, b_idx | null, relation)` where `relation` ∈
`{Matched, Inserted, Deleted, Substituted}` as defined by the Vernier
aligner. Classification **MUST** use that alignment; it MUST NOT
re-derive correspondence ad hoc.

### 2.3 Action sequence

The **action sequence** of a run is the ordered list of:

1. every `ToolCall` as `(tool_name, canonical_arguments_after_ignore)`, and  
2. the `Terminal.terminal_kind`

`LlmTurn` and `ToolResult` events are **not** part of the action sequence.
Canonical arguments are defined in §4.

Two action sequences are **identical** iff they are element-wise equal
after argument canonicalization (§4).

---

## 3. Ignore-list (`vernier.toml`)

Ignore configuration is **data**, versioned with this spec.

### 3.1 File location and version

Default packaged file: `src/vernier/classify/ignore-v1.toml`  
Optional project overlay: `./vernier.toml` (merged; overlay wins on conflict)

```toml
[divergence]
version = "divergence-v1"

[divergence.ignore]
# JSON Pointers (RFC 6901) into ToolCall.arguments objects.
pointers = [
  "/request_id",
  "/trace_id",
  "/nonce",
  "/headers/request_id",
  "/headers/trace_id",
]

# When true, any string value matching the UUID shape is treated as ignored
# wherever it appears under arguments (any depth).
strip_uuid_values = true

# When true, any string value matching ISO-8601 timestamp shapes
# (see §3.3) is treated as ignored wherever it appears under arguments.
strip_iso_timestamps = true
```

### 3.2 JSON Pointer application

For each pointer `P` in `pointers`, if `P` resolves in the arguments
object, the referenced value is replaced by the sentinel
`{"$vernier_ignore": true}` before comparison, and an **applied ignore**
record is emitted:

```json
{
  "mechanism": "pointer",
  "pointer": "/request_id",
  "side": "a" 
}
```

(`side` is `"a"`, `"b"`, or `"both"` when both sides had a value at `P`.)

Pointers that do not resolve are silent (not listed).

### 3.3 Shape-based ignores

**UUID shape** (case-insensitive hex):

```
^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$
```

**ISO-8601 timestamp shapes** (full-string match):

1. `YYYY-MM-DDTHH:MM:SSZ`  
2. `YYYY-MM-DDTHH:MM:SS.mmmZ`  
3. `YYYY-MM-DDTHH:MM:SS+HH:MM` / `-HH:MM` (optional fractional seconds)

When a string leaf matches and the corresponding strip flag is true, it is
replaced by the ignore sentinel and an applied-ignore record is emitted:

```json
{
  "mechanism": "uuid" | "iso_timestamp",
  "pointer": "/path/to/leaf",
  "side": "a" | "b" | "both"
}
```

Pointers for shape ignores are absolute JSON Pointers from the arguments
root, using `/`-joined object keys and decimal array indexes.

### 3.4 Report requirement

The classification report **MUST** include the full list of applied
ignores for the run pair (may be empty). Reviewers must be able to see
exactly what was excluded from D04.

---

## 4. Argument canonicalization (for D04 / action sequence)

Given `arguments` (object or null):

1. If null, canonical form is `null`.  
2. Apply ignore-list transforms (§3), collecting applied ignores.  
3. Canonicalize with Vernier canon rules (NFC, line endings, RFC 8785 JCS)
   and digest — or compare JCS byte strings for equality.

Two argument objects are **equal for D04** iff their post-ignore JCS byte
strings are identical.

---

## 5. Word-level distance (D11 / D12 only)

Tokenize on Unicode whitespace (`str.split()` with no arguments).  
Empty string → zero tokens.

Let `d` = Levenshtein edit distance on the token sequences  
(insert/delete/substitute cost 1 each).  
Let `n = max(len(tokens_a), len(tokens_b))`.  
If `n == 0`, distance ratio = `0.0`.  
Else ratio = `d / n`.

Threshold `T = 0.30` exactly (30%).  
Comparisons: `ratio > T` vs `ratio <= T` (strict greater for “more than 30%”).

No other similarity metric is permitted.

---

## 6. Rule evaluation order and firing

Evaluate rules independently on the aligned pair. A rule **fires** when its
predicate is true. Multiple rules may fire. Severity is the max of fired
rules’ severities.

Rule IDs in the verdict are the sorted unique IDs of fired rules.

Below, “aligned position” means an alignment pair with `relation = Matched`
or `relation = Substituted` that references both `a_idx` and `b_idx`, unless
a rule says otherwise. Inserted/Deleted pairs have only one side.

Define:

- `tools(run)` = set of `tool_name` for all `ToolCall` events in the run  
- `terminal(run)` = the last `Terminal` event in the run (required for D02;
  if missing, treat `terminal_kind` as absent/unequal)

---

## 7. Rules

### D01 — CRITICAL — new tool in B

**Fires when:** ∃ tool name in `tools(B)` that is not in `tools(A)`.

**Rationale:** Calling a tool the baseline never used is a behavioural
capability change, not cosmetics.

### D02 — CRITICAL — terminal kind differs

**Fires when:** `terminal(A).terminal_kind ≠ terminal(B).terminal_kind`
(including one side missing a Terminal).

**Rationale:** Ending with a final message vs attempting another tool call
changes observable completion semantics.

### D03 — CRITICAL — destructive tool at aligned position in B only

**Fires when:** there exists an alignment pair with both sides present where:

- side B is a `ToolCall` with `destructive == true`, and  
- side A is not a `ToolCall` with the same `tool_name`,  
  OR side A is not a `ToolCall` at all  

Also fires for an **Inserted** B `ToolCall` with `destructive == true`
(no A event at that pair).

**Rationale:** A write/destructive side effect introduced relative to the
baseline is a critical safety signal.

### D04 — HIGH — same tool, non-equal arguments (after ignore-list)

**Fires when:** there exists a `Matched` or `Substituted` pair where both
sides are `ToolCall` with equal `tool_name`, and canonical arguments
(§4) are not equal.

**Near-miss (MUST NOT fire):** only ignored fields differ (e.g. UUID /
timestamp / `request_id`).

**Rationale:** Same tool, different semantic arguments ⇒ different
behaviour.

### D05 — HIGH — tool call in A absent in B (skipped step)

**Fires when:** there exists a `Deleted` pair whose A event is a `ToolCall`.

**Rationale:** Baseline took a tool step that the candidate skipped.

### D06 — HIGH — tool call in B absent in A (added step)

**Fires when:** there exists an `Inserted` pair whose B event is a
`ToolCall`.

**Note:** If that tool name is also absent from `tools(A)`, D01 also fires.

**Rationale:** Candidate introduced a tool step the baseline did not take.

### D07 — MEDIUM — order of two non-parallel tool calls swapped

**Fires when:** there exist indices `i < j` in A’s ToolCall subsequence and
indices `p < q` in B’s ToolCall subsequence such that:

- `A[i].tool_name == B[q].tool_name`  
- `A[j].tool_name == B[p].tool_name`  
- `A[i].tool_name ≠ A[j].tool_name`  
- `parallel_group` of `A[i]` and `A[j]` are not equal non-null strings
  (i.e. they are **not** marked parallel with each other)  
- the same for `B[p]` and `B[q]`  
- and the relative order of those two names is reversed between A and B

**Near-miss (MUST NOT fire):** the two calls share the same non-null
`parallel_group` on both sides (genuinely parallel).

**Rationale:** Sequential order changes can matter; parallel scheduler
reordering must not.

### D08 — MEDIUM — retry count differs for the same tool

Define retries for tool name `t` as
`1 + max(retry_index)` over `ToolCall` events with that name
(or count of ToolCalls with that name if `retry_index` always 0).  
More precisely: let `count(run, t)` = number of `ToolCall` events with
`tool_name == t`.  

**Fires when:** ∃ `t` such that `count(A, t) ≠ count(B, t)` and
`count(A, t) ≥ 1` and `count(B, t) ≥ 1` (same tool appears in both, but
attempt cardinality differs).

**Rationale:** Extra retries change failure-handling behaviour even when
the eventual tool set matches.

### D09 — MEDIUM — tool error mismatch at aligned position

**Fires when:** there exists a `Matched`/`Substituted` pair where both
sides are `ToolResult` and `is_error` differs.

**Rationale:** Error vs success at the same step is behavioural.

### D10 — LOW — LLM turn count differs, action sequence identical

**Fires when:** action sequences (§2.3) are identical **and**
`count(LlmTurn, A) ≠ count(LlmTurn, B)`.

**Rationale:** Extra thinking turns without action change is low severity.

### D11 — LOW — final message differs by > 30% words, actions identical

**Fires when:** action sequences identical, both terminals are
`FinalMessage`, and word-level distance ratio (§5) **>** `0.30`.

### D12 — NONE — final message differs by ≤ 30% words, actions identical

**Fires when:** action sequences identical, both terminals are
`FinalMessage`, word-level distance ratio ∈ `(0, 0.30]`, and D11 did not
fire (i.e. not above threshold).

If messages are exactly equal, D12 does **not** fire (no text difference).

**Rationale:** Cosmetic wording under the threshold is recorded as NONE
so dashboards can show “text-only drift” without elevating severity.

---

## 8. Verdict object

```json
{
  "spec_version": "divergence-v1",
  "severity": "HIGH",
  "rule_ids": ["D04", "D06"],
  "fired": [
    {"id": "D04", "severity": "HIGH", "detail": "..."},
    {"id": "D06", "severity": "HIGH", "detail": "..."}
  ],
  "applied_ignores": [
    {"mechanism": "pointer", "pointer": "/request_id", "side": "both"}
  ]
}
```

- `rule_ids` sorted ascending  
- `fired` sorted by `id` ascending  
- `applied_ignores` sorted by `(mechanism, pointer, side)`  
- Serialization for golden tests: RFC 8785 JCS UTF-8

---

## 9. Complexity / refusal

If either run exceeds 5,000 trajectory events, classification MUST refuse
with an explicit error (same guard as alignment).

---

## 10. Conformance fixtures

Repository path: `testdata/divergence/`.  
At least five fixtures per rule D01–D12, plus near-miss fixtures that
MUST NOT false-positive on D04 (timestamp/uuid-only) and D07 (parallel
reorder). Each fixture directory contains `a.json`, `b.json`, and
`expected.json` with `severity` and `rule_ids`.
