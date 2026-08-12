# Vernier in CI

You want a pull request to fail when an agent starts behaving differently, not when a unit mock still returns green.

`vern gate` is that check. It re-drives recorded runs, classifies each pair, and exits non-zero when severity goes above your cap. The GitHub Action also posts a PR comment, uploads the JSON report, and sets a `vernier/gate` commit status.

Vernier does not add secrets of its own. If live-diff needs a model provider, you pass the key you already use. Fixture corpora that ship a local stub need no key.

## GitHub Actions

### Scaffold with `vern init`

From a repo that already has an agent:

```bash
uv pip install vernier-cli
vern init
```

That writes `vernier.toml` and `.github/workflows/vernier-gate.yml`.

Record a baseline into the corpus path (default `vernier-corpus/`):

```bash
mkdir -p vernier-corpus/demo_01
uv run vern record --out vernier-corpus/demo_01 -- python agent.py
```

Arrange folders so each run sits at `vernier-corpus/<label>/run/…` (or a flat run directory Vernier can discover). Then open a PR and let the workflow run.

### Copy-paste workflow

```yaml
name: Vernier gate
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read
  pull-requests: write
  statuses: write

jobs:
  vernier-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Vernier gate
        uses: gautamtalksdev/Vernier@main
        with:
          corpus-path: vernier-corpus
          max-severity: MEDIUM
          model: gpt-4o-mini
          baseline-model: gpt-4o
          fail-on-miss: "true"
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

Pin a tag or commit SHA in production instead of `@main`.

### Action inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `corpus-path` | `vernier-corpus` | Directory of recorded runs |
| `max-severity` | `MEDIUM` | Fail when any run is above this |
| `model` | (empty) | Live-diff candidate model override |
| `baseline-model` | (empty) | Metadata label in the report |
| `fail-on-miss` | `true` | Exit 2 on hard `ReplayMiss` |
| `prompt-file` | (empty) | Optional system prompt override |

Artifacts: `vernier-gate-report` (`gate-report.json`, `gate-comment.md`).
Commit status context: `vernier/gate`.

## GitLab CI

```yaml
vernier-gate:
  image: python:3.12-bookworm
  variables:
    PIP_DISABLE_PIP_VERSION_CHECK: "1"
  before_script:
    - pip install uv
    - uv pip install --system vernier-cli
  script:
    - |
      vern gate --corpus vernier-corpus \
        --max-severity MEDIUM \
        --json-out gate-report.json \
        --comment-out gate-comment.md \
        --fail-on-miss
  artifacts:
    when: always
    paths:
      - gate-report.json
      - gate-comment.md
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
```

Map `OPENAI_API_KEY` (or your provider's variable) in GitLab CI/CD settings.
Vernier does not phone home.

## Bare `vern gate`

```bash
vern gate --corpus vernier-corpus --max-severity MEDIUM
echo $?
# 0: every run at or below the cap
# 1: at least one run above the cap
# 2: operational failure (integrity, miss, crash)
```

With a candidate model override and report files:

```bash
vern gate --corpus vernier-corpus --max-severity HIGH \
  --model gpt-4o-mini --baseline-model gpt-4o \
  --json-out gate-report.json --comment-out gate-comment.md
```

Network rule: Vernier makes zero requests other than to the model provider you already configured.
