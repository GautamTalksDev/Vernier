# Contributing to Vernier

Thanks for helping improve Vernier.

## Developer Certificate of Origin (DCO)

All contributions must be signed off under the [Developer Certificate of
Origin](https://developercertificate.org/). Every commit must include a
`Signed-off-by` line matching the commit author:

```text
Signed-off-by: Your Name <your.email@example.com>
```

Create signed-off commits with:

```bash
git commit -s
```

Unsigned commits will not be accepted.

By signing off, you certify that you have the right to submit the work under
the project's Apache License 2.0 and that you agree to the DCO.

## Development setup

Requires Python 3.11+ and [uv](https://github.com/astral-sh/uv).

```bash
uv sync
uv run ruff check .
uv run mypy src
uv run pytest
uv run vern --help
```

## Pull requests

- Keep changes focused and small enough to review.
- Do not add dependencies that are not already in `pyproject.toml` without
  maintainer agreement.
- Verdict, comparison, and classification logic must remain deterministic and
  unit-testable with fixed expected values. No LLM calls, embeddings,
  similarity scores, heuristic thresholds, or unsolicited network requests on
  those paths.
- Comparison output must be byte-identical across Linux, macOS, and Windows.
- Update tests when behaviour changes.
- Ensure `ruff` and `mypy` (strict) are clean.

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
