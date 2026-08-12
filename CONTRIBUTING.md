# Contributing to Vernier

You want to change Vernier without breaking the only promise that matters: the same inputs produce the same verdict forever.

## Developer Certificate of Origin (DCO)

Every commit must carry a `Signed-off-by` line that matches the author:

```text
Signed-off-by: Your Name <your.email@example.com>
```

```bash
git commit -s
```

Unsigned commits will not be accepted. By signing off, you certify you can submit the work under Apache License 2.0 and you agree to the DCO ([text](https://developercertificate.org/)).

## Development setup

Python 3.11+ and [uv](https://github.com/astral-sh/uv):

```bash
uv sync
uv run ruff check .
uv run mypy src
uv run pytest
uv run vern --help
```

## Pull requests

Keep the change small enough to review.

Do not add a dependency that is not already in `pyproject.toml` without asking first.

Verdict, comparison, and classification logic must stay deterministic and unit-testable with fixed expected values. No LLM calls, embeddings, similarity scores, heuristic thresholds, or unsolicited network requests on those paths. Why: CI cannot depend on a judge that might disagree tomorrow.

Comparison output must be byte-identical across Linux, macOS, and Windows.

Update tests when behaviour changes. Keep `ruff` and `mypy` (strict) clean.

## Branch protection (`main`)

`main` is protected (you enable this in GitHub repo settings):

- No force-push to `main`.
- Pull requests required. Direct pushes to `main` are blocked.
- Required status checks must pass before merge (CI lint/test matrix, lockfile/audit, and other configured checks).
- Commits must be signed off (DCO as above). Repo settings may also require GPG or SSH signing.

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
