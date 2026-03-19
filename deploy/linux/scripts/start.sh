#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

usage() {
    echo "Usage: $0 [live|paper|all]"
}

target="${1:-all}"

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

if [ ! -f .env ]; then
    echo "Error: .env file not found. Copy .env.example and fill in credentials."
    exit 1
fi

docker compose up -d "${services[@]}"

echo ""
echo "Gateway services started (${target})."
case "$target" in
    live)
        echo "  Live API: localhost:4001"
        echo "  Live VNC: localhost:5900"
        ;;
    paper)
        echo "  Paper API: localhost:4002"
        echo "  Paper VNC: localhost:5901"
        ;;
    all)
        echo "  Live API:  localhost:4001"
        echo "  Paper API: localhost:4002"
        echo "  Live VNC:  localhost:5900"
        echo "  Paper VNC: localhost:5901"
        ;;
esac
