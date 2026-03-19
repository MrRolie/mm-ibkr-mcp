# Linux Dual Gateway Deployment

Production deployment for two IBKR Gateway sessions on Linux with Docker, using IBC for auto-login and 2FA handling. This Linux deployment is gateway-only: there is no extra HTTP service in the runtime path.

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
4. No Linux REST API is exposed.

## References

- [IBKR Campus: Third Party Connections](https://www.interactivebrokers.com/campus/ibkr-api-page/third-party-connections/)
- [IBC User Guide](https://github.com/IbcAlpha/IBC/blob/master/userguide.md)
- [ib-gateway-docker](https://github.com/gnzsnz/ib-gateway-docker)
- [IB Gateway Downloads](https://www.interactivebrokers.com/en/trading/ibgateway-stable.php)
