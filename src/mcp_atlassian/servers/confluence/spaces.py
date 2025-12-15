"""Confluence space tools - get_spaces."""

import json
import logging
from typing import Annotated

from fastmcp import Context
from pydantic import Field

from mcp_atlassian.servers.dependencies import get_confluence_fetcher

from ._server import confluence_mcp

logger = logging.getLogger(__name__)


@confluence_mcp.tool(tags={"confluence", "read"})
async def get_spaces(
    ctx: Context,
    limit: Annotated[
        int,
        Field(description="Maximum number of spaces to return", ge=1, le=100, default=25),
    ] = 25,
) -> str:
    """List available Confluence spaces.

    Args:
        ctx: The FastMCP context.
        limit: Maximum number of spaces to return.

    Returns:
        JSON string with list of spaces.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    try:
        result = confluence_fetcher.get_spaces(start=0, limit=limit)
        spaces = []
        for space in result.get("results", []):
            spaces.append({
                "key": space.get("key"),
                "name": space.get("name"),
                "type": space.get("type"),
            })

        return json.dumps(
            {"success": True, "total": len(spaces), "spaces": spaces},
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        logger.error(f"Failed to get spaces: {e}")
        return json.dumps(
            {"error": f"Failed to get spaces: {str(e)}"},
            indent=2,
            ensure_ascii=False,
        )
