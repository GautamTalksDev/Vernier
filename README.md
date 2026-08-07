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
KT-4: If nobody has put `vern gate` in a CI pipeline unprompted
      within 3 months of launch, there is no business here.

## Install

Requires Python 3.11+ and [uv](https://github.com/astral-sh/uv).

```bash
uv sync
uv run vern --help
```

## Development

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
