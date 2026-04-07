#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR_DEFAULT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WRAPPER_DEFAULT="${SCRIPT_DIR}/run_mcp_stdio.sh"
SSH_USER="${IBKR_MCP_SSH_USER:-ibkr-mcp}"
PUBLIC_KEY_FILE="${IBKR_MCP_PUBLIC_KEY_FILE:-}"
PUBLIC_KEY_VALUE="${IBKR_MCP_PUBLIC_KEY:-}"
REPO_DIR="${IBKR_MCP_PROJECT_DIR:-${REPO_DIR_DEFAULT}}"
WRAPPER_SCRIPT="${IBKR_MCP_WRAPPER_SCRIPT:-${WRAPPER_DEFAULT}}"
UV_BIN="${UV_BIN:-}"
DRY_RUN=0

usage() {
    cat <<EOF
Usage: sudo $0 --public-key-file /path/to/key.pub [options]

Options:
  --user USER               Dedicated SSH username (default: ${SSH_USER})
  --public-key-file PATH    Public key file to authorize for forced-command access
  --public-key KEY          Public key string to authorize
  --repo-dir PATH           mm-ibkr-mcp repo root (default: ${REPO_DIR_DEFAULT})
  --wrapper PATH            Forced-command wrapper path (default: ${WRAPPER_DEFAULT})
  --dry-run                 Print planned actions without changing the host
  -h, --help                Show this help text
EOF
}

log() {
    echo "[setup-mcp-ssh-user] $*"
}

resolve_uv_bin() {
    if [ -n "${UV_BIN}" ] && [ -x "${UV_BIN}" ]; then
        return 0
    fi

    if command -v uv >/dev/null 2>&1; then
        UV_BIN="$(command -v uv)"
        return 0
    fi

    if [ -n "${SUDO_USER:-}" ]; then
        local sudo_home
        local sudo_user_uv

        # Check uv using the invoking user's login shell PATH. This catches
        # user-local installs (for example snap-managed paths) that root
        # cannot discover with command -v.
        if command -v sudo >/dev/null 2>&1; then
            sudo_user_uv="$(sudo -Hiu "${SUDO_USER}" sh -lc 'command -v uv 2>/dev/null' | tr -d '\r\n' || true)"
            if [ -n "${sudo_user_uv}" ] && [ -x "${sudo_user_uv}" ]; then
                UV_BIN="${sudo_user_uv}"
                return 0
            fi
        fi

        sudo_home="$(getent passwd "${SUDO_USER}" | cut -d: -f6)"
        if [ -n "${sudo_home}" ] && [ -x "${sudo_home}/.local/bin/uv" ]; then
            UV_BIN="${sudo_home}/.local/bin/uv"
            return 0
        fi
    fi

    for candidate in /usr/local/bin/uv /usr/bin/uv; do
        if [ -x "${candidate}" ]; then
            UV_BIN="${candidate}"
            return 0
        fi
    done

    echo "Error: could not find uv. Re-run with UV_BIN set, for example:" >&2
    echo "  sudo env UV_BIN=\$(command -v uv) $0 ..." >&2
    exit 1
}

run() {
    if [ "${DRY_RUN}" -eq 1 ]; then
        printf '[dry-run] '
        printf '%q ' "$@"
        printf '\n'
        return 0
    fi
    "$@"
}

need_root() {
    if [ "${EUID}" -ne 0 ]; then
        echo "Error: run this script with sudo/root." >&2
        exit 1
    fi
}

# mm-ibkr-mcp RuntimeConfig has: control_dir, data_storage_dir, audit_db_path, log_dir
# It does NOT have watchdog_log_dir (that was mm-ibkr-gateway only).
load_runtime_paths() {
    mapfile -t RUNTIME_PATHS < <(
        "${UV_BIN}" run --project "${REPO_DIR}" python - <<'PY'
from ibkr_core.runtime_config import load_runtime_config

cfg = load_runtime_config(create_if_missing=True)
print(cfg.control_dir)
print(cfg.data_storage_dir)
print(cfg.audit_db_path)
print(cfg.log_dir)
PY
    )

    if [ "${#RUNTIME_PATHS[@]}" -lt 4 ]; then
        echo "Error: failed to load runtime config paths (expected 4, got ${#RUNTIME_PATHS[@]})." >&2
        exit 1
    fi

    CONTROL_DIR="${RUNTIME_PATHS[0]}"
    STORAGE_DIR="${RUNTIME_PATHS[1]}"
    AUDIT_DB_PATH="${RUNTIME_PATHS[2]}"
    LOG_DIR="${RUNTIME_PATHS[3]}"
}

grant_traverse_acl() {
    local target="$1"
    local path="$target"

    if [ ! -d "$path" ]; then
        path="$(dirname "$path")"
    fi

    while [ "$path" != "/" ]; do
        run setfacl -m "u:${SSH_USER}:x" "$path"
        path="$(dirname "$path")"
    done
}

grant_tree_acl() {
    local target="$1"
    local perms="$2"

    if [ ! -e "$target" ]; then
        run install -d -m 0755 "$target"
    fi

    run setfacl -R -m "u:${SSH_USER}:${perms}" "$target"
    if [ -d "$target" ]; then
        run find "$target" -type d -exec setfacl -m "u:${SSH_USER}:${perms}" {} +
        run find "$target" -type d -exec setfacl -d -m "u:${SSH_USER}:${perms}" {} +
    fi
}

ensure_user() {
    if id "${SSH_USER}" >/dev/null 2>&1; then
        log "User ${SSH_USER} already exists."
    else
        run useradd --create-home --shell /bin/bash "${SSH_USER}"
        run passwd -l "${SSH_USER}"
        log "Created user ${SSH_USER}."
    fi

    USER_HOME="$(getent passwd "${SSH_USER}" | cut -d: -f6)"
    if [ -z "${USER_HOME}" ]; then
        echo "Error: could not determine home directory for ${SSH_USER}." >&2
        exit 1
    fi
}

install_authorized_key() {
    local ssh_dir="${USER_HOME}/.ssh"
    local auth_keys="${ssh_dir}/authorized_keys"
    local key_line

    if [ -n "${PUBLIC_KEY_FILE}" ]; then
        if [ ! -f "${PUBLIC_KEY_FILE}" ]; then
            echo "Error: public key file not found at ${PUBLIC_KEY_FILE}" >&2
            exit 1
        fi
        PUBLIC_KEY_VALUE="$(tr -d '\r' < "${PUBLIC_KEY_FILE}")"
    fi

    if [ -z "${PUBLIC_KEY_VALUE}" ]; then
        echo "Error: provide --public-key-file or --public-key." >&2
        exit 1
    fi

    key_line="command=\"${WRAPPER_SCRIPT}\",restrict,no-user-rc,no-pty,no-agent-forwarding,no-port-forwarding,no-X11-forwarding ${PUBLIC_KEY_VALUE}"

    run install -d -o "${SSH_USER}" -g "${SSH_USER}" -m 0700 "${ssh_dir}"
    run touch "${auth_keys}"
    run chown "${SSH_USER}:${SSH_USER}" "${auth_keys}"
    run chmod 0600 "${auth_keys}"

    if [ "${DRY_RUN}" -eq 1 ]; then
        echo "[dry-run] append to ${auth_keys}: ${key_line}"
        return 0
    fi

    if ! grep -Fxq "${key_line}" "${auth_keys}"; then
        printf '%s\n' "${key_line}" >> "${auth_keys}"
        chown "${SSH_USER}:${SSH_USER}" "${auth_keys}"
        chmod 0600 "${auth_keys}"
        log "Added forced-command key to ${auth_keys}."
    else
        log "Forced-command key already present in ${auth_keys}."
    fi
}

prepare_runtime_env() {
    local venv_path="${USER_HOME}/.local/share/mm-ibkr-mcp/.venv"

    run install -d -o "${SSH_USER}" -g "${SSH_USER}" -m 0755 "${USER_HOME}/.local/share/mm-ibkr-mcp"
    run install -d -o "${SSH_USER}" -g "${SSH_USER}" -m 0755 "${USER_HOME}/.cache/uv"

    if [ "${DRY_RUN}" -eq 1 ]; then
        echo "[dry-run] create venv and sync project for ${SSH_USER} at ${venv_path}"
        return 0
    fi

    env HOME="${USER_HOME}" UV_CACHE_DIR="${USER_HOME}/.cache/uv" \
        "${UV_BIN}" venv --allow-existing "${venv_path}"

    env \
        HOME="${USER_HOME}" \
        UV_CACHE_DIR="${USER_HOME}/.cache/uv" \
        VIRTUAL_ENV="${venv_path}" \
        PATH="${venv_path}/bin:${PATH}" \
        "${UV_BIN}" sync --active --locked --no-dev --project "${REPO_DIR}"

    run chown -R "${SSH_USER}:${SSH_USER}" \
        "${USER_HOME}/.local/share/mm-ibkr-mcp" \
        "${USER_HOME}/.cache/uv"
}

apply_permissions() {
    grant_traverse_acl "${REPO_DIR}"
    grant_traverse_acl "${CONTROL_DIR}"
    grant_traverse_acl "${STORAGE_DIR}"
    grant_traverse_acl "${LOG_DIR}"

    grant_tree_acl "${REPO_DIR}" "r-x"
    grant_tree_acl "${CONTROL_DIR}" "rwX"
    grant_tree_acl "${STORAGE_DIR}" "rwX"
    grant_tree_acl "${LOG_DIR}" "rwX"

    if [ -e "${AUDIT_DB_PATH}" ]; then
        run setfacl -m "u:${SSH_USER}:rw-" "${AUDIT_DB_PATH}"
    fi
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --user)
            SSH_USER="$2"
            shift 2
            ;;
        --public-key-file)
            PUBLIC_KEY_FILE="$2"
            shift 2
            ;;
        --public-key)
            PUBLIC_KEY_VALUE="$2"
            shift 2
            ;;
        --repo-dir)
            REPO_DIR="$2"
            shift 2
            ;;
        --wrapper)
            WRAPPER_SCRIPT="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: unknown argument $1" >&2
            usage
            exit 1
            ;;
    esac
done

need_root
resolve_uv_bin

if [ ! -d "${REPO_DIR}" ]; then
    echo "Error: repo directory not found at ${REPO_DIR}" >&2
    exit 1
fi

if [ ! -x "${WRAPPER_SCRIPT}" ] && [ ! -f "${WRAPPER_SCRIPT}" ]; then
    echo "Error: wrapper script not found at ${WRAPPER_SCRIPT}" >&2
    exit 1
fi

load_runtime_paths
ensure_user
apply_permissions
prepare_runtime_env
install_authorized_key

log "SSH stdio MCP setup complete for user ${SSH_USER}."
log "Repo:        ${REPO_DIR}"
log "Wrapper:     ${WRAPPER_SCRIPT}"
log "Control dir: ${CONTROL_DIR}"
log "Storage dir: ${STORAGE_DIR}"
log "Log dir:     ${LOG_DIR}"
