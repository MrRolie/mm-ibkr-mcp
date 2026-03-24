# Linux Dual Gateway Deployment

Production deployment for two IBKR Gateway sessions on Linux with Docker, using IBC for auto-login and 2FA handling. The gateways stay local on this host. Remote LLM access should use SSH stdio into the MCP wrapper instead of exposing the raw IB sockets.

## Quick Start

```bash
cd deploy/linux
cp .env.example .env
# Edit .env with your live and paper IBKR credentials
./scripts/start.sh
./scripts/status.sh
```

Approve both 2FA prompts on your mobile device on first start.

## Quick Reference

| Service | Address | Purpose |
|---------|---------|---------|
| IB Gateway Live | `127.0.0.1:4001` | Live trading API socket |
| IB Gateway Paper | `127.0.0.1:4002` | Paper trading API socket |
| Live VNC | `127.0.0.1:5900` | Live gateway UI access |
| Paper VNC | `127.0.0.1:5901` | Paper gateway UI access |

## Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│                    Linux Host                                   │
│                                                                 │
│  ┌─────────────────────┐     ┌─────────────────────┐            │
│  │  IB Gateway Live    │     │  IB Gateway Paper   │            │
│  │  (IBC + Xvfb+socat) │     │  (IBC + Xvfb+socat) │            │
│  │  API: 4003 -> 4001  │     │  API: 4004 -> 4002  │            │
│  │  VNC: 5900 -> 5900  │     │  VNC: 5900 -> 5901  │            │
│  └─────────────────────┘     └─────────────────────┘            │
│                                                                 │
│  SSH stdio MCP access                                           │
│  - ssh -T ibkr-mcp@host                                         │
│  - forced command -> run_mcp_stdio.sh -> uv run ibkr-mcp        │
│  - MCP then talks locally to 127.0.0.1:4001 / 127.0.0.1:4002    │
└─────────────────────────────────────────────────────────────────┘
```

## File Structure

```text
deploy/linux/
├── docker-compose.yml
├── .env.example
├── config/
│   └── tws_settings/
│       ├── live/
│       └── paper/
├── scripts/
│   ├── start.sh
│   ├── stop.sh
│   ├── status.sh
│   └── logs.sh
└── README.md
```

## Configuration

### Environment Variables

| Variable | Description |
|----------|-------------|
| `TWS_USERID_LIVE` | Live IBKR username |
| `TWS_PASSWORD_LIVE` | Live IBKR password |
| `TWS_USERID_PAPER` | Paper IBKR username |
| `TWS_PASSWORD_PAPER` | Paper IBKR password |
| `TIME_ZONE` | Host timezone, for example `America/Toronto` |
| `VNC_PASSWORD` | Shared VNC password for both gateway UIs |

### Runtime Defaults

| Service | Auto Restart | API Port | VNC Port |
|---------|--------------|----------|----------|
| Live | `11:45 PM` | `4001` | `5900` |
| Paper | `11:50 PM` | `4002` | `5901` |

Shared settings:
- `TWOFA_TIMEOUT_ACTION=restart`
- `RELOGIN_AFTER_TWOFA_TIMEOUT=yes`
- `EXISTING_SESSION_DETECTED_ACTION=primary`
- `TWS_ACCEPT_INCOMING=accept`

## Operating Model

- Run both sessions concurrently so live strategies can remain active while new strategies are tested in paper.
- Use separate live and paper usernames. IBKR supports simultaneous live and paper sessions when they use different credentials and different ports.
- `mm-trading` and local tools connect directly to `127.0.0.1:4001` or `127.0.0.1:4002`.

## Management Commands

```bash
./scripts/start.sh
./scripts/start.sh live
./scripts/start.sh paper

./scripts/stop.sh
./scripts/stop.sh live
./scripts/stop.sh paper

./scripts/status.sh
./scripts/status.sh live
./scripts/status.sh paper

./scripts/logs.sh
./scripts/logs.sh live
./scripts/logs.sh paper
```

## Direct Connectivity

### Python (`ib_insync`)

```python
from ib_insync import IB

ib = IB()
ib.connect("127.0.0.1", 4001, clientId=100)  # Live
# ib.connect("127.0.0.1", 4002, clientId=200)  # Paper

print(ib.managedAccounts())
ib.disconnect()
```

Use distinct `clientId` values for each process. If you run live and paper connections at the same time, keep their client IDs separate as well.

### `mm-trading`

`mm-trading` should continue using direct `ibkr_core` socket access:
- live lane uses `127.0.0.1:4001`
- paper lane uses `127.0.0.1:4002`
- no Linux HTTP wrapper should be added for these paths

## Claude Code Over SSH StdIO

Recommended pattern for a remote single-user LLM client:

1. Keep both gateways local on this host.
2. Create a dedicated forced-command SSH account for MCP access.
3. Have the remote client use `ssh -T <mcp-user>@<this-host>` as the MCP command.

This keeps the brokerage socket local and only exposes SSH.

### Host Setup

Run the installer as root after you have the remote machine's public key:

```bash
cd /path/to/mm-ibkr-gateway/deploy/linux
sudo ./scripts/setup_mcp_ssh_user.sh \
  --user ibkr-mcp \
  --public-key-file /path/to/remote-machine.pub
```

What the setup script does:

- creates or reuses the dedicated SSH account
- installs a forced-command `authorized_keys` entry pointing to `scripts/run_mcp_stdio.sh`
- locks the account to key-only SSH access
- creates a dedicated uv-backed virtualenv in the SSH user's home directory
- grants repo read access and runtime data read/write access via ACLs

The forced-command wrapper always launches:

```bash
MCP_TRANSPORT=stdio uv run --active --no-sync --frozen --no-dev --project /path/to/mm-ibkr-gateway ibkr-mcp
```

### Remote Client Command

For Claude Code or another MCP client that supports stdio commands:

```json
{
  "mcpServers": {
    "ibkr-gateway": {
      "command": "ssh",
      "args": ["-T", "ibkr-mcp@YOUR_IB_HOST"]
    }
  }
}
```

If you connect over Tailscale, keep using the Tailscale network address or MagicDNS name, but disable Tailscale SSH on this host:

```bash
sudo tailscale set --ssh=false
```

Reason: this deployment relies on normal `sshd` so the `ibkr-mcp` user can be forced into `run_mcp_stdio.sh`. Tailscale SSH bypasses that path and will not attach the MCP server correctly.

Recommended Mac `~/.ssh/config` entry:

```sshconfig
Host trade-node-mcp
    HostName 100.95.151.127
    User ibkr-mcp
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    BatchMode yes
    PreferredAuthentications publickey
    PasswordAuthentication no
    RequestTTY no
```

If the client requires an explicit remote command instead of a forced command:

```json
{
  "mcpServers": {
    "ibkr-gateway": {
      "command": "ssh",
      "args": [
        "-T",
        "ibkr-mcp@YOUR_IB_HOST",
        "/path/to/mm-ibkr-gateway/deploy/linux/scripts/run_mcp_stdio.sh"
      ]
    }
  }
}
```

### Verification

Local wrapper verification:

```bash
./scripts/verify_mcp_ssh.sh
```

SSH verification against the forced-command account:

```bash
./scripts/verify_mcp_ssh.sh --ssh-target ibkr-mcp@YOUR_IB_HOST
```

Include gateway-dependent checks once the Docker gateways are up:

```bash
./scripts/verify_mcp_ssh.sh --ssh-target ibkr-mcp@YOUR_IB_HOST --gateway-checks
```

`control.json` and `config.json` changes are picked up automatically by new MCP requests. If Claude already has a long-lived stdio session open, reconnect the server once after changing trading mode or SSH settings.

### Optional SSHD Match Block

If you prefer an sshd Match block in addition to the restricted `authorized_keys` entry, see:

- `deploy/linux/ssh/sshd_config.d/ibkr-mcp.conf.example`

That example disables TTY, port forwarding, agent forwarding, X11 forwarding, and user rc files while forcing the MCP wrapper command.

## 2FA Handling

1. Each container starts IBC and auto-fills credentials.
2. IBKR sends a mobile 2FA prompt for the live session and another for the paper session.
3. Approve both prompts within the IBKR timeout window.
4. If a prompt expires, IBC restarts the login flow and sends a fresh prompt.

Weekly re-authentication is still required after the Sunday reset window. Daily auto-restarts should not need fresh approval outside that weekly cycle as long as the same usernames are not used elsewhere.

## VNC Access

```bash
vncviewer 127.0.0.1:5900  # Live
vncviewer 127.0.0.1:5901  # Paper
```

Both sessions use `VNC_PASSWORD` from `.env`.

## Troubleshooting

### A gateway is not starting

```bash
./scripts/logs.sh live
./scripts/logs.sh paper
```

Common causes:
- missing or incorrect credentials in `.env`
- 2FA approval timed out
- another application is using the same username
- stale settings under `config/tws_settings/live` or `config/tws_settings/paper`

### A socket port is closed

```bash
./scripts/status.sh
```

If the container is up but the API port is closed, inspect the matching VNC session to confirm whether IB Gateway is still at the login or 2FA screen.

### Another trading app gets logged out

IBKR will not keep multiple trading apps active on the same username. Dedicate one username to live automation and a different username to paper automation.

## Data Locations

| Path | Purpose |
|------|---------|
| `deploy/linux/.env` | Live and paper gateway credentials |
| `deploy/linux/config/tws_settings/live/` | Persistent live gateway settings |
| `deploy/linux/config/tws_settings/paper/` | Persistent paper gateway settings |

## Security

1. Credentials stay in `.env`, which should remain gitignored.
2. All ports are bound to `127.0.0.1`.
3. VNC is password-protected on separate live and paper ports.
4. Remote LLM access should use SSH stdio into a forced-command account.
5. No Linux REST API or MCP HTTP listener is required for the SSH deployment path.

## References

- [IBKR Campus: Third Party Connections](https://www.interactivebrokers.com/campus/ibkr-api-page/third-party-connections/)
- [IBC User Guide](https://github.com/IbcAlpha/IBC/blob/master/userguide.md)
- [ib-gateway-docker](https://github.com/gnzsnz/ib-gateway-docker)
- [IB Gateway Downloads](https://www.interactivebrokers.com/en/trading/ibgateway-stable.php)
