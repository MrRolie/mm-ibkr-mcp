#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

cd "${REPO_DIR}"
exec uv run python "${REPO_DIR}/deploy/linux/scripts/verify_mcp_ssh_stdio.py" "$@"
