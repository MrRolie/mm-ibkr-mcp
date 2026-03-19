#!/bin/bash
set -u
cd "$(dirname "$0")/.."

usage() {
    echo "Usage: $0 [live|paper|all]"
}

target="${1:-all}"

case "$target" in
    live)
        services=(ib-gateway-live)
        checks=("Live|4001|5900")
        ;;
    paper)
        services=(ib-gateway-paper)
        checks=("Paper|4002|5901")
        ;;
    all)
        services=(ib-gateway-live ib-gateway-paper)
        checks=("Live|4001|5900" "Paper|4002|5901")
        ;;
    *)
        usage
        exit 1
        ;;
esac

check_port() {
    local port="$1"
    bash -c "</dev/tcp/127.0.0.1/${port}" >/dev/null 2>&1
}

echo "=== Container Status ==="
docker compose ps "${services[@]}"

echo -e "\n=== Port Reachability ==="
for check in "${checks[@]}"; do
    IFS="|" read -r label api_port vnc_port <<<"$check"

    if check_port "$api_port"; then
        echo "${label} API port ${api_port}: OPEN"
    else
        echo "${label} API port ${api_port}: CLOSED"
    fi

    if check_port "$vnc_port"; then
        echo "${label} VNC port ${vnc_port}: OPEN"
    else
        echo "${label} VNC port ${vnc_port}: CLOSED"
    fi
done
