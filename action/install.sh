#!/usr/bin/env bash
# Install Vernier into a venv on PATH for the composite action.
set -euo pipefail
VENV="${RUNNER_TEMP:-/tmp}/vernier-action-venv"
uv venv "${VENV}"
# shellcheck disable=SC2129
echo "${VENV}/bin" >> "${GITHUB_PATH}"
export PATH="${VENV}/bin:${PATH}"

ACTION_PATH="${GITHUB_ACTION_PATH:-}"
VERNIER_REF="${VERNIER_REF:-}"

if [ -n "${VERNIER_REF}" ]; then
  uv pip install --python "${VENV}/bin/python" "vernier @ git+https://github.com/${VERNIER_REF}"
elif [ -n "${ACTION_PATH}" ] && [ -f "${ACTION_PATH}/pyproject.toml" ]; then
  uv pip install --python "${VENV}/bin/python" "${ACTION_PATH}"
else
  uv pip install --python "${VENV}/bin/python" vernier
fi

vern --version 2>/dev/null || vern --help >/dev/null
