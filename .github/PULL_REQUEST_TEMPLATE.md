## Summary

[Describe the change in 2-4 short sentences.]

## Validation

- [ ] `uv run python -m compileall -q ibkr_core mcp_server trade_core tests`
- [ ] `uv run pytest -m "not integration" -q`
- [ ] Manual validation performed when needed

## Safety Notes

- [ ] Trading behavior did not change
- [ ] Or, if trading behavior changed, paper-mode and safety behavior were verified
- [ ] No secrets, tokens, or account-specific data were added

## Notes

[Anything an implementer or reviewer should know.]
