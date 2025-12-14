"""
Test file for validating the refactored FastMCP tools with real API data.

This test file connects to real Confluence instances to validate
that our model refactoring works correctly with actual API data.

These tests will be skipped if the required environment variables are not set
or if the --use-real-data flag is not passed to pytest.

To run these tests:
    pytest tests/test_real_api_validation.py --use-real-data

Required environment variables:
    - CONFLUENCE_URL, CONFLUENCE_USERNAME, CONFLUENCE_API_TOKEN
    - CONFLUENCE_TEST_PAGE_ID, CONFLUENCE_TEST_SPACE_KEY
"""

import datetime
import os
import uuid
from collections.abc import Callable, Generator

import pytest
from fastmcp import Client
from fastmcp.client import FastMCPTransport
from mcp.types import TextContent

from mcp_atlassian.confluence import ConfluenceFetcher
from mcp_atlassian.confluence.comments import CommentsMixin as ConfluenceCommentsMixin
from mcp_atlassian.confluence.config import ConfluenceConfig
from mcp_atlassian.confluence.labels import LabelsMixin as ConfluenceLabelsMixin
from mcp_atlassian.confluence.pages import PagesMixin
from mcp_atlassian.confluence.search import SearchMixin as ConfluenceSearchMixin
from mcp_atlassian.models.confluence import (
    ConfluenceComment,
    ConfluenceLabel,
    ConfluencePage,
)
from mcp_atlassian.servers import main_mcp


# Resource tracking for cleanup
class ResourceTracker:
    """Tracks resources created during tests for cleanup."""

    def __init__(self):
        self.confluence_pages: list[str] = []
        self.confluence_comments: list[str] = []

    def add_confluence_page(self, page_id: str) -> None:
        """Track a Confluence page for later cleanup."""
        self.confluence_pages.append(page_id)

    def add_confluence_comment(self, comment_id: str) -> None:
        """Track a Confluence comment for later cleanup."""
        self.confluence_comments.append(comment_id)

    def cleanup(
        self,
        confluence_client: ConfluenceFetcher | None = None,
    ) -> None:
        """Clean up all tracked resources."""
        if confluence_client:
            for comment_id in self.confluence_comments:
                try:
                    confluence_client.delete_comment(comment_id)
                    print(f"Deleted Confluence comment {comment_id}")
                except Exception as e:
                    print(f"Failed to delete Confluence comment {comment_id}: {e}")

            for page_id in self.confluence_pages:
                try:
                    confluence_client.delete_page(page_id)
                    print(f"Deleted Confluence page {page_id}")
                except Exception as e:
                    print(f"Failed to delete Confluence page {page_id}: {e}")


@pytest.fixture
def confluence_config() -> ConfluenceConfig:
    """Create a ConfluenceConfig from environment variables."""
    if not os.environ.get("CONFLUENCE_URL"):
        pytest.skip("CONFLUENCE_URL environment variable not set")
    return ConfluenceConfig.from_env()


@pytest.fixture
def confluence_client(confluence_config: ConfluenceConfig) -> ConfluenceFetcher:
    """Create a ConfluenceFetcher instance."""
    return ConfluenceFetcher(config=confluence_config)


@pytest.fixture
def test_page_id() -> str:
    """Get test Confluence page ID from environment."""
    page_id = os.environ.get("CONFLUENCE_TEST_PAGE_ID")
    if not page_id:
        pytest.skip("CONFLUENCE_TEST_PAGE_ID environment variable not set")
    return page_id


@pytest.fixture
def test_space_key() -> str:
    """Get test Confluence space key from environment."""
    space_key = os.environ.get("CONFLUENCE_TEST_SPACE_KEY")
    if not space_key:
        pytest.skip("CONFLUENCE_TEST_SPACE_KEY environment variable not set")
    return space_key


@pytest.fixture
def resource_tracker() -> Generator[ResourceTracker, None, None]:
    """Create and yield a ResourceTracker that will be used to clean up after tests."""
    tracker = ResourceTracker()
    yield tracker


@pytest.fixture
def cleanup_resources(
    resource_tracker: ResourceTracker,
    confluence_client: ConfluenceFetcher,
) -> Callable[[], None]:
    """Return a function that can be called to clean up resources."""

    def _cleanup():
        resource_tracker.cleanup(confluence_client=confluence_client)

    return _cleanup


# Only use asyncio backend for anyio tests
pytestmark = pytest.mark.anyio(backends=["asyncio"])


@pytest.fixture(scope="class")
async def api_validation_client():
    """Provides a FastMCP client connected to the main server for tool calls."""
    transport = FastMCPTransport(main_mcp)
    client = Client(transport=transport)
    async with client as connected_client:
        yield connected_client


async def call_tool(
    client: Client, tool_name: str, arguments: dict
) -> list[TextContent]:
    """Helper function to call tools via the client."""
    return await client.call_tool(tool_name, arguments)


class TestRealConfluenceValidation:
    """
    Test class for validating Confluence models with real API data.

    These tests will be skipped if:
    1. The --use-real-data flag is not passed to pytest
    2. The required Confluence environment variables are not set
    """

    def test_get_page_content(self, use_real_confluence_data, test_page_id):
        """Test that get_page_content returns a proper ConfluencePage model."""
        if not use_real_confluence_data:
            pytest.skip("Real Confluence data testing is disabled")

        config = ConfluenceConfig.from_env()
        pages_client = PagesMixin(config=config)

        page = pages_client.get_page_content(test_page_id)

        assert isinstance(page, ConfluencePage)
        assert page.id == test_page_id
        assert page.title is not None
        assert page.content is not None

        assert page.space is not None
        assert page.space.key is not None

        assert page.content_format in ["storage", "view", "markdown"]

    def test_get_page_comments(self, use_real_confluence_data, test_page_id):
        """Test that page comments are properly converted to ConfluenceComment models."""
        if not use_real_confluence_data:
            pytest.skip("Real Confluence data testing is disabled")

        config = ConfluenceConfig.from_env()
        comments_client = ConfluenceCommentsMixin(config=config)

        comments = comments_client.get_page_comments(test_page_id)

        if len(comments) == 0:
            pytest.skip("Test page has no comments")

        for comment in comments:
            assert isinstance(comment, ConfluenceComment)
            assert comment.id is not None
            assert comment.body is not None

    def test_get_page_labels(self, use_real_confluence_data, test_page_id):
        """Test that page labels are properly converted to ConfluenceLabel models."""
        if not use_real_confluence_data:
            pytest.skip("Real Confluence data testing is disabled")

        config = ConfluenceConfig.from_env()
        labels_client = ConfluenceLabelsMixin(config=config)

        labels = labels_client.get_page_labels(test_page_id)

        if len(labels) == 0:
            pytest.skip("Test page has no labels")

        for label in labels:
            assert isinstance(label, ConfluenceLabel)
            assert label.id is not None
            assert label.name is not None

    def test_search_content(self, use_real_confluence_data):
        """Test that search returns ConfluencePage models."""
        if not use_real_confluence_data:
            pytest.skip("Real Confluence data testing is disabled")

        config = ConfluenceConfig.from_env()
        search_client = ConfluenceSearchMixin(config=config)

        cql = 'type = "page" ORDER BY created DESC'
        results = search_client.search(cql, limit=5)

        assert len(results) > 0
        for page in results:
            assert isinstance(page, ConfluencePage)
            assert page.id is not None
            assert page.title is not None


@pytest.mark.anyio
async def test_confluence_get_page_content(
    confluence_client: ConfluenceFetcher, test_page_id: str
) -> None:
    """Test retrieving a page from Confluence."""
    page = confluence_client.get_page_content(test_page_id)

    assert page is not None
    assert page.id == test_page_id
    assert page.title is not None


@pytest.mark.anyio
async def test_confluence_create_page(
    confluence_client: ConfluenceFetcher,
    test_space_key: str,
    resource_tracker: ResourceTracker,
    cleanup_resources: Callable[[], None],
) -> None:
    """Test creating a page in Confluence."""
    # Generate a unique title
    test_id = str(uuid.uuid4())[:8]
    title = f"Test Page (API Validation) {test_id}"

    timestamp = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
    content = f"""
    <h1>Test Page</h1>
    <p>This is a test page created by the API validation tests at {timestamp}.</p>
    <p>It should be automatically deleted after the test.</p>
    """

    try:
        try:
            page = confluence_client.create_page(
                space_key=test_space_key, title=title, body=content
            )
        except Exception as e:
            if "permission" in str(e).lower():
                pytest.skip(f"No permission to create pages in space {test_space_key}")
                return
            elif "space" in str(e).lower() and (
                "not found" in str(e).lower() or "doesn't exist" in str(e).lower()
            ):
                pytest.skip(
                    f"Space {test_space_key} not found. Skipping page creation test."
                )
                return
            else:
                raise

        page_id = page.id
        resource_tracker.add_confluence_page(page_id)

        assert page is not None
        assert page.title == title

        retrieved_page = confluence_client.get_page_content(page_id)
        assert retrieved_page is not None
    finally:
        cleanup_resources()


@pytest.mark.anyio
async def test_confluence_update_page(
    confluence_client: ConfluenceFetcher,
    resource_tracker: ResourceTracker,
    test_space_key: str,
    cleanup_resources: Callable[[], None],
) -> None:
    """Test updating a page in Confluence and validate TextContent structure.

    This test has two purposes:
    1. Test the basic page update functionality
    2. Validate the TextContent class requires the 'type' field to prevent issue #97
    """
    test_id = str(uuid.uuid4())[:8]
    title = f"Update Test Page {test_id}"
    content = f"<p>Initial content {test_id}</p>"

    try:
        page = confluence_client.create_page(
            space_key=test_space_key, title=title, body=content
        )

        page_id = page.id
        resource_tracker.add_confluence_page(page_id)

        now = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
        updated_content = f"<p>Updated content {test_id} at {now}</p>"

        updated_page = confluence_client.update_page(
            page_id=page_id, title=title, body=updated_content
        )

        assert updated_page is not None

        # ======= TextContent Validation (prevents issue #97) =======
        # Import TextContent class to test directly
        from mcp.types import TextContent

        print("Testing TextContent validation to prevent issue #97")

        try:
            _ = TextContent(text="This should fail without type field")
            raise AssertionError(
                "TextContent creation without 'type' field should fail but didn't"
            )
        except Exception as e:
            print(f"Correctly got error: {str(e)}")
            assert "type" in str(e), "Error should mention missing 'type' field"

        valid_content = TextContent(
            type="text", text="This should work with type field"
        )
        assert valid_content.type == "text", "TextContent should have type='text'"
        assert valid_content.text == "This should work with type field", (
            "TextContent text should match"
        )

        print("TextContent validation succeeded - 'type' field is properly required")

    finally:
        cleanup_resources()


@pytest.mark.anyio
async def test_confluence_add_page_label(
    confluence_client: ConfluenceFetcher,
    resource_tracker: ResourceTracker,
    test_space_key: str,
    cleanup_resources: Callable[[], None],
) -> None:
    """Test adding a label to a page in Confluence"""
    test_id = str(uuid.uuid4())[:8]
    title = f"Update Test Page {test_id}"
    content = f"<p>Initial content {test_id}</p>"

    try:
        page = confluence_client.create_page(
            space_key=test_space_key, title=title, body=content
        )

        page_id = page.id
        resource_tracker.add_confluence_page(page_id)

        name = "test"
        updated_labels = confluence_client.add_page_label(page_id=page_id, name=name)

        assert updated_labels is not None
    finally:
        cleanup_resources()
