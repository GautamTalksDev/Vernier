# KT-1 evaluation corpus

Baseline model: `vernier-baseline-local-v1` (local deterministic stub).

## Layout

| Path | In git? | Purpose |
| --- | --- | --- |
| `MANIFEST.json` | yes | Index of all runs (ids, shapes, inputs) |
| `README.md` | yes | This file |
| `runs/<run_id>/` | **no** (gitignored) | Raw records (events, blobs) — large |
| `kt1_results.csv` | no | Produced by `scripts/run_kt1.py` |

## Rebuild / restore

```bash
uv run python scripts/build_corpus.py
uv run python scripts/verify_corpus.py   # every run → vern verify severity NONE
```

Optional external bucket (document only; no phone-home from vern):

```bash
export VERNIER_CORPUS_BUCKET=s3://your-bucket/vernier-corpus
# sync with your own tooling, e.g. aws s3 sync testdata/corpus/runs "$VERNIER_CORPUS_BUCKET"
```

Git LFS alternative: track `testdata/corpus/runs/**` via LFS if you prefer
pushing binaries instead of regenerating.

## Agent shapes (6)

1. `research` — search → read → summarize  
2. `data` — query → transform → write  
3. `support` — lookup → decide → act  
4. `coding` — read → edit → test  
5. `booking` — search → select → confirm  
6. `router` — multi-tool intent router  

Each shape has ≥5 varied inputs (≥30 runs total). Each run is ≥8 projected
trajectory steps and uses ≥4 tools.
