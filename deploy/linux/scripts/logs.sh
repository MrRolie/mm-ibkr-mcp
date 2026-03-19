#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

usage() {
    echo "Usage: $0 [live|paper|all]"
}

target="${1:-all}"
shift || true

case "$target" in
    live)
        services=(ib-gateway-live)
        ;;
    paper)
        services=(ib-gateway-paper)
        ;;
    all)
        services=(ib-gateway-live ib-gateway-paper)
        ;;
    *)
        usage
        exit 1
        ;;
esac

docker compose logs -f "${services[@]}" "$@"
