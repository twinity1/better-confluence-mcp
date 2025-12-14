"""Dependency providers for ConfluenceFetcher with context awareness.

Provides get_confluence_fetcher for use in tool functions.
"""

from __future__ import annotations

import logging

from fastmcp import Context

from mcp_atlassian.confluence import ConfluenceFetcher
from mcp_atlassian.servers.context import MainAppContext

logger = logging.getLogger("mcp-atlassian.servers.dependencies")


async def get_confluence_fetcher(ctx: Context) -> ConfluenceFetcher:
    """Returns a ConfluenceFetcher instance from the global configuration.

    Args:
        ctx: The FastMCP context.

    Returns:
        ConfluenceFetcher instance from the global config.

    Raises:
        ValueError: If Confluence is not configured.
    """
    logger.debug(f"get_confluence_fetcher: ENTERED. Context ID: {id(ctx)}")

    lifespan_ctx_dict = ctx.request_context.lifespan_context  # type: ignore
    app_lifespan_ctx: MainAppContext | None = (
        lifespan_ctx_dict.get("app_lifespan_context")
        if isinstance(lifespan_ctx_dict, dict)
        else None
    )

    if app_lifespan_ctx and app_lifespan_ctx.full_confluence_config:
        logger.debug(
            "get_confluence_fetcher: Using global ConfluenceFetcher from lifespan_context. "
            f"Global config auth_type: {app_lifespan_ctx.full_confluence_config.auth_type}"
        )
        return ConfluenceFetcher(config=app_lifespan_ctx.full_confluence_config)

    logger.error("Confluence configuration could not be resolved.")
    raise ValueError(
        "Confluence client (fetcher) not available. Ensure server is configured correctly."
    )
