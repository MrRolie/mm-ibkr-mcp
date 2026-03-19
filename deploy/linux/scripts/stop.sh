#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

usage() {
    echo "Usage: $0 [live|paper|all]"
}

target="${1:-all}"

case "$target" in
    live)
        docker compose stop ib-gateway-live
        ;;
    paper)
        docker compose stop ib-gateway-paper
        ;;
    all)
        docker compose down
        ;;
    *)
        usage
        exit 1
        ;;
esac

echo "Gateway services stopped (${target})."
