# Support

You are stuck, and you want a clear path to an answer.

## Start here

1. Read [README.md](README.md) for install and the three core commands.
2. Read [docs/CI.md](docs/CI.md) if the failure is in a pipeline.
3. Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) if you need the record / re-drive shape.
4. Search existing GitHub issues before opening a new one.

## Bug reports

Open an issue with:

- Vernier version (`uv run python -c "import vernier; print(vernier.__version__)"`)
- OS and Python version
- Exact command you ran
- What you expected, and what you got
- A minimal reproduction if you can share one (fixture run id, tiny agent, or redacted log)

Example of a useful report title and body:

```text
Title: vern gate exits 2 on ReplayMiss for streamed chat

Command: vern gate --corpus vernier-corpus --max-severity MEDIUM
Expected: exit 1 with a D-rule, or exit 0
Actual: exit 2, agent_output shows ReplayMiss on POST /v1/chat/completions
Python: 3.12.3 on Ubuntu 24.04
```

## Security issues

See [SECURITY.md](SECURITY.md). Do not open a public issue for vulnerabilities.

## What we will decline

Changes that fight the kill tests in [README.md](README.md), or that put LLMs, embeddings, or nondeterministic scores on the classify path, will be declined. Capture ideas you will not build in [IDEAS.md](IDEAS.md).
