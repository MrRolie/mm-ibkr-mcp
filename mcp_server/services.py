"""Shared service layer for direct IBKR MCP access."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, TypeVar

from ibkr_core.client import ConnectionError as IBKRConnectionError
from ibkr_core.client import IBKRClient
from ibkr_core.config import get_config

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _init_ibkr_executor() -> None:
    """Ensure an asyncio loop exists for the IBKR worker thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)


class IBKRMCPService:
    """Thin async wrapper around the synchronous ibkr_core client/functions."""

    def __init__(self, *, request_timeout: float = 60.0, connect_timeout: int = 10) -> None:
        self.request_timeout = request_timeout
        self.connect_timeout = connect_timeout
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="ibkr-mcp",
            initializer=_init_ibkr_executor,
        )
        self._client: Optional[IBKRClient] = None
        self._lock = asyncio.Lock()

    def _current_config_signature(self) -> tuple[str, str, int]:
        config = get_config()
        return (config.trading_mode, config.ibkr_gateway_host, config.ibkr_gateway_port)

    def _client_matches_config(self, client: IBKRClient) -> bool:
        mode, host, port = self._current_config_signature()
        return client.mode == mode and client.host == host and client.port == port

    def _create_client(self) -> IBKRClient:
        config = get_config()
        base_client_id = config.client_id
        pid_offset = os.getpid() % 1000
        return IBKRClient(mode=config.trading_mode, client_id=base_client_id + pid_offset)

    async def get_client(self) -> IBKRClient:
        """Return a connected client, reconnecting when config has changed."""
        async with self._lock:
            if self._client is not None and not self._client_matches_config(self._client):
                await self._disconnect_locked()

            if self._client is None or not self._client.is_connected:
                self._client = self._create_client()
                loop = asyncio.get_running_loop()
                try:
                    await asyncio.wait_for(
                        loop.run_in_executor(
                            self._executor,
                            lambda: self._client.connect(timeout=self.connect_timeout),
                        ),
                        timeout=self.connect_timeout + 2,
                    )
                except IBKRConnectionError:
                    self._client = None
                    raise
                except Exception as exc:
                    self._client = None
                    raise IBKRConnectionError(f"Failed to connect to IBKR: {exc}") from exc

            return self._client

    async def run_with_client(
        self,
        operation: Callable[[IBKRClient], T],
        *,
        timeout_s: Optional[float] = None,
    ) -> T:
        """Run a synchronous IBKR core operation in the dedicated worker thread."""
        client = await self.get_client()
        loop = asyncio.get_running_loop()
        ctx = contextvars.copy_context()
        timeout = timeout_s if timeout_s is not None else self.request_timeout
        return await asyncio.wait_for(
            loop.run_in_executor(self._executor, lambda: ctx.run(operation, client)),
            timeout=timeout,
        )

    async def run_sync(self, operation: Callable[[], T], *, timeout_s: Optional[float] = None) -> T:
        """Run a synchronous non-client operation in the dedicated worker thread."""
        loop = asyncio.get_running_loop()
        ctx = contextvars.copy_context()
        timeout = timeout_s if timeout_s is not None else self.request_timeout
        return await asyncio.wait_for(
            loop.run_in_executor(self._executor, lambda: ctx.run(operation)),
            timeout=timeout,
        )

    async def invalidate_client(self) -> None:
        """Drop the current client so the next request reconnects with fresh config."""
        async with self._lock:
            await self._disconnect_locked()

    async def _disconnect_locked(self) -> None:
        if self._client is None:
            return
        client = self._client
        self._client = None
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, client.disconnect)
        except Exception as exc:
            logger.warning("Error disconnecting MCP IBKR client: %s", exc)

    async def disconnect(self) -> None:
        """Disconnect any active client and release resources."""
        async with self._lock:
            await self._disconnect_locked()

    async def shutdown(self) -> None:
        """Shut down the service and its dedicated executor."""
        await self.disconnect()
        self._executor.shutdown(wait=True)
