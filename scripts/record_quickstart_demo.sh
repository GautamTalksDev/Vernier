#!/usr/bin/env bash
# Regenerate docs/assets/quickstart.{cast,svg} from a real vern gate run.
# Requires: asciinema (uv tool install asciinema), Node/npx for svg-term-cli.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p docs/assets
export PATH="${HOME}/.local/bin:${PATH}"
export COLUMNS=88 LINES=32 TERM=xterm-256color

# Warm the env so the cast is not dominated by a cold uv sync.
uv run vern gate --corpus testdata/verify --max-severity HIGH >/dev/null

asciinema rec \
  --overwrite \
  --cols 88 \
  --rows 28 \
  --title "vern gate quickstart" \
  --command 'printf "$ uv run vern gate --corpus testdata/verify --max-severity HIGH\n"; uv run vern gate --corpus testdata/verify --max-severity HIGH; printf "\n$ echo exit=$?\n"; echo "exit=$?"' \
  docs/assets/quickstart.cast

npx --yes svg-term-cli \
  --in docs/assets/quickstart.cast \
  --out docs/assets/quickstart.svg \
  --window \
  --width 88 \
  --height 28 \
  --padding 12

echo "wrote docs/assets/quickstart.cast and docs/assets/quickstart.svg"
