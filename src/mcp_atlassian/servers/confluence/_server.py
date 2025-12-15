"""Confluence MCP server instance and shared utilities."""

import asyncio
import logging

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# FastMCP server instance
confluence_mcp = FastMCP(
    name="Confluence MCP Service",
    description="Provides tools for syncing and editing Confluence spaces locally.",
)

# Auto full sync interval (3 days)
AUTO_FULL_SYNC_DAYS = 3

# Per-space locks to prevent concurrent sync operations on the same space
_space_locks: dict[str, asyncio.Lock] = {}


def get_space_lock(space_key: str) -> asyncio.Lock:
    """Get or create a lock for a specific space.

    This prevents race conditions when multiple tools try to sync
    the same space concurrently (e.g., parallel read_page calls).
    """
    if space_key not in _space_locks:
        _space_locks[space_key] = asyncio.Lock()
    return _space_locks[space_key]
