"""
IBKR Client wrapper for managing connections to Interactive Brokers Gateway/TWS.

Provides a stable, reconnection-aware wrapper around the current broker backend with:
- Dual mode support (paper/live)
- Automatic connection management
- Structured logging
- Connection health monitoring
"""

import asyncio
import logging
from datetime import datetime
from typing import List, Optional

from ib_insync import IB

from ibkr_core.broker import BrokerAdapter, IBInsyncBrokerAdapter
from ibkr_core.config import Config, get_config
from ibkr_core.logging_config import log_with_context
from ibkr_core.metrics import record_ibkr_operation, set_connection_status

logger = logging.getLogger(__name__)


class ConnectionError(Exception):
    """Raised when connection to IBKR fails."""

    pass


class IBKRClient:
    """
    Wrapper around the active broker backend for IBKR Gateway/TWS connections.

    Handles connection lifecycle, reconnection logic, and provides
    a clean interface for the rest of the application.

    Usage:
        client = IBKRClient()
        client.connect()
        # ... use client.broker for adapted operations ...
        client.disconnect()

    Or as context manager:
        with IBKRClient() as client:
            # ... use client.broker for adapted operations ...
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        mode: Optional[str] = None,
        client_id: Optional[int] = None,
    ):
        """
        Initialize IBKRClient.

        Args:
            config: Configuration object. If not provided, uses global config.
            mode: Override trading mode ('paper' or 'live'). If not provided, uses config.
            client_id: Override client ID. If not provided, uses config based on mode.
        """
        self._config = config or get_config()
        self._mode = mode.lower() if mode else self._config.trading_mode

        if self._mode not in ("paper", "live"):
            raise ValueError(f"Invalid mode: {self._mode}. Must be 'paper' or 'live'.")

        # Select port and client ID based on mode
        if self._mode == "live":
            self._port = self._config.live_gateway_port
            self._client_id = client_id if client_id is not None else self._config.live_client_id
        else:
            self._port = self._config.paper_gateway_port
            self._client_id = client_id if client_id is not None else self._config.paper_client_id

        self._host = self._config.ibkr_gateway_host
        self._ib = IB()
        self._broker = IBInsyncBrokerAdapter(self._ib)
        self._connected = False
        self._connection_time: Optional[datetime] = None

    @property
    def ib(self) -> IB:
        """Access to the underlying ib_insync.IB instance."""
        return self._ib

    @property
    def broker(self) -> BrokerAdapter:
        """Access to the current broker adapter."""
        return self._broker

    @property
    def is_connected(self) -> bool:
        """Check if client is connected to IBKR."""
        return self._connected and self._ib.isConnected()

    @property
    def mode(self) -> str:
        """Current trading mode (paper or live)."""
        return self._mode

    @property
    def host(self) -> str:
        """Gateway host."""
        return self._host

    @property
    def port(self) -> int:
        """Gateway port."""
        return self._port

    @property
    def client_id(self) -> int:
        """Client ID used for connection."""
        return self._client_id

    @property
    def connection_time(self) -> Optional[datetime]:
        """Time when connection was established."""
        return self._connection_time

    @property
    def managed_accounts(self) -> List[str]:
        """List of managed accounts (empty if not connected)."""
        if not self.is_connected:
            return []
        return self._broker.managed_accounts()

    def connect(self, timeout: int = 10, readonly: bool = False) -> None:
        """
        Connect to IBKR Gateway/TWS.

        Args:
            timeout: Connection timeout in seconds.
            readonly: If True, connect in read-only mode (no order submission).

        Raises:
            ConnectionError: If connection fails.
        """
        if self.is_connected:
            logger.debug("Already connected to IBKR")
            return

        log_with_context(
            logger,
            logging.INFO,
            "Connecting to IBKR Gateway",
            operation="connect",
            host=self._host,
            port=self._port,
            mode=self._mode.upper(),
            client_id=self._client_id,
            timeout=timeout,
            readonly=readonly,
        )

        try:
            import time

            start_time = time.time()

            self._ib.connect(
                host=self._host,
                port=self._port,
                clientId=self._client_id,
                timeout=timeout,
                readonly=readonly,
            )

            if not self._ib.isConnected():
                raise ConnectionError("Connection returned but isConnected() is False")

            self._connected = True
            self._connection_time = datetime.now()

            # Log success with account info
            accounts = self._broker.managed_accounts()
            server_time = self._broker.request_current_time()

            elapsed_ms = (time.time() - start_time) * 1000

            log_with_context(
                logger,
                logging.INFO,
                "Connected to IBKR Gateway successfully",
                operation="connect",
                status="success",
                accounts=accounts if accounts else [],
                server_time=str(server_time),
                elapsed_ms=round(elapsed_ms, 2),
            )

            # Record metrics
            elapsed_seconds = elapsed_ms / 1000
            record_ibkr_operation("connect", "success", elapsed_seconds)
            set_connection_status(self._mode, connected=True)

        except ConnectionRefusedError as e:
            msg = f"Connection refused at {self._host}:{self._port}. Is IBKR Gateway/TWS running?"
            log_with_context(
                logger,
                logging.ERROR,
                "IBKR connection refused",
                operation="connect",
                status="error",
                error_type="ConnectionRefusedError",
                host=self._host,
                port=self._port,
            )
            elapsed_seconds = time.time() - start_time
            record_ibkr_operation("connect", "error", elapsed_seconds)
            set_connection_status(self._mode, connected=False)
            raise ConnectionError(msg) from e
        except TimeoutError as e:
            msg = f"Connection timeout at {self._host}:{self._port}"
            log_with_context(
                logger,
                logging.ERROR,
                "IBKR connection timeout",
                operation="connect",
                status="error",
                error_type="TimeoutError",
                host=self._host,
                port=self._port,
                timeout=timeout,
            )
            elapsed_seconds = time.time() - start_time
            record_ibkr_operation("connect", "timeout", elapsed_seconds)
            set_connection_status(self._mode, connected=False)
            raise ConnectionError(msg) from e
        except Exception as e:
            msg = f"Failed to connect to IBKR: {type(e).__name__}: {e}"
            log_with_context(
                logger,
                logging.ERROR,
                "IBKR connection failed",
                operation="connect",
                status="error",
                error_type=type(e).__name__,
                error=str(e),
            )
            elapsed_seconds = time.time() - start_time
            record_ibkr_operation("connect", "error", elapsed_seconds)
            set_connection_status(self._mode, connected=False)
            raise ConnectionError(msg) from e

    def disconnect(self) -> None:
        """
        Disconnect from IBKR Gateway/TWS.

        Safe to call even if not connected.
        """
        if self._ib.isConnected():
            log_with_context(
                logger,
                logging.INFO,
                "Disconnecting from IBKR Gateway",
                operation="disconnect",
            )
            self._ib.disconnect()
            log_with_context(
                logger,
                logging.INFO,
                "Disconnected from IBKR Gateway",
                operation="disconnect",
                status="success",
            )

            # Record disconnection metric
            set_connection_status(self._mode, connected=False)

        self._connected = False
        self._connection_time = None

    def ensure_connected(self, timeout: int = 10) -> None:
        """
        Ensure connection is active, reconnecting if necessary.

        Args:
            timeout: Connection timeout in seconds.

        Raises:
            ConnectionError: If connection fails.
        """
        if self.is_connected:
            return

        logger.info("Connection lost or not established. Reconnecting...")
        self.connect(timeout=timeout)

    def get_server_time(self, timeout_s: Optional[float] = None) -> datetime:
        """
        Get current server time from IBKR.

        Args:
            timeout_s: Optional timeout in seconds for the request.

        Returns:
            Server time as datetime.

        Raises:
            ConnectionError: If not connected.
        """
        if not self.is_connected:
            raise ConnectionError("Not connected to IBKR")

        if timeout_s is None:
            return self._broker.request_current_time()

        loop = asyncio.get_event_loop()

        async def _get_time():
            return await asyncio.wait_for(
                self._broker.request_current_time_async(),
                timeout=timeout_s,
            )

        try:
            return loop.run_until_complete(_get_time())
        except asyncio.TimeoutError as e:
            raise ConnectionError(f"Server time request timed out after {timeout_s}s") from e

    def __enter__(self) -> "IBKRClient":
        """Context manager entry - connect."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - disconnect."""
        self.disconnect()

    def __repr__(self) -> str:
        status = "connected" if self.is_connected else "disconnected"
        return f"IBKRClient({self._host}:{self._port}, mode={self._mode}, {status})"


# Convenience function to create a client with default config
def create_client(
    mode: Optional[str] = None,
    client_id: Optional[int] = None,
) -> IBKRClient:
    """
    Create an IBKRClient with the specified parameters.

    Args:
        mode: Trading mode ('paper' or 'live'). Defaults to config.
        client_id: Client ID override. Defaults to config based on mode.

    Returns:
        Configured IBKRClient instance (not connected).
    """
    return IBKRClient(mode=mode, client_id=client_id)
