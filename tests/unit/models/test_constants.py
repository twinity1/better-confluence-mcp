"""Tests for model constants.

Focused tests for model constants, validating correct values and business logic.
"""

from mcp_atlassian.models.constants import (
    # Confluence defaults
    CONFLUENCE_DEFAULT_ID,
    CONFLUENCE_DEFAULT_SPACE,
    CONFLUENCE_DEFAULT_VERSION,
    # Date/Time defaults
    DEFAULT_TIMESTAMP,
    # Common defaults
    EMPTY_STRING,
    NONE_VALUE,
    UNASSIGNED,
    UNKNOWN,
)


class TestCommonDefaults:
    """Test suite for common default constants."""

    def test_string_constants_values(self):
        """Test that common string constants have expected values."""
        assert EMPTY_STRING == ""
        assert UNKNOWN == "Unknown"
        assert UNASSIGNED == "Unassigned"
        assert NONE_VALUE == "None"

    def test_string_constants_types(self):
        """Test that all string constants are strings."""
        assert isinstance(EMPTY_STRING, str)
        assert isinstance(UNKNOWN, str)
        assert isinstance(UNASSIGNED, str)
        assert isinstance(NONE_VALUE, str)


class TestConfluenceDefaults:
    """Test suite for Confluence default constants."""

    def test_confluence_id_value(self):
        """Test Confluence default ID value."""
        assert CONFLUENCE_DEFAULT_ID == "0"

    def test_confluence_default_space_structure(self):
        """Test that Confluence default space has correct structure."""
        assert isinstance(CONFLUENCE_DEFAULT_SPACE, dict)
        expected_space = {
            "key": EMPTY_STRING,
            "name": UNKNOWN,
            "id": CONFLUENCE_DEFAULT_ID,
        }
        assert CONFLUENCE_DEFAULT_SPACE == expected_space

    def test_confluence_default_version_structure(self):
        """Test that Confluence default version has correct structure."""
        assert isinstance(CONFLUENCE_DEFAULT_VERSION, dict)
        expected_version = {"number": 0, "when": EMPTY_STRING}
        assert CONFLUENCE_DEFAULT_VERSION == expected_version
        assert isinstance(CONFLUENCE_DEFAULT_VERSION["number"], int)


class TestDateTimeDefaults:
    """Test suite for date/time default constants."""

    def test_default_timestamp_format(self):
        """Test that DEFAULT_TIMESTAMP has expected format."""
        assert DEFAULT_TIMESTAMP == "1970-01-01T00:00:00.000+0000"
        assert isinstance(DEFAULT_TIMESTAMP, str)
        assert DEFAULT_TIMESTAMP.startswith("1970-01-01T")
        assert "+0000" in DEFAULT_TIMESTAMP


class TestCrossReferenceConsistency:
    """Test suite for consistency between related constants."""

    def test_id_consistency(self):
        """Test that default IDs are consistent across structures."""
        assert CONFLUENCE_DEFAULT_SPACE["id"] == CONFLUENCE_DEFAULT_ID

    def test_semantic_usage_consistency(self):
        """Test that semantically similar fields use consistent values."""
        # UNKNOWN used for required fields with unknown values
        assert CONFLUENCE_DEFAULT_SPACE["name"] == UNKNOWN

        # EMPTY_STRING used for optional string fields
        assert CONFLUENCE_DEFAULT_SPACE["key"] == EMPTY_STRING
        assert CONFLUENCE_DEFAULT_VERSION["when"] == EMPTY_STRING
