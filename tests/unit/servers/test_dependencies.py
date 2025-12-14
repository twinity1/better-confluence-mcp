"""Unit tests for server dependencies module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mcp_atlassian.confluence import ConfluenceConfig, ConfluenceFetcher
from mcp_atlassian.servers.context import MainAppContext
from mcp_atlassian.servers.dependencies import get_confluence_fetcher
from tests.utils.mocks import MockFastMCP

# Configure pytest for async tests
pytestmark = pytest.mark.anyio


@pytest.fixture
def config_factory():
    """Factory for creating various configuration objects."""

    class ConfigFactory:
        @staticmethod
        def create_confluence_config(auth_type="basic", **overrides):
            """Create a ConfluenceConfig instance."""
            defaults = {
                "url": "https://test.atlassian.net",
                "auth_type": auth_type,
                "ssl_verify": True,
                "http_proxy": None,
                "https_proxy": None,
                "no_proxy": None,
                "socks_proxy": None,
                "spaces_filter": ["TEST"],
            }

            if auth_type == "basic":
                defaults.update(
                    {"username": "test_username", "api_token": "test_token"}
                )
            elif auth_type == "pat":
                defaults["personal_token"] = "test_pat_token"

            return ConfluenceConfig(**{**defaults, **overrides})

        @staticmethod
        def create_app_context(confluence_config=None, **overrides):
            """Create a MainAppContext instance."""
            defaults = {
                "full_confluence_config": confluence_config
                or ConfigFactory.create_confluence_config(),
                "read_only": False,
                "enabled_tools": ["confluence_get_page"],
            }
            return MainAppContext(**{**defaults, **overrides})

    return ConfigFactory()


@pytest.fixture
def mock_context():
    """Create a mock Context instance."""
    return MockFastMCP.create_context()


def _setup_mock_context(mock_context, app_context):
    """Helper to setup mock context with app context."""
    mock_context.request_context.lifespan_context = {
        "app_lifespan_context": app_context
    }


class TestGetConfluenceFetcher:
    """Tests for get_confluence_fetcher function."""

    @patch("mcp_atlassian.servers.dependencies.ConfluenceFetcher")
    async def test_fetcher_created_from_global_config(
        self,
        mock_confluence_fetcher_class,
        mock_context,
        config_factory,
    ):
        """Test that ConfluenceFetcher is created from global config."""
        # Setup context with global config
        app_context = config_factory.create_app_context()
        _setup_mock_context(mock_context, app_context)

        # Setup mock fetcher
        mock_fetcher = MagicMock(spec=ConfluenceFetcher)
        mock_confluence_fetcher_class.return_value = mock_fetcher

        result = await get_confluence_fetcher(mock_context)

        assert result == mock_fetcher
        mock_confluence_fetcher_class.assert_called_once_with(
            config=app_context.full_confluence_config
        )

    @pytest.mark.parametrize("auth_type", ["basic", "pat"])
    @patch("mcp_atlassian.servers.dependencies.ConfluenceFetcher")
    async def test_fetcher_with_different_auth_types(
        self,
        mock_confluence_fetcher_class,
        mock_context,
        config_factory,
        auth_type,
    ):
        """Test fetcher creation with different auth types."""
        # Setup context with specific auth type
        confluence_config = config_factory.create_confluence_config(auth_type=auth_type)
        app_context = config_factory.create_app_context(confluence_config)
        _setup_mock_context(mock_context, app_context)

        # Setup mock fetcher
        mock_fetcher = MagicMock(spec=ConfluenceFetcher)
        mock_confluence_fetcher_class.return_value = mock_fetcher

        result = await get_confluence_fetcher(mock_context)

        assert result == mock_fetcher
        # Verify the config passed to ConfluenceFetcher
        called_config = mock_confluence_fetcher_class.call_args[1]["config"]
        assert called_config.auth_type == auth_type

    async def test_missing_global_config_raises_error(
        self,
        mock_context,
    ):
        """Test error when global config is missing."""
        mock_context.request_context.lifespan_context = {}

        with pytest.raises(ValueError, match="Confluence client.*not available"):
            await get_confluence_fetcher(mock_context)

    async def test_missing_lifespan_context_raises_error(
        self,
        mock_context,
    ):
        """Test error when lifespan context is missing."""
        mock_context.request_context.lifespan_context = None

        with pytest.raises((ValueError, TypeError, AttributeError)):
            await get_confluence_fetcher(mock_context)

    async def test_fetcher_with_none_confluence_config(
        self,
        mock_context,
    ):
        """Test error when confluence config is None."""
        # Create app context with None confluence config directly
        app_context = MainAppContext(
            full_confluence_config=None,
            read_only=False,
            enabled_tools=[],
        )
        _setup_mock_context(mock_context, app_context)

        with pytest.raises(ValueError, match="Confluence client.*not available"):
            await get_confluence_fetcher(mock_context)
