"""Configuration helpers for the IBKR MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal, Optional
from urllib.parse import urlparse

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp.server import TransportSecuritySettings


TransportType = Literal["stdio", "sse", "streamable-http"]


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_int(value: Optional[str], default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _parse_float(value: Optional[str], default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _parse_csv(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _normalize_path(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"


def _default_public_base_url(host: str, port: int) -> str:
    public_host = host
    if host == "0.0.0.0":
        public_host = "127.0.0.1"
    return f"http://{public_host}:{port}"


@dataclass(slots=True)
class MCPConfig:
    """Runtime configuration for the MCP server."""

    transport: TransportType = "stdio"
    host: str = "127.0.0.1"
    port: int = 8001
    streamable_http_path: str = "/mcp"
    sse_path: str = "/sse"
    message_path: str = "/messages/"
    request_timeout: float = 60.0
    connect_timeout: int = 10
    auth_token: Optional[str] = None
    public_base_url: Optional[str] = None
    auth_issuer_url: Optional[str] = None
    allowed_hosts: list[str] = field(default_factory=list)
    allowed_origins: list[str] = field(default_factory=list)
    enable_admin_tools: bool = False
    enable_legacy_aliases: bool = False
    log_level: str = "INFO"
    json_response: bool = False
    stateless_http: bool = False

    def __post_init__(self) -> None:
        if self.transport not in {"stdio", "sse", "streamable-http"}:
            raise ValueError(f"Unsupported MCP transport: {self.transport}")

        self.streamable_http_path = _normalize_path(self.streamable_http_path)
        self.sse_path = _normalize_path(self.sse_path)
        self.message_path = _normalize_path(self.message_path)
        self.log_level = self.log_level.upper()

        if self.public_base_url is None:
            self.public_base_url = _default_public_base_url(self.host, self.port)
        if self.auth_issuer_url is None:
            self.auth_issuer_url = self.public_base_url

        if self.transport in {"sse", "streamable-http"} and not self.auth_token:
            raise ValueError(
                "MCP_AUTH_TOKEN is required when MCP_TRANSPORT is 'sse' or 'streamable-http'"
            )

    @property
    def is_http_transport(self) -> bool:
        """Whether the transport is HTTP-based."""
        return self.transport in {"sse", "streamable-http"}

    def build_auth_settings(self) -> Optional[AuthSettings]:
        """Build FastMCP auth settings for HTTP transports."""
        if not self.is_http_transport:
            return None
        return AuthSettings(
            issuer_url=self.auth_issuer_url,
            resource_server_url=self.public_base_url,
            required_scopes=[],
        )

    def build_transport_security(self) -> Optional[TransportSecuritySettings]:
        """Build transport security settings for HTTP transports."""
        if not self.is_http_transport:
            return None

        if self.allowed_hosts or self.allowed_origins:
            return TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=self.allowed_hosts,
                allowed_origins=self.allowed_origins,
            )

        if self.host in {"127.0.0.1", "localhost", "::1"}:
            # Let FastMCP auto-provision its localhost-safe defaults.
            return None

        parsed = urlparse(self.public_base_url)
        default_host = parsed.netloc
        default_origin = f"{parsed.scheme}://{parsed.netloc}"
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[default_host],
            allowed_origins=[default_origin],
        )


def get_mcp_config() -> MCPConfig:
    """Load MCP configuration from environment variables."""
    return MCPConfig(
        transport=os.environ.get("MCP_TRANSPORT", "stdio").strip().lower(),
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=_parse_int(os.environ.get("MCP_PORT"), 8001),
        streamable_http_path=os.environ.get("MCP_STREAMABLE_HTTP_PATH", "/mcp"),
        sse_path=os.environ.get("MCP_SSE_PATH", "/sse"),
        message_path=os.environ.get("MCP_MESSAGE_PATH", "/messages/"),
        request_timeout=_parse_float(os.environ.get("MCP_REQUEST_TIMEOUT"), 60.0),
        connect_timeout=_parse_int(os.environ.get("MCP_CONNECT_TIMEOUT"), 10),
        auth_token=os.environ.get("MCP_AUTH_TOKEN"),
        public_base_url=os.environ.get("MCP_PUBLIC_BASE_URL"),
        auth_issuer_url=os.environ.get("MCP_AUTH_ISSUER_URL"),
        allowed_hosts=_parse_csv(os.environ.get("MCP_ALLOWED_HOSTS")),
        allowed_origins=_parse_csv(os.environ.get("MCP_ALLOWED_ORIGINS")),
        enable_admin_tools=_parse_bool(os.environ.get("MCP_ENABLE_ADMIN_TOOLS")),
        enable_legacy_aliases=_parse_bool(os.environ.get("MCP_ENABLE_LEGACY_ALIASES")),
        log_level=os.environ.get("MCP_LOG_LEVEL", "INFO"),
        json_response=_parse_bool(os.environ.get("MCP_JSON_RESPONSE")),
        stateless_http=_parse_bool(os.environ.get("MCP_STATELESS_HTTP")),
    )
