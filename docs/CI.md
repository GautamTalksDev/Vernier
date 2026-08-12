# Vernier in CI

Vernier’s distribution vehicle is **`vern gate` inside someone else’s
pipeline**. The GitHub Action posts a PR comment, uploads the JSON report,
and sets a `vernier/gate` commit status. Vernier adds **no secrets of its
own** — only the model provider key you already use (optional for
fixtures that ship a local stub).

## GitHub Actions

### One-liner (`vern init`)

From a repo that already has an agent:

```bash
uv pip install vernier   # or: uv pip install /path/to/Vernier
vern init
# edits vernier.toml + .github/workflows/vernier-gate.yml
```

Record baselines into the corpus path (default `vernier-corpus/`):

```bash
mkdir -p vernier-corpus/demo_01
uv run vern record --out vernier-corpus/demo_01 -- python agent.py
# move/rename so layout is vernier-corpus/<label>/run/…
```

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
        uses: gautamtalksdev/Vernier@main   # pin a tag in production
        with:
          corpus-path: vernier-corpus
          max-severity: MEDIUM
          model: gpt-4o-mini              # optional candidate override
          baseline-model: gpt-4o          # report label only
          fail-on-miss: "true"
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

### Action inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `corpus-path` | `vernier-corpus` | Directory of recorded runs |
| `max-severity` | `MEDIUM` | Fail when any run is above this |
| `model` | _(empty)_ | Live-diff candidate model override |
| `baseline-model` | _(empty)_ | Metadata label in the report |
| `fail-on-miss` | `true` | Exit 2 on hard `ReplayMiss` |
| `prompt-file` | _(empty)_ | Optional system prompt override |

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
    - uv pip install --system vernier
    # or: uv pip install --system "vernier @ git+https://github.com/gautamtalksdev/Vernier@main"
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

Map `OPENAI_API_KEY` (or your provider’s variable) in GitLab CI/CD settings.
Vernier does not phone home.

## Bare `vern gate`

```bash
vern gate --corpus vernier-corpus --max-severity MEDIUM
vern gate --corpus vernier-corpus --max-severity HIGH \
  --model gpt-4o-mini --baseline-model gpt-4o \
  --json-out gate-report.json --comment-out gate-comment.md
echo $?   # 0 ok, 1 above threshold, 2 operational / miss
```

Network guarantee: **zero** requests other than to the model provider you
already configured.
