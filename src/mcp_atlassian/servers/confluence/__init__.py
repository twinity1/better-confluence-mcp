"""Confluence MCP server package.

This package contains the Confluence MCP server and tools organized into modules:
- _server.py: FastMCP instance and shared utilities
- sync.py: Space sync tools (sync_space)
- pages.py: Page tools (read_page, create_page, push_page_update)
- spaces.py: Space tools (get_spaces)
- comments.py: Comment and user tools (get_comments, add_comment, search_user)
- attachments.py: Attachment tools (download_attachments, upload_attachment, create_mermaid_diagram)
"""

# Import all tool modules to register them with confluence_mcp
from . import attachments, comments, pages, spaces, sync

# Export the MCP server instance and constants
from ._server import AUTO_FULL_SYNC_DAYS, confluence_mcp

# Re-export get_confluence_fetcher for backward compatibility (used by tests)
from mcp_atlassian.servers.dependencies import get_confluence_fetcher

# Export tool functions for backward compatibility (used by tests)
from .attachments import create_mermaid_diagram, download_attachments, upload_attachment
from .comments import add_comment, get_comments, search_user
from .pages import create_page, push_page_update, read_page
from .spaces import get_spaces
from .sync import sync_space, sync_space_impl

__all__ = [
    "confluence_mcp",
    "AUTO_FULL_SYNC_DAYS",
    "get_confluence_fetcher",
    # Sync tools
    "sync_space",
    "sync_space_impl",
    # Page tools
    "read_page",
    "create_page",
    "push_page_update",
    # Space tools
    "get_spaces",
    # Comment/user tools
    "get_comments",
    "add_comment",
    "search_user",
    # Attachment tools
    "download_attachments",
    "upload_attachment",
    "create_mermaid_diagram",
]
