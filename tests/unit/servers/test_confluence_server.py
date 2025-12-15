"""Unit tests for the Confluence FastMCP server with local sync tools."""

import json
import logging
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import Client, FastMCP
from fastmcp.client import FastMCPTransport

from src.mcp_atlassian.confluence import ConfluenceFetcher
from src.mcp_atlassian.confluence.config import ConfluenceConfig
from src.mcp_atlassian.models.confluence.page import (
    ConfluencePage,
    ConfluenceSpace,
    ConfluenceVersion,
)
from src.mcp_atlassian.servers.context import MainAppContext
from src.mcp_atlassian.servers.main import AtlassianMCP

logger = logging.getLogger(__name__)


@pytest.fixture
def mock_confluence_fetcher():
    """Create a mocked ConfluenceFetcher instance for testing."""
    mock_fetcher = MagicMock(spec=ConfluenceFetcher)

    # Mock space
    mock_space = MagicMock(spec=ConfluenceSpace)
    mock_space.key = "TEST"
    mock_space.name = "Test Space"

    # Mock version
    mock_version = MagicMock(spec=ConfluenceVersion)
    mock_version.number = 1

    # Mock page for various methods
    mock_page = MagicMock(spec=ConfluencePage)
    mock_page.id = "123456"
    mock_page.title = "Test Page Mock Title"
    mock_page.url = "https://example.atlassian.net/wiki/spaces/TEST/pages/123456/Test+Page"
    mock_page.content = "<p>This is test page content</p>"
    mock_page.space = mock_space
    mock_page.version = mock_version
    mock_page.to_simplified_dict.return_value = {
        "id": "123456",
        "title": "Test Page Mock Title",
        "url": "https://example.atlassian.net/wiki/spaces/TEST/pages/123456/Test+Page",
        "content": "<p>This is test page content</p>",
    }

    # Mock ancestor
    mock_ancestor = MagicMock()
    mock_ancestor.id = "111111"

    # Set up mock responses for each method
    mock_fetcher.search.return_value = [mock_page]
    mock_fetcher.search_all.return_value = [mock_page]  # Used by incremental sync
    mock_fetcher.get_page_content.return_value = mock_page
    mock_fetcher.get_page_ancestors.return_value = [mock_ancestor]
    mock_fetcher.update_page.return_value = mock_page

    # Mock for full sync - returns raw page dicts
    mock_fetcher.get_all_space_pages_with_content.return_value = [{
        "id": "123456",
        "title": "Test Page Mock Title",
        "body": {"storage": {"value": "<p>This is test page content</p>"}},
        "version": {"number": 1},
        "ancestors": [{"id": "111111"}],
        "_links": {"webui": "/spaces/TEST/pages/123456/Test+Page"},
        "space": {"key": "TEST", "name": "Test Space"},
    }]

    # Mock config for URL building
    mock_config = MagicMock()
    mock_config.url = "https://example.atlassian.net/wiki"
    mock_fetcher.config = mock_config

    return mock_fetcher


@pytest.fixture
def mock_base_confluence_config():
    """Create a mock base ConfluenceConfig for MainAppContext using basic auth."""
    return ConfluenceConfig(
        url="https://mock.atlassian.net/wiki",
        auth_type="basic",
        username="test_user",
        api_token="test_token",
    )


@pytest.fixture
def test_confluence_mcp(mock_confluence_fetcher, mock_base_confluence_config):
    """Create a test FastMCP instance with standard configuration."""

    # Import and register tool functions (as they are in confluence.py)
    from src.mcp_atlassian.servers.confluence import (
        push_page_update,
        read_page,
        sync_space,
    )

    @asynccontextmanager
    async def test_lifespan(app: FastMCP) -> AsyncGenerator[MainAppContext, None]:
        try:
            yield MainAppContext(
                full_confluence_config=mock_base_confluence_config, read_only=False
            )
        finally:
            pass

    test_mcp = AtlassianMCP(
        "TestConfluence",
        description="Test Confluence MCP Server",
        lifespan=test_lifespan,
    )

    # Create and configure the sub-MCP for Confluence tools
    confluence_sub_mcp = FastMCP(name="TestConfluenceSubMCP")
    confluence_sub_mcp.tool()(sync_space)
    confluence_sub_mcp.tool()(read_page)
    confluence_sub_mcp.tool()(push_page_update)

    test_mcp.mount("confluence", confluence_sub_mcp)

    return test_mcp


@pytest.fixture
async def client(test_confluence_mcp, mock_confluence_fetcher, tmp_path, monkeypatch):
    """Create a FastMCP client with mocked Confluence fetcher."""
    # Use temporary directory for storage
    monkeypatch.chdir(tmp_path)

    # Patch all modules that import get_confluence_fetcher
    with (
        patch(
            "src.mcp_atlassian.servers.confluence.sync.get_confluence_fetcher",
            AsyncMock(return_value=mock_confluence_fetcher),
        ),
        patch(
            "src.mcp_atlassian.servers.confluence.pages.get_confluence_fetcher",
            AsyncMock(return_value=mock_confluence_fetcher),
        ),
        patch(
            "src.mcp_atlassian.servers.confluence.spaces.get_confluence_fetcher",
            AsyncMock(return_value=mock_confluence_fetcher),
        ),
        patch(
            "src.mcp_atlassian.servers.confluence.comments.get_confluence_fetcher",
            AsyncMock(return_value=mock_confluence_fetcher),
        ),
        patch(
            "src.mcp_atlassian.servers.confluence.attachments.get_confluence_fetcher",
            AsyncMock(return_value=mock_confluence_fetcher),
        ),
    ):
        client_instance = Client(transport=FastMCPTransport(test_confluence_mcp))
        async with client_instance as connected_client:
            yield connected_client


@pytest.mark.anyio
async def test_sync_space(client, mock_confluence_fetcher, tmp_path):
    """Test the sync_space tool with basic space key."""
    response = await client.call_tool("confluence_sync_space", {"space_key": "TEST"})

    # Full sync (first sync) uses bulk fetch, not search
    mock_confluence_fetcher.get_all_space_pages_with_content.assert_called_once_with("TEST")

    result_data = json.loads(response[0].text)
    assert result_data["success"] is True
    assert result_data["space_key"] == "TEST"
    assert result_data["pages_synced"] == 1
    assert result_data["sync_type"] == "full"  # First sync is always full

    # Verify files were created
    storage_path = tmp_path / ".better-confluence-mcp" / "TEST"
    assert storage_path.exists()


@pytest.mark.anyio
async def test_sync_space_empty(client, mock_confluence_fetcher):
    """Test sync_space with no pages found."""
    mock_confluence_fetcher.get_all_space_pages_with_content.return_value = []

    response = await client.call_tool(
        "confluence_sync_space", {"space_key": "EMPTY"}
    )

    result_data = json.loads(response[0].text)
    assert "error" in result_data
    assert "No pages found" in result_data["error"]


@pytest.mark.anyio
async def test_read_page(client, mock_confluence_fetcher, tmp_path):
    """Test the read_page tool."""
    response = await client.call_tool("confluence_read_page", {"page_ids": "123456"})

    # read_page uses CQL search_all to get page info
    mock_confluence_fetcher.search_all.assert_called()

    result_data = json.loads(response[0].text)
    assert result_data["success"] is True
    assert result_data["page_id"] == "123456"
    assert result_data["title"] == "Test Page Mock Title"
    assert result_data["space_key"] == "TEST"
    assert "local_path" in result_data

    # Verify file was created
    local_path = tmp_path / result_data["local_path"]
    assert local_path.exists()


@pytest.mark.anyio
async def test_read_page_not_found(client, mock_confluence_fetcher):
    """Test read_page when page doesn't exist."""
    mock_confluence_fetcher.search_all.return_value = []

    response = await client.call_tool("confluence_read_page", {"page_ids": "nonexistent"})

    result_data = json.loads(response[0].text)
    assert "error" in result_data
    assert "not found" in result_data["error"].lower()


@pytest.mark.anyio
async def test_push_page_update(client, mock_confluence_fetcher, tmp_path):
    """Test push_page_update after syncing a page."""
    # First sync the page
    await client.call_tool("confluence_read_page", {"page_ids": "123456"})

    # Now update it
    response = await client.call_tool(
        "confluence_push_page_update",
        {"page_ids": "123456", "revision_message": "Test update"},
    )

    result_data = json.loads(response[0].text)
    # Response is now a pages array
    assert result_data["success_count"] == 1
    assert result_data["pages"][0]["success"] is True
    assert result_data["pages"][0]["page_id"] == "123456"
    assert result_data["revision_message"] == "Test update"


@pytest.mark.anyio
async def test_push_page_update_not_synced(client):
    """Test push_page_update when page is not in local storage."""
    response = await client.call_tool(
        "confluence_push_page_update",
        {"page_ids": "nonexistent", "revision_message": "Test update"},
    )

    result_data = json.loads(response[0].text)
    assert result_data["pages"][0]["error"]
    assert "not found in local storage" in result_data["pages"][0]["error"]


@pytest.mark.anyio
async def test_push_page_update_version_mismatch(client, mock_confluence_fetcher, tmp_path):
    """Test push_page_update when version has changed in Confluence."""
    # First sync the page (version 1)
    await client.call_tool("confluence_read_page", {"page_ids": "123456"})

    # Simulate Confluence having a newer version (version 2)
    mock_version_v2 = MagicMock(spec=ConfluenceVersion)
    mock_version_v2.number = 2

    mock_page_v2 = MagicMock(spec=ConfluencePage)
    mock_page_v2.id = "123456"
    mock_page_v2.title = "Test Page Mock Title - Updated"
    mock_page_v2.url = "https://example.atlassian.net/wiki/spaces/TEST/pages/123456/Test+Page"
    mock_page_v2.content = "<p>Updated content by someone else</p>"
    mock_page_v2.space = mock_confluence_fetcher.get_page_content.return_value.space
    mock_page_v2.version = mock_version_v2

    mock_confluence_fetcher.get_page_content.return_value = mock_page_v2

    # Try to update - should fail due to version mismatch
    response = await client.call_tool(
        "confluence_push_page_update",
        {"page_ids": "123456", "revision_message": "My update"},
    )

    result_data = json.loads(response[0].text)
    assert result_data["pages"][0]["error"]
    assert "mismatch" in result_data["pages"][0]["error"].lower()


@pytest.mark.anyio
async def test_sync_space_incremental(client, mock_confluence_fetcher, tmp_path):
    """Test incremental sync after initial full sync."""
    # First sync (full)
    await client.call_tool("confluence_sync_space", {"space_key": "TEST"})

    # Reset mock
    mock_confluence_fetcher.search.reset_mock()

    # Second sync (incremental)
    response = await client.call_tool(
        "confluence_sync_space", {"space_key": "TEST", "full_sync": False}
    )

    result_data = json.loads(response[0].text)
    assert result_data["success"] is True
    # Should use incremental CQL query (contains lastModified)


@pytest.mark.anyio
async def test_sync_space_auto_full_sync(client, mock_confluence_fetcher, tmp_path):
    """Test that auto full sync triggers after 3 days."""
    from src.mcp_atlassian import local_storage

    # First sync
    await client.call_tool("confluence_sync_space", {"space_key": "TEST"})

    # Modify the metadata to have old last_synced time
    metadata = local_storage.load_space_metadata("TEST")
    old_time = datetime.now(timezone.utc) - timedelta(days=4)
    metadata.last_synced = old_time.isoformat()
    local_storage.save_space_metadata(metadata)

    # Reset mock
    mock_confluence_fetcher.search.reset_mock()

    # Next sync should be auto full
    response = await client.call_tool(
        "confluence_sync_space", {"space_key": "TEST", "full_sync": False}
    )

    result_data = json.loads(response[0].text)
    assert result_data["success"] is True
    assert result_data["sync_type"] == "auto_full"
    assert "auto_full_sync_reason" in result_data
