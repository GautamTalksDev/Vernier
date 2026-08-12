# Threat model

Vernier treats recorded agent runs as **untrusted evidence**, not trusted
input. Capture, replay, classification, and CLI rendering must assume every
byte may be hostile.

## Trust boundaries

| Boundary | Inside (trusted) | Outside (untrusted) |
| --- | --- | --- |
| Operator CLI argv / flags on the machine running `vern` | Chosen by the operator | Values copied from a run, corpus, or remote PR |
| Packaged `costs-v1.toml` / `ignore-v1.toml` | Shipped with Vernier | Overlays from a cloned repo (`vernier.toml`) |
| Local project tree when resolving store paths | Project root chosen by the operator | Paths and names embedded in records |
| Model provider TLS channel | That the operator configured a provider | Response bodies, tool calls, tool results |
| Record store under `.vernier/` | Directory layout Vernier creates | File contents, digests, manifests, event lines |

## Assume hostile

- Every recorded byte: event envelopes, blob payloads, manifests, indexes.
- Every provider response and every tool description / argument / result.
- Every `vernier.toml` (and any other config) found in a cloned repository.
- Run ids, server names, tool names, blob refs, and URLs appearing in data.
- Agent stdout/stderr captured during re-drive and printed by `vern`.

## Defenses in scope

- Terminal-safe rendering of untrusted strings (no control sequences).
- Path confinement so store reads/writes cannot escape the run / project root.
- Resource limits: decompressed blob size, event line length, event count,
  alignment length, JSON nesting depth.
- Capture redaction of secrets in headers, URL query strings, and JSON bodies.
- Hardening of the `sitecustomize` injection path used by `vern record`.
- Strict parsing of `vernier.toml` (no code execution, no env mutation, no
  out-of-tree store paths).

## Out of scope

- Compromised operator workstation, malicious Vernier builds, or supply-chain
  attacks against the Python interpreter / OS.
- Confidentiality of secrets **already present** in the operator environment
  or typed into argv (Vernier does not scrub the operator's shell history).
- Preventing a wrapped agent from exfiltrating secrets over the network while
  it runs (capture records traffic; it does not sandbox the agent).
- Availability attacks against the model provider itself.
- Guaranteeing that a human reviewer is not socially engineered by benign-
  looking but misleading natural-language tool text (we strip terminal
  controls; we do not censor meaning).
