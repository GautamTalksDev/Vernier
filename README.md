# Vernier

Deterministic capture, replay, and divergence classification for agent runs.

## Kill Tests

KT-1: If a single-variable model-version change produces behavioural
      divergence in fewer than 20% of 30+ recorded real agent runs,
      agents are robust to the change people actually make, there is
      no pain, and this project stops.
KT-2: If the classifier cannot agree with hand-labelled ground truth
      on >=85% of divergences AND catch 100% of behavioural
      divergences, it is worse than useless -- it is a false green
      light in CI -- and this project stops.
KT-3: If a full-stub re-drive of 10 recorded runs of >=8 steps each
      does not produce a 100% action match, the substrate is broken
      and nothing above it means anything.
KT-3b: If a live-diff re-drive with no model/prompt override (unchanged
      variable) does not return severity NONE, the live-diff harness is
      broken by definition and KT-1/KT-2 results are void.
KT-4: If nobody has put `vern gate` in a CI pipeline unprompted
      within 3 months of launch, there is no business here.

Status: see [`RESULTS.md`](RESULTS.md). Retracted v0 write-up:
[`RESULTS-v0-RETRACTED.md`](RESULTS-v0-RETRACTED.md).

## Install

Requires Python 3.11+ and [uv](https://github.com/astral-sh/uv).

```bash
uv sync
uv run vern --help
```

## Quick start

```bash
# Record an agent run (zero edits to the agent)
uv run vern record -- python examples/verify_fixture_agent.py --scenario 01_tools_basic

# Live re-drive: LLM live, tools from the record; print a human verdict
uv run vern diff <run_id> --model gpt-4o-mini

# Machine-readable report
uv run vern diff <run_id> --json

# CI gate (exit 0 at/below threshold, 1 above, 2 operational error)
uv run vern gate <run_id> --max-severity MEDIUM
uv run vern gate --corpus testdata/verify --max-severity HIGH
```

## Exit codes

| Command | 0 | 1 | 2 |
| --- | --- | --- | --- |
| `vern verify` | identical trajectory (severity NONE) | divergence or ReplayMiss | operational error |
| `vern diff` | no divergence | divergence found | operational error |
| `vern gate` | severity ≤ `--max-severity` | severity above threshold | operational error |
| `vern verify-record` | integrity OK | integrity failed | operational error |

## Network guarantee

`vern` makes **ZERO** network requests other than to the model provider
you have already configured. No telemetry, no version check, no
analytics, no phone-home.

## CI gate

```bash
vern init                          # vernier.toml + .github/workflows/vernier-gate.yml
vern gate --corpus vernier-corpus --max-severity MEDIUM
```

GitHub Action (zero Vernier secrets — provider key only): see [docs/CI.md](docs/CI.md).

```yaml
- uses: gautamtalksdev/Vernier@main
  with:
    corpus-path: vernier-corpus
    max-severity: MEDIUM
```

## Corpus (KT-1)

```bash
uv run python scripts/build_corpus.py    # ≥30 runs, 6 agent shapes
uv run python scripts/verify_corpus.py   # each run → vern verify NONE
uv run python scripts/run_kt1.py         # CSV; $100 hard budget cap
```

Raw records under `testdata/corpus/runs/` are gitignored (large). Rebuild
locally or sync via `VERNIER_CORPUS_BUCKET`. See `testdata/corpus/README.md`.

## Development

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
