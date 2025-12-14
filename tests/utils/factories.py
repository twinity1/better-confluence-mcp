"""Test data factories for creating consistent test objects."""

from typing import Any


class ConfluencePageFactory:
    """Factory for creating Confluence page test data."""

    @staticmethod
    def create(page_id: str = "123456", **overrides) -> dict[str, Any]:
        """Create a Confluence page with default values."""
        defaults = {
            "id": page_id,
            "title": "Test Page",
            "type": "page",
            "status": "current",
            "space": {"key": "TEST", "name": "Test Space"},
            "body": {
                "storage": {"value": "<p>Test content</p>", "representation": "storage"}
            },
            "version": {"number": 1},
            "_links": {
                "webui": f"/spaces/TEST/pages/{page_id}",
                "self": f"https://test.atlassian.net/wiki/rest/api/content/{page_id}",
            },
        }
        return deep_merge(defaults, overrides)


class AuthConfigFactory:
    """Factory for authentication configuration objects."""

    @staticmethod
    def create_basic_auth_config(**overrides) -> dict[str, str]:
        """Create basic auth configuration."""
        defaults = {
            "url": "https://test.atlassian.net",
            "username": "test@example.com",
            "api_token": "test-api-token",
        }
        return {**defaults, **overrides}


class ErrorResponseFactory:
    """Factory for creating error response test data."""

    @staticmethod
    def create_api_error(
        status_code: int = 400, message: str = "Bad Request"
    ) -> dict[str, Any]:
        """Create API error response."""
        return {"errorMessages": [message], "errors": {}, "status": status_code}

    @staticmethod
    def create_auth_error() -> dict[str, Any]:
        """Create authentication error response."""
        return {"errorMessages": ["Authentication failed"], "status": 401}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
