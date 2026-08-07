# Architecture

Vernier records agent behaviour on two capture planes, then aligns and
classifies divergences deterministically.

## Two capture planes

```text
                         Agent process
+------------------------------------------------------------------+
|                                                                  |
|   Plane A: Function shim              Plane B: Transport shim    |
|   -------------------------           -------------------------- |
|   In-process call boundary            Network / I/O boundary     |
|   - tool invocations                  - HTTP / RPC / sockets     |
|   - model / LLM calls                 - external service traffic |
|   - local function entry/exit         - request/response bytes   |
|                                                                  |
|              \                              /                    |
|               \                            /                     |
|                v                          v                      |
|              +--------------------------------+                  |
|              |   Append-only record store     |                  |
|              |   (ordered, hash-chained)      |                  |
|              +--------------------------------+                  |
|                              |                                   |
|                              v                                   |
|              +--------------------------------+                  |
|              | canonicalize -> align ->       |                  |
|              | classify -> report             |                  |
|              +--------------------------------+                  |
+------------------------------------------------------------------+
```

- **Function shim** captures semantic call-plane events inside the process.
- **Transport shim** captures byte-plane traffic leaving the process.
- Both planes append into the same record store so replay and classification
  can reason about one ordered trajectory.

Package layout maps to this pipeline:

| Package | Role |
| --- | --- |
| `vernier.capture` | Transport shim + function shim |
| `vernier.record` | Append-only record store |
| `vernier.canonical` | Canonicalization + hashing (pure, no I/O) |
| `vernier.align` | Trajectory alignment |
| `vernier.classify` | Deterministic divergence classifier |
| `vernier.report` | Human + JSON output |
| `vernier.cli` | `vern` console entrypoint |
