"""Base test classes and utilities for Better Confluence MCP tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest


class BaseMixinTest:
    """Base class for mixin tests with common setup patterns."""

    @pytest.fixture
    def mock_config(self):
        """Mock configuration for testing."""
        return MagicMock()

    @pytest.fixture
    def mock_client(self):
        """Mock client with common methods."""
        client = MagicMock()
        # Add common client methods
        client.get = AsyncMock()
        client.post = AsyncMock()
        client.put = AsyncMock()
        client.delete = AsyncMock()
        return client


class BaseAuthTest:
    """Base class for authentication-related tests."""

    @pytest.fixture
    def basic_auth_env_vars(self):
        """Standard basic auth environment variables."""
        return {
            "CONFLUENCE_URL": "https://test.atlassian.net/wiki",
            "CONFLUENCE_USERNAME": "test@example.com",
            "CONFLUENCE_API_TOKEN": "test-token",
        }


class BaseServerTest:
    """Base class for server-related tests."""

    @pytest.fixture
    def mock_request(self):
        """Mock FastMCP request object."""
        request = MagicMock()
        request.state = MagicMock()
        return request

    @pytest.fixture
    def mock_context(self):
        """Mock FastMCP context object."""
        return MagicMock()
