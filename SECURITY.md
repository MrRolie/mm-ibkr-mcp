# Security Policy

## Supported surface

Security-sensitive areas include:
- order placement and cancellation
- approval and control flows
- account data exposure
- local configuration and secret handling

## Reporting

Please do not open public issues for potential security vulnerabilities.

Instead, contact the maintainer privately with:
- a short description of the issue
- impact
- reproduction steps if available

## Expectations

- Never commit real credentials, account IDs, or private broker data.
- Keep `orders_enabled=false` and `dry_run=true` as safe defaults unless intentionally testing execution behavior.
