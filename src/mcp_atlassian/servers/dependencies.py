"""Dependency providers for ConfluenceFetcher with context awareness.

Provides get_confluence_fetcher for use in tool functions.
"""

from __future__ import annotations

import logging

from fastmcp import Context

from mcp_atlassian.confluence import ConfluenceFetcher
from mcp_atlassian.confluence.config import ConfluenceConfig
from mcp_atlassian.servers.context import MainAppContext

logger = logging.getLogger("mcp-atlassian.servers.dependencies")


def get_transport_from_context(ctx: Context) -> str:
    """Get the transport mode from the lifespan context.

    Returns:
        'stdio' or 'streamable-http'
    """
    lifespan_ctx_dict = ctx.request_context.lifespan_context  # type: ignore
    app_lifespan_ctx: MainAppContext | None = (
        lifespan_ctx_dict.get("app_lifespan_context")
        if isinstance(lifespan_ctx_dict, dict)
        else None
    )
    return getattr(app_lifespan_ctx, "transport", "stdio") if app_lifespan_ctx else "stdio"


async def get_confluence_fetcher(ctx: Context) -> ConfluenceFetcher:
    """Returns a ConfluenceFetcher instance from the global configuration.

    In stdio mode, uses the global config from lifespan context.
    In HTTP mode, creates a per-request fetcher using credentials from headers.

    Args:
        ctx: The FastMCP context.

    Returns:
        ConfluenceFetcher instance.

    Raises:
        ValueError: If Confluence is not configured or auth is missing.
    """
    logger.debug(f"get_confluence_fetcher: ENTERED. Context ID: {id(ctx)}")

    lifespan_ctx_dict = ctx.request_context.lifespan_context  # type: ignore
    app_lifespan_ctx: MainAppContext | None = (
        lifespan_ctx_dict.get("app_lifespan_context")
        if isinstance(lifespan_ctx_dict, dict)
        else None
    )

    # HTTP mode: create per-request fetcher from header credentials
    if app_lifespan_ctx and app_lifespan_ctx.transport == "streamable-http":
        from mcp_atlassian.servers.http_auth import get_request_auth

        request_auth = get_request_auth()
        if not request_auth:
            raise ValueError(
                "No authentication credentials in request headers. "
                "Provide 'Authorization: Basic ...' or "
                "'X-Confluence-Username' + 'X-Confluence-Token' headers."
            )

        if not app_lifespan_ctx.full_confluence_config:
            raise ValueError(
                "Confluence URL not configured. Set CONFLUENCE_URL environment variable."
            )

        # Build per-request config with URL from env + auth from headers
        base_config = app_lifespan_ctx.full_confluence_config
        per_request_config = ConfluenceConfig(
            url=base_config.url,
            auth_type="basic",
            username=request_auth.username,
            api_token=request_auth.api_token,
            ssl_verify=base_config.ssl_verify,
            spaces_filter=base_config.spaces_filter,
            http_proxy=base_config.http_proxy,
            https_proxy=base_config.https_proxy,
            no_proxy=base_config.no_proxy,
            socks_proxy=base_config.socks_proxy,
            custom_headers=base_config.custom_headers,
        )

        logger.debug("get_confluence_fetcher: Using per-request auth from HTTP headers")
        return ConfluenceFetcher(config=per_request_config)

    # stdio mode: use global config
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
