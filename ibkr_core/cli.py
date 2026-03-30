"""
IBKR Gateway Command-Line Interface

Provides user-friendly commands for common operations:
- healthcheck: Check IBKR Gateway connection
- demo: Run interactive demo
- start-api: Launch FastAPI server

Usage:
    ibkr-gateway --help
    ibkr-gateway healthcheck
    ibkr-gateway demo
    ibkr-gateway start-api
"""

import subprocess
import sys
from datetime import datetime
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ibkr_core import demo as demo_module
from ibkr_core.broker import get_broker_adapter
from ibkr_core.client import ConnectionError, create_client
from ibkr_core.config import InvalidConfigError, get_config, reset_config

# Initialize Typer app
app = typer.Typer(
    name="ibkr-gateway",
    help="IBKR Gateway CLI - Market data, account status, and order management",
    add_completion=False,
)

# Rich console for beautiful output
console = Console()


# =============================================================================
# Context and Configuration
# =============================================================================


class CLIContext:
    """Shared context for CLI commands."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        paper: bool = False,
        live: bool = False,
    ):
        self.host = host
        self.port = port
        self.paper = paper
        self.live = live


# Global context
ctx_obj = {}


@app.callback()
def main(
    ctx: typer.Context,
    host: Optional[str] = typer.Option(
        None, "--host", "-h", help="IBKR Gateway host (overrides env)"
    ),
    port: Optional[int] = typer.Option(
        None, "--port", "-p", help="IBKR Gateway port (overrides env)"
    ),
    paper: bool = typer.Option(False, "--paper", help="Force paper trading mode"),
    live: bool = typer.Option(False, "--live", help="Force live trading mode (⚠️  DANGEROUS)"),
):
    """
    IBKR Gateway CLI tool for market data, account status, and order management.

    Run 'ibkr-gateway COMMAND --help' for help on a specific command.
    """
    # Validate mutually exclusive options
    if paper and live:
        console.print("[red]Error: --paper and --live are mutually exclusive[/red]")
        raise typer.Exit(1)

    # Store context
    ctx_obj["config"] = CLIContext(host=host, port=port, paper=paper, live=live)

    # Apply overrides to environment
    if paper or live:
        # Reset config to pick up environment changes
        reset_config()
        import os

        mode = "paper" if paper else "live"
        os.environ["TRADING_MODE"] = mode


# =============================================================================
# Commands
# =============================================================================


@app.command()
def healthcheck(
    ctx: typer.Context,
):
    """
    Check IBKR Gateway connection and display status.

    Verifies connection to IBKR Gateway, displays server time and managed accounts.
    Returns exit code 0 on success, 1 on failure.

    Examples:
        ibkr-gateway healthcheck
        ibkr-gateway --paper healthcheck
        ibkr-gateway --host localhost --port 4002 healthcheck
    """
    console.print("\n[bold cyan]IBKR Gateway Health Check[/bold cyan]\n")

    try:
        # Get config
        config = get_config()

        # Apply CLI overrides
        cli_ctx = ctx_obj.get("config", CLIContext())
        if cli_ctx.host:
            config.ibkr_gateway_host = cli_ctx.host
        if cli_ctx.port:
            if cli_ctx.paper or config.trading_mode == "paper":
                config.paper_gateway_port = cli_ctx.port
            else:
                config.live_gateway_port = cli_ctx.port

        # Display configuration
        mode = config.trading_mode
        mode_color = "green" if mode == "paper" else "red"

        config_table = Table(show_header=False, box=box.SIMPLE)
        config_table.add_row("Mode", f"[{mode_color}]{mode.upper()}[/{mode_color}]")
        config_table.add_row("Host", config.ibkr_gateway_host)

        if mode == "paper":
            config_table.add_row("Port", str(config.paper_gateway_port))
            config_table.add_row("Client ID", str(config.paper_client_id))
        else:
            config_table.add_row("Port", str(config.live_gateway_port))
            config_table.add_row("Client ID", str(config.live_client_id))

        console.print(config_table)
        console.print()

        # Attempt connection
        console.print("[yellow]Connecting to IBKR Gateway...[/yellow]")

        client = create_client(mode=mode)
        broker = get_broker_adapter(client)

        # Get server time
        server_time = broker.request_current_time()
        server_dt = (
            server_time
            if isinstance(server_time, datetime)
            else datetime.fromtimestamp(server_time)
        )

        # Get managed accounts
        accounts = broker.managed_accounts()

        # Success!
        console.print("[green]✓ Connected successfully![/green]\n")

        # Display results
        result_table = Table(show_header=False, box=box.SIMPLE)
        result_table.add_row("Server Time", server_dt.strftime("%Y-%m-%d %H:%M:%S"))
        if accounts:
            result_table.add_row("Managed Accounts", ", ".join(accounts))

        console.print(result_table)
        console.print()

        # Cleanup
        client.disconnect()

        console.print("[green]✓ Health check passed[/green]\n")
        raise typer.Exit(0)

    except ConnectionError as e:
        console.print(f"[red]✗ Connection failed: {e}[/red]\n")
        console.print("[yellow]Troubleshooting:[/yellow]")
        console.print("  • Ensure IBKR Gateway or TWS is running")
        console.print("  • Check that API connections are enabled in settings")
        console.print("  • Verify the port matches your configuration")
        console.print()
        raise typer.Exit(1)

    except InvalidConfigError as e:
        console.print(f"[red]✗ Configuration error: {e}[/red]\n")
        raise typer.Exit(1)

    except typer.Exit:
        # Re-raise typer.Exit without catching it as a general exception
        raise

    except Exception as e:
        console.print(f"[red]✗ Unexpected error: {e}[/red]\n")
        raise typer.Exit(1)


@app.command()
def demo(
    ctx: typer.Context,
):
    """
    Run interactive demo showcasing system capabilities.

    Demonstrates market data fetching and account status queries.
    Requires IBKR Gateway running in paper mode.

    The demo shows:
      • Real-time quote for AAPL (stock)
      • Historical bars for SPY (ETF)
      • Account summary and positions

    Examples:
        ibkr-gateway demo
        ibkr-gateway --paper demo
    """
    # Apply CLI context overrides if needed
    cli_ctx = ctx_obj.get("config", CLIContext())

    # If live mode was requested, warn user
    if cli_ctx.live:
        console.print(
            Panel(
                "[red]Demo only runs in paper trading mode for safety.[/red]\n"
                "Forcing paper mode...",
                title="⚠️  Safety Override",
                border_style="red",
            )
        )
        console.print()
        # Force paper mode
        import os

        os.environ["TRADING_MODE"] = "paper"
        reset_config()

    # Run the demo
    exit_code = demo_module.main()
    raise typer.Exit(exit_code)


@app.command(name="start-api")
def start_api(
    port: int | None = typer.Option(None, "--port", "-p", help="API server port"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (development)"),
    host: str = typer.Option(
        None, "--host", help="Bind host (default: from config.json)"
    ),
):
    """
    Start the FastAPI REST server.

    Launches the REST API server for HTTP access to IBKR Gateway.
    The API provides endpoints for market data, account status, and orders.

    Examples:
        ibkr-gateway start-api
        ibkr-gateway start-api --port 8080
        ibkr-gateway start-api --reload  # Development mode with auto-reload

    The server will be available at http://localhost:8000 (or your specified port).
    API documentation is available at http://localhost:8000/docs
    """
    config = get_config()

    if host is None:
        host = config.api_bind_host
    if port is None:
        port = config.api_port

    console.print("\n[bold cyan]Starting IBKR Gateway API Server[/bold cyan]\n")

    # Display configuration
    config_table = Table(show_header=False, box=box.SIMPLE)
    config_table.add_row("Host", host)
    config_table.add_row("Port", str(port))
    config_table.add_row("Auto-reload", "✓" if reload else "✗")

    console.print(config_table)
    console.print()

    # Display endpoints
    console.print("[bold]Available Endpoints:[/bold]")
    console.print(
        f"  • API Documentation: [link=http://{host}:{port}/docs]http://{host}:{port}/docs[/link]"
    )
    console.print(f"  • Health Check: http://{host}:{port}/health")
    console.print(f"  • OpenAPI Schema: http://{host}:{port}/openapi.json")
    console.print()

    console.print("[yellow]Starting server...[/yellow]")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    # Build uvicorn command
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "api.server:app",
        "--host",
        host,
        "--port",
        str(port),
    ]

    if reload:
        cmd.append("--reload")

    # Run uvicorn
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        console.print("\n[yellow]Server stopped by user[/yellow]")
        raise typer.Exit(0)
    except subprocess.CalledProcessError as e:
        console.print(f"\n[red]Server failed: {e}[/red]")
        raise typer.Exit(1)
    except FileNotFoundError:
        console.print("\n[red]Error: uvicorn not found. Install with: poetry install[/red]")
        raise typer.Exit(1)


@app.command()
def version():
    """Display version information."""
    console.print("\n[bold cyan]IBKR Gateway[/bold cyan]")
    console.print("Version: 0.1.0")
    console.print("Python: " + sys.version.split()[0])
    console.print()


def cli_main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    cli_main()
