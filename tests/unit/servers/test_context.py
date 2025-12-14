"""Tests for the server context module."""

import pytest

from mcp_atlassian.confluence.config import ConfluenceConfig
from mcp_atlassian.servers.context import MainAppContext


class TestMainAppContext:
    """Tests for the MainAppContext dataclass."""

    def test_initialization_with_defaults(self):
        """Test MainAppContext initialization with default values."""
        context = MainAppContext()

        assert context.full_confluence_config is None
        assert context.read_only is False
        assert context.enabled_tools is None

    def test_initialization_with_all_parameters(self):
        """Test MainAppContext initialization with all parameters provided."""
        # Arrange
        confluence_config = ConfluenceConfig(
            url="https://example.atlassian.net/wiki",
            auth_type="basic",
            username="test@example.com",
            api_token="test_token",
        )
        enabled_tools = ["confluence_get_page", "confluence_search"]

        # Act
        context = MainAppContext(
            full_confluence_config=confluence_config,
            read_only=True,
            enabled_tools=enabled_tools,
        )

        # Assert
        assert context.full_confluence_config is confluence_config
        assert context.read_only is True
        assert context.enabled_tools == enabled_tools

    def test_initialization_with_partial_parameters(self):
        """Test MainAppContext initialization with some parameters provided."""
        # Arrange
        confluence_config = ConfluenceConfig(
            url="https://example.atlassian.net/wiki",
            auth_type="pat",
            personal_token="test_personal_token",
        )

        # Act
        context = MainAppContext(full_confluence_config=confluence_config, read_only=True)

        # Assert
        assert context.full_confluence_config is confluence_config
        assert context.read_only is True
        assert context.enabled_tools is None

    def test_frozen_dataclass_behavior(self):
        """Test that MainAppContext is frozen and immutable."""
        # Arrange
        context = MainAppContext(read_only=False)

        # Act & Assert - should raise FrozenInstanceError when trying to modify
        with pytest.raises(AttributeError):
            context.read_only = True

        with pytest.raises(AttributeError):
            context.full_confluence_config = ConfluenceConfig(
                url="https://test.com",
                auth_type="basic",
                username="test",
                api_token="token",
            )

    def test_type_hint_compliance_confluence_config(self):
        """Test type hint compliance for ConfluenceConfig field."""
        # Test with None
        context = MainAppContext(full_confluence_config=None)
        assert context.full_confluence_config is None

        # Test with valid ConfluenceConfig
        confluence_config = ConfluenceConfig(
            url="https://confluence.example.com",
            auth_type="pat",
            username="test@example.com",
            api_token="test_token",
        )
        context = MainAppContext(full_confluence_config=confluence_config)
        assert isinstance(context.full_confluence_config, ConfluenceConfig)
        assert context.full_confluence_config.url == "https://confluence.example.com"

    def test_enabled_tools_field_validation(self):
        """Test enabled_tools field validation and default handling."""
        # Test with None (default)
        context = MainAppContext()
        assert context.enabled_tools is None

        # Test with empty list
        context = MainAppContext(enabled_tools=[])
        assert context.enabled_tools == []

        # Test with list of strings
        tools = ["confluence_create_page", "confluence_search", "confluence_get_page"]
        context = MainAppContext(enabled_tools=tools)
        assert context.enabled_tools == tools
        assert len(context.enabled_tools) == 3

    def test_read_only_field_validation(self):
        """Test read_only field validation and default handling."""
        # Test default value
        context = MainAppContext()
        assert context.read_only is False
        assert isinstance(context.read_only, bool)

        # Test explicit True
        context = MainAppContext(read_only=True)
        assert context.read_only is True
        assert isinstance(context.read_only, bool)

        # Test explicit False
        context = MainAppContext(read_only=False)
        assert context.read_only is False
        assert isinstance(context.read_only, bool)

    def test_string_representation(self):
        """Test the string representation of MainAppContext."""
        # Test with default values
        context = MainAppContext()
        str_repr = str(context)

        assert "MainAppContext" in str_repr
        assert "full_confluence_config=None" in str_repr
        assert "read_only=False" in str_repr
        assert "enabled_tools=None" in str_repr

        # Test with values provided
        confluence_config = ConfluenceConfig(
            url="https://test.atlassian.net/wiki",
            auth_type="basic",
            username="test",
            api_token="token",
        )
        context = MainAppContext(
            full_confluence_config=confluence_config,
            read_only=True,
            enabled_tools=["tool1", "tool2"],
        )
        str_repr = str(context)

        assert "MainAppContext" in str_repr
        assert "read_only=True" in str_repr
        assert "enabled_tools=['tool1', 'tool2']" in str_repr

    def test_equality_comparison(self):
        """Test equality comparison between MainAppContext instances."""
        # Test identical instances
        context1 = MainAppContext()
        context2 = MainAppContext()
        assert context1 == context2

        # Test instances with same values
        confluence_config = ConfluenceConfig(
            url="https://test.atlassian.net/wiki",
            auth_type="basic",
            username="test",
            api_token="token",
        )
        context1 = MainAppContext(
            full_confluence_config=confluence_config, read_only=True, enabled_tools=["tool1"]
        )
        context2 = MainAppContext(
            full_confluence_config=confluence_config, read_only=True, enabled_tools=["tool1"]
        )
        assert context1 == context2

        # Test instances with different values
        context3 = MainAppContext(read_only=False)
        context4 = MainAppContext(read_only=True)
        assert context3 != context4

        # Test with different configs
        different_confluence_config = ConfluenceConfig(
            url="https://different.atlassian.net/wiki",
            auth_type="basic",
            username="different",
            api_token="different_token",
        )
        context5 = MainAppContext(full_confluence_config=confluence_config)
        context6 = MainAppContext(full_confluence_config=different_confluence_config)
        assert context5 != context6

    def test_hash_behavior(self):
        """Test hash behavior for MainAppContext instances."""
        # Test that instances with only hashable fields (None configs, no enabled_tools) can be hashed
        context1 = MainAppContext(read_only=True)
        context2 = MainAppContext(read_only=True)
        assert hash(context1) == hash(context2)

        # Test instances with different hashable values
        context3 = MainAppContext(read_only=False)
        context4 = MainAppContext(read_only=True)
        contexts_dict = {context3: "value3", context4: "value4"}
        assert len(contexts_dict) == 2

        # Test that instances with unhashable fields raise TypeError
        confluence_config = ConfluenceConfig(
            url="https://test.atlassian.net/wiki",
            auth_type="basic",
            username="test",
            api_token="token",
        )
        context_with_config = MainAppContext(full_confluence_config=confluence_config)
        with pytest.raises(TypeError, match="unhashable type"):
            hash(context_with_config)

        # Test that instances with list fields raise TypeError
        context_with_list = MainAppContext(enabled_tools=["tool1", "tool2"])
        with pytest.raises(TypeError, match="unhashable type"):
            hash(context_with_list)

    def test_field_access_edge_cases(self):
        """Test edge cases for field access."""
        # Test accessing fields on empty context
        context = MainAppContext()

        # All fields should be accessible
        assert hasattr(context, "full_confluence_config")
        assert hasattr(context, "read_only")
        assert hasattr(context, "enabled_tools")

        # Test that we can't access non-existent fields
        assert not hasattr(context, "non_existent_field")

    def test_with_pat_config(self):
        """Test MainAppContext with Confluence config using PAT auth type."""
        # Arrange
        confluence_config = ConfluenceConfig(
            url="https://confluence.company.com",
            auth_type="pat",
            personal_token="test-pat-token",
        )

        # Act
        context = MainAppContext(
            full_confluence_config=confluence_config,
            read_only=True,
            enabled_tools=[
                "confluence_get_page",
                "confluence_create_page",
                "confluence_search",
            ],
        )

        # Assert
        assert context.full_confluence_config.auth_type == "pat"
        assert context.read_only is True
        assert len(context.enabled_tools) == 3
        assert "confluence_get_page" in context.enabled_tools
        assert "confluence_create_page" in context.enabled_tools
