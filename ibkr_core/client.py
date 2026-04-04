"""
IBKR client wrapper for connecting to a running Interactive Brokers Gateway/TWS.

The MCP repo assumes the broker process is already running. This wrapper keeps a
stable connection and exposes a broker adapter for account, market-data, and
order flows.
"""

import asyncio
import logging
from datetime import datetime
from typing import List, Optional

from ib_insync import IB

from ibkr_core.broker import BrokerAdapter, IBInsyncBrokerAdapter
from ibkr_core.config import Config, get_config
from ibkr_core.logging_config import log_with_context

logger = logging.getLogger(__name__)


class ConnectionError(Exception):
    """Raised when connection to IBKR fails."""


class IBKRClient:
    """Wrapper around the active broker backend for IBKR Gateway/TWS connections."""

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
            mode: Optional execution label ('paper' or 'live') retained for safety status.
            client_id: Optional client ID override for the connection.
        """
        self._config = config or get_config()
        self._mode = mode.lower() if mode else self._config.trading_mode
        if self._mode not in ("paper", "live"):
            raise ValueError(f"Invalid mode: {self._mode}. Must be 'paper' or 'live'.")

        self._host = self._config.ibkr_host
        self._port = self._config.ibkr_port
        self._client_id = client_id if client_id is not None else self._config.ibkr_client_id
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
        """Current execution label (paper or live)."""
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
        """Connect to IBKR Gateway/TWS."""
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
        except ConnectionRefusedError as exc:
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
            raise ConnectionError(msg) from exc
        except TimeoutError as exc:
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
            raise ConnectionError(msg) from exc
        except Exception as exc:
            msg = f"Failed to connect to IBKR: {type(exc).__name__}: {exc}"
            log_with_context(
                logger,
                logging.ERROR,
                "IBKR connection failed",
                operation="connect",
                status="error",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise ConnectionError(msg) from exc

    def disconnect(self) -> None:
        """Disconnect from IBKR Gateway/TWS."""
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

        self._connected = False
        self._connection_time = None

    def ensure_connected(self, timeout: int = 10) -> None:
        """Ensure connection is active, reconnecting if necessary."""
        if self.is_connected:
            return

        logger.info("Connection lost or not established. Reconnecting...")
        self.connect(timeout=timeout)

    def get_server_time(self, timeout_s: Optional[float] = None) -> datetime:
        """Get current server time from IBKR."""
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
        except asyncio.TimeoutError as exc:
            raise ConnectionError(f"Server time request timed out after {timeout_s}s") from exc

    def __enter__(self) -> "IBKRClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()

    def __repr__(self) -> str:
        status = "connected" if self.is_connected else "disconnected"
        return f"IBKRClient({self._host}:{self._port}, mode={self._mode}, {status})"


def create_client(
    mode: Optional[str] = None,
    client_id: Optional[int] = None,
) -> IBKRClient:
    """Create an IBKRClient with optional mode label and client-id override."""
    return IBKRClient(mode=mode, client_id=client_id)
