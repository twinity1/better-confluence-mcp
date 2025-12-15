"""Confluence comment and user tools - get_comments, add_comment, search_user."""

import json
import logging
from typing import Annotated

from fastmcp import Context
from pydantic import Field

from mcp_atlassian.servers.dependencies import get_confluence_fetcher
from mcp_atlassian.utils.decorators import check_write_access

from ._server import confluence_mcp

logger = logging.getLogger(__name__)


@confluence_mcp.tool(tags={"confluence", "read"})
async def get_comments(
    ctx: Context,
    page_id: Annotated[
        str,
        Field(description="Confluence page ID"),
    ],
) -> str:
    """Get comments for a specific Confluence page.

    Args:
        ctx: The FastMCP context.
        page_id: Confluence page ID.

    Returns:
        JSON string with list of comments.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    try:
        comments = confluence_fetcher.get_page_comments(page_id, return_markdown=True)

        comment_list = []
        for comment in comments:
            comment_list.append({
                "id": comment.id,
                "author": comment.author.display_name if comment.author else None,
                "created": comment.created.isoformat() if comment.created else None,
                "content": comment.body,
            })

        return json.dumps(
            {"success": True, "page_id": page_id, "total": len(comment_list), "comments": comment_list},
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        logger.error(f"Failed to get comments for page {page_id}: {e}")
        return json.dumps(
            {"error": f"Failed to get comments: {str(e)}"},
            indent=2,
            ensure_ascii=False,
        )


@confluence_mcp.tool(tags={"confluence", "write"})
@check_write_access
async def add_comment(
    ctx: Context,
    page_id: Annotated[
        str,
        Field(description="Confluence page ID"),
    ],
    content: Annotated[
        str,
        Field(description="Comment content in Markdown format"),
    ],
) -> str:
    """Add a comment to a Confluence page.

    Args:
        ctx: The FastMCP context.
        page_id: The ID of the page to add a comment to.
        content: The comment content in Markdown format.

    Returns:
        JSON string with the created comment.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    try:
        comment = confluence_fetcher.add_comment(page_id, content)

        if not comment:
            return json.dumps(
                {"error": "Failed to add comment"},
                indent=2,
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "success": True,
                "comment": {
                    "id": comment.id,
                    "author": comment.author.display_name if comment.author else None,
                    "created": comment.created.isoformat() if comment.created else None,
                    "content": comment.body,
                },
            },
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        logger.error(f"Failed to add comment to page {page_id}: {e}")
        return json.dumps(
            {"error": f"Failed to add comment: {str(e)}"},
            indent=2,
            ensure_ascii=False,
        )


@confluence_mcp.tool(tags={"confluence", "read"})
async def search_user(
    ctx: Context,
    query: Annotated[
        str,
        Field(
            description="CQL query for user search. Example: 'user.fullname ~ \"John\"'"
        ),
    ],
    limit: Annotated[
        int,
        Field(description="Maximum number of results (1-50)", ge=1, le=50, default=10),
    ] = 10,
) -> str:
    """Search Confluence users using CQL.

    Args:
        ctx: The FastMCP context.
        query: CQL query string for user search.
        limit: Maximum number of results (1-50).

    Returns:
        JSON string with list of matching users.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    try:
        results = confluence_fetcher.search_user(cql=query, limit=limit)

        users = []
        for user in results:
            users.append({
                "account_id": user.account_id,
                "display_name": user.display_name,
                "email": user.email,
            })

        return json.dumps(
            {"success": True, "total": len(users), "users": users},
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        logger.error(f"User search failed: {e}")
        return json.dumps(
            {"error": f"User search failed: {str(e)}"},
            indent=2,
            ensure_ascii=False,
        )
