#!/bin/bash
set -euo pipefail

# Resolve the mm-ibkr-mcp repo root from this script's location.
# scripts/ is one level below the repo root.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${IBKR_MCP_PROJECT_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"

if [ ! -d "${REPO_DIR}" ]; then
    echo "Error: mm-ibkr-mcp repo not found at ${REPO_DIR}" >&2
    exit 1
fi

# Prefer an explicit venv override, then the ibkr-mcp user's local share venv,
# then the project-level venv, then fall back to plain uv run.
USER_VENV_DEFAULT="${HOME}/.local/share/mm-ibkr-mcp/.venv"
PROJECT_VENV_DEFAULT="${REPO_DIR}/.venv"
VENV_PATH="${IBKR_MCP_VENV_PATH:-${USER_VENV_DEFAULT}}"

# Fall through to project venv if user venv does not exist.
if [ ! -x "${VENV_PATH}/bin/python" ] && [ -x "${PROJECT_VENV_DEFAULT}/bin/python" ]; then
    VENV_PATH="${PROJECT_VENV_DEFAULT}"
fi

if [ -x "${VENV_PATH}/bin/python" ]; then
    export VIRTUAL_ENV="${VENV_PATH}"
    export PATH="${VENV_PATH}/bin:${PATH}"
fi

export PYTHONUNBUFFERED=1
export MCP_TRANSPORT=stdio
export MCP_HOST=127.0.0.1
unset MCP_AUTH_TOKEN

cd "${REPO_DIR}"

APP_BIN="${VENV_PATH}/bin/ibkr-mcp"
PROJECT_APP_BIN="${PROJECT_VENV_DEFAULT}/bin/ibkr-mcp"

if [ -x "${APP_BIN}" ]; then
    exec "${APP_BIN}"
fi

if [ -x "${PROJECT_APP_BIN}" ]; then
    exec "${PROJECT_APP_BIN}"
fi

exec uv run --active --no-sync --frozen --no-dev --project "${REPO_DIR}" ibkr-mcp
