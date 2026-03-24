#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${IBKR_MCP_PROJECT_DIR:-$(cd "${SCRIPT_DIR}/../../.." && pwd)}"
USER_VENV_DEFAULT="${HOME}/.local/share/mm-ibkr-gateway/.venv"
PROJECT_VENV_DEFAULT="${REPO_DIR}/.venv"
VENV_PATH="${IBKR_MCP_VENV_PATH:-${USER_VENV_DEFAULT}}"
APP_BIN="${VENV_PATH}/bin/ibkr-mcp"
PROJECT_APP_BIN="${PROJECT_VENV_DEFAULT}/bin/ibkr-mcp"

if [ ! -d "${REPO_DIR}" ]; then
    echo "Error: mm-ibkr-gateway repo not found at ${REPO_DIR}" >&2
    exit 1
fi

if [ ! -x "${VENV_PATH}/bin/python" ] && [ -x "${PROJECT_VENV_DEFAULT}/bin/python" ]; then
    VENV_PATH="${PROJECT_VENV_DEFAULT}"
    APP_BIN="${PROJECT_APP_BIN}"
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

if [ -x "${APP_BIN}" ]; then
    exec "${APP_BIN}"
fi

if [ -x "${PROJECT_APP_BIN}" ]; then
    exec "${PROJECT_APP_BIN}"
fi

exec uv run --active --no-sync --frozen --no-dev --project "${REPO_DIR}" ibkr-mcp
