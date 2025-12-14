"""Reusable mock utilities and fixtures for Better Confluence MCP tests."""

import os
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

from .factories import AuthConfigFactory, ConfluencePageFactory


class MockEnvironment:
    """Utility for mocking environment variables."""

    @staticmethod
    @contextmanager
    def basic_auth_env():
        """Context manager for basic auth environment variables."""
        auth_config = AuthConfigFactory.create_basic_auth_config()
        env_vars = {
            "CONFLUENCE_URL": auth_config["url"],
            "CONFLUENCE_USERNAME": auth_config["username"],
            "CONFLUENCE_API_TOKEN": auth_config["api_token"],
        }
        with patch.dict(os.environ, env_vars, clear=False):
            yield env_vars

    @staticmethod
    @contextmanager
    def clean_env():
        """Context manager with no authentication environment variables."""
        auth_vars = [
            "CONFLUENCE_URL",
            "CONFLUENCE_USERNAME",
            "CONFLUENCE_API_TOKEN",
            "CONFLUENCE_PERSONAL_TOKEN",
        ]

        # Remove auth vars from environment
        with patch.dict(os.environ, {}, clear=False) as env_dict:
            for var in auth_vars:
                env_dict.pop(var, None)
            yield env_dict


class MockAtlassianClient:
    """Factory for creating mock Atlassian clients."""

    @staticmethod
    def create_confluence_client(**response_overrides):
        """Create a mock Confluence client with common responses."""
        client = MagicMock()

        # Default responses
        default_responses = {
            "get_page_by_id": ConfluencePageFactory.create(),
            "get_all_pages_from_space": {
                "results": [
                    ConfluencePageFactory.create("123"),
                    ConfluencePageFactory.create("456"),
                ]
            },
            "get_all_spaces": {"results": [{"key": "TEST", "name": "Test Space"}]},
        }

        # Merge with overrides
        responses = {**default_responses, **response_overrides}

        # Set up mock methods
        client.get_page_by_id.return_value = responses["get_page_by_id"]
        client.get_all_pages_from_space.return_value = responses[
            "get_all_pages_from_space"
        ]
        client.get_all_spaces.return_value = responses["get_all_spaces"]

        return client


class MockFastMCP:
    """Utility for mocking FastMCP components."""

    @staticmethod
    def create_request(state_data: dict[str, Any] | None = None):
        """Create a mock FastMCP request."""
        request = MagicMock()
        request.state = MagicMock()

        if state_data:
            for key, value in state_data.items():
                setattr(request.state, key, value)

        return request

    @staticmethod
    def create_context():
        """Create a mock FastMCP context."""
        return MagicMock()


class MockPreprocessor:
    """Utility for mocking content preprocessors."""

    @staticmethod
    def create_html_to_markdown():
        """Create a mock HTML to Markdown preprocessor."""
        preprocessor = MagicMock()
        preprocessor.process.return_value = "# Markdown Content"
        return preprocessor

    @staticmethod
    def create_markdown_to_html():
        """Create a mock Markdown to HTML preprocessor."""
        preprocessor = MagicMock()
        preprocessor.process.return_value = "<h1>HTML Content</h1>"
        return preprocessor
