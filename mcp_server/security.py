"""Authentication helpers for the IBKR MCP server."""

from __future__ import annotations

import secrets

from mcp.server.auth.provider import AccessToken, TokenVerifier


class StaticBearerTokenVerifier(TokenVerifier):
    """Simple bearer-token verifier for private MCP deployments."""

    def __init__(self, token: str, *, client_id: str = "ibkr-mcp-client") -> None:
        self._token = token
        self._client_id = client_id

    async def verify_token(self, token: str) -> AccessToken | None:
        if not secrets.compare_digest(token, self._token):
            return None
        return AccessToken(token=token, client_id=self._client_id, scopes=[])
