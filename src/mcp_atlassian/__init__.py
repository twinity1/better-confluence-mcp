import asyncio
import logging
import os
import sys
from importlib.metadata import PackageNotFoundError, version

import click
from dotenv import load_dotenv

from mcp_atlassian.utils.env import is_env_truthy
from mcp_atlassian.utils.lifecycle import (
    ensure_clean_exit,
    setup_signal_handlers,
)
from mcp_atlassian.utils.logging import setup_logging

try:
    __version__ = version("mcp-atlassian")
except PackageNotFoundError:
    # package is not installed
    __version__ = "0.0.0"

# Initialize logging with appropriate level
logging_level = logging.WARNING
if is_env_truthy("MCP_VERBOSE"):
    logging_level = logging.DEBUG

# Set up logging to STDOUT if MCP_LOGGING_STDOUT is set to true
logging_stream = sys.stdout if is_env_truthy("MCP_LOGGING_STDOUT") else sys.stderr

# Set up logging using the utility function
logger = setup_logging(logging_level, logging_stream)


@click.version_option(__version__, prog_name="mcp-atlassian")
@click.command()
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Increase verbosity (can be used multiple times)",
)
@click.option(
    "--env-file", type=click.Path(exists=True, dir_okay=False), help="Path to .env file"
)
@click.option(
    "--confluence-url",
    help="Confluence URL (e.g., https://your-domain.atlassian.net/wiki)",
)
@click.option("--confluence-username", help="Confluence username/email")
@click.option("--confluence-token", help="Confluence API token")
@click.option(
    "--confluence-personal-token",
    help="Confluence Personal Access Token (for Confluence Server/Data Center)",
)
@click.option(
    "--confluence-ssl-verify/--no-confluence-ssl-verify",
    default=True,
    help="Verify SSL certificates for Confluence Server/Data Center (default: verify)",
)
@click.option(
    "--confluence-spaces-filter",
    help="Comma-separated list of Confluence space keys to filter search results",
)
@click.option(
    "--read-only",
    is_flag=True,
    help="Run in read-only mode (disables all write operations)",
)
@click.option(
    "--enabled-tools",
    help="Comma-separated list of tools to enable (enables all if not specified)",
)
def main(
    verbose: int,
    env_file: str | None,
    confluence_url: str | None,
    confluence_username: str | None,
    confluence_token: str | None,
    confluence_personal_token: str | None,
    confluence_ssl_verify: bool,
    confluence_spaces_filter: str | None,
    read_only: bool,
    enabled_tools: str | None,
) -> None:
    """MCP Atlassian Server - Confluence functionality for MCP

    Supports both Atlassian Cloud and Confluence Server/Data Center deployments.
    Authentication methods supported:
    - Username and API token (Cloud)
    - Personal Access Token (Server/Data Center)
    """
    # Logging level logic
    if verbose == 1:
        current_logging_level = logging.INFO
    elif verbose >= 2:  # -vv or more
        current_logging_level = logging.DEBUG
    else:
        # Default to DEBUG if MCP_VERY_VERBOSE is set, else INFO if MCP_VERBOSE is set, else WARNING
        if is_env_truthy("MCP_VERY_VERBOSE", "false"):
            current_logging_level = logging.DEBUG
        elif is_env_truthy("MCP_VERBOSE", "false"):
            current_logging_level = logging.INFO
        else:
            current_logging_level = logging.WARNING

    # Set up logging to STDOUT if MCP_LOGGING_STDOUT is set to true
    logging_stream = sys.stdout if is_env_truthy("MCP_LOGGING_STDOUT") else sys.stderr

    global logger
    logger = setup_logging(current_logging_level, logging_stream)
    logger.debug(f"Logging level set to: {logging.getLevelName(current_logging_level)}")
    logger.debug(
        f"Logging stream set to: {'stdout' if logging_stream is sys.stdout else 'stderr'}"
    )

    def was_option_provided(ctx: click.Context, param_name: str) -> bool:
        return (
            ctx.get_parameter_source(param_name)
            != click.core.ParameterSource.DEFAULT_MAP
            and ctx.get_parameter_source(param_name)
            != click.core.ParameterSource.DEFAULT
        )

    if env_file:
        logger.debug(f"Loading environment from file: {env_file}")
        load_dotenv(env_file, override=True)
    else:
        logger.debug(
            "Attempting to load environment from default .env file if it exists"
        )
        load_dotenv(override=True)

    click_ctx = click.get_current_context(silent=True)

    # Set env vars for downstream config
    if click_ctx and was_option_provided(click_ctx, "enabled_tools"):
        os.environ["ENABLED_TOOLS"] = enabled_tools
    if click_ctx and was_option_provided(click_ctx, "confluence_url"):
        os.environ["CONFLUENCE_URL"] = confluence_url
    if click_ctx and was_option_provided(click_ctx, "confluence_username"):
        os.environ["CONFLUENCE_USERNAME"] = confluence_username
    if click_ctx and was_option_provided(click_ctx, "confluence_token"):
        os.environ["CONFLUENCE_API_TOKEN"] = confluence_token
    if click_ctx and was_option_provided(click_ctx, "confluence_personal_token"):
        os.environ["CONFLUENCE_PERSONAL_TOKEN"] = confluence_personal_token
    if click_ctx and was_option_provided(click_ctx, "read_only"):
        os.environ["READ_ONLY_MODE"] = str(read_only).lower()
    if click_ctx and was_option_provided(click_ctx, "confluence_ssl_verify"):
        os.environ["CONFLUENCE_SSL_VERIFY"] = str(confluence_ssl_verify).lower()
    if click_ctx and was_option_provided(click_ctx, "confluence_spaces_filter"):
        os.environ["CONFLUENCE_SPACES_FILTER"] = confluence_spaces_filter

    from mcp_atlassian.servers import main_mcp

    # Set up signal handlers for graceful shutdown
    setup_signal_handlers()

    logger.info("Starting server with STDIO transport.")

    try:
        logger.debug("Starting asyncio event loop...")
        asyncio.run(main_mcp.run_async(transport="stdio"))
    except (KeyboardInterrupt, SystemExit) as e:
        logger.info(f"Server shutdown initiated: {type(e).__name__}")
    except Exception as e:
        logger.error(f"Server encountered an error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        ensure_clean_exit()


__all__ = ["main", "__version__"]

if __name__ == "__main__":
    main()
