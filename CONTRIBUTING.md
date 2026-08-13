# Contributing

This repo is optimized for a solo-maintained workflow with agent assistance.

## Default flow

1. Create a short-lived branch from `main`.
2. Make the change.
3. Run:
   - `uv run python -m compileall -q ibkr_core mcp_server trade_core tests`
   - `uv run pytest -m "not integration" -q`
4. Push the branch and open a draft PR.
5. Merge with squash once CI is green.

## Scope expectations

- Keep the repo MCP-first.
- Do not add gateway process management, REST/UI, or deploy surfaces here.
- Preserve safety defaults and approval behavior.

## Integration tests

Integration tests require a running local IB Gateway or TWS instance and are not part of default CI.
