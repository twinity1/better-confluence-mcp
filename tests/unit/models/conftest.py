"""
Test fixtures for model testing.

This module provides specialized fixtures for testing data models and API response
parsing. It integrates with the new factory system and provides efficient,
reusable fixtures for model validation and serialization testing.
"""

import os
from typing import Any

import pytest

from mcp_atlassian.utils.env import is_env_truthy
from tests.fixtures.confluence_mocks import (
    MOCK_COMMENTS_RESPONSE,
    MOCK_CQL_SEARCH_RESPONSE,
    MOCK_LABELS_RESPONSE,
    MOCK_PAGE_RESPONSE,
)
from tests.utils.factories import (
    ConfluencePageFactory,
    ErrorResponseFactory,
)

# ============================================================================
# Factory-Based Data Fixtures
# ============================================================================


@pytest.fixture
def make_confluence_page_data():
    """
    Factory fixture for creating Confluence page data for model testing.

    Returns:
        Callable: Function that creates Confluence page data

    Example:
        def test_confluence_model(make_confluence_page_data):
            page_data = make_confluence_page_data(
                title="Model Test Page",
                space={"key": "MODEL"}
            )
            model = ConfluencePage.from_dict(page_data)
            assert model.title == "Model Test Page"
    """
    return ConfluencePageFactory.create


@pytest.fixture
def make_error_response_data():
    """
    Factory fixture for creating error response data for model testing.

    Returns:
        Callable: Function that creates error response data

    Example:
        def test_error_model(make_error_response_data):
            error_data = make_error_response_data(
                status_code=422,
                message="Validation Error"
            )
            model = ErrorResponse.from_dict(error_data)
            assert model.status == 422
    """
    return ErrorResponseFactory.create_api_error


# ============================================================================
# Compatibility Fixtures (using legacy mock data)
# ============================================================================


@pytest.fixture
def confluence_search_data() -> dict[str, Any]:
    """
    Return mock Confluence search (CQL) results.

    Note: This fixture is maintained for backward compatibility.
    """
    return MOCK_CQL_SEARCH_RESPONSE


@pytest.fixture
def confluence_page_data() -> dict[str, Any]:
    """
    Return mock Confluence page data.

    Note: This fixture is maintained for backward compatibility.
    Consider using make_confluence_page_data for new tests.
    """
    return MOCK_PAGE_RESPONSE


@pytest.fixture
def confluence_comments_data() -> dict[str, Any]:
    """
    Return mock Confluence comments data.

    Note: This fixture is maintained for backward compatibility.
    """
    return MOCK_COMMENTS_RESPONSE


@pytest.fixture
def confluence_labels_data() -> dict[str, Any]:
    """
    Return mock Confluence labels data.

    Note: This fixture is maintained for backward compatibility.
    """
    return MOCK_LABELS_RESPONSE


# ============================================================================
# Enhanced Model Test Data Fixtures
# ============================================================================


@pytest.fixture
def complete_confluence_page_data():
    """
    Fixture providing complete Confluence page data with all fields populated.

    Returns:
        Dict[str, Any]: Complete Confluence page data
    """
    return ConfluencePageFactory.create(
        page_id="complete123",
        title="Complete Test Page",
        type="page",
        status="current",
        space={"key": "COMPLETE", "name": "Complete Test Space", "type": "global"},
        body={
            "storage": {
                "value": "<h1>Complete Test Page</h1><p>This page has all fields populated.</p>",
                "representation": "storage",
            },
            "view": {
                "value": "<h1>Complete Test Page</h1><p>This page has all fields populated.</p>",
                "representation": "view",
            },
        },
        version={
            "number": 2,
            "when": "2023-01-02T12:00:00.000Z",
            "by": {"displayName": "Test User"},
            "message": "Updated with complete data",
        },
        metadata={
            "labels": {
                "results": [
                    {"name": "testing"},
                    {"name": "complete"},
                    {"name": "model"},
                ]
            }
        },
        ancestors=[{"id": "parent123", "title": "Parent Page"}],
        children={"page": {"results": [{"id": "child123", "title": "Child Page"}]}},
    )


# ============================================================================
# Validation and Edge Case Fixtures
# ============================================================================


@pytest.fixture
def invalid_confluence_page_data():
    """
    Fixture providing invalid Confluence page data for validation testing.

    Returns:
        List[Dict[str, Any]]: List of invalid page data variations
    """
    return [
        {},  # Empty data
        {"id": None},  # Null ID
        {"id": ""},  # Empty ID
        {"id": "123"},  # Missing title
        {"id": "123", "title": None},  # Null title
        {"id": "123", "title": ""},  # Empty title
        {
            "id": "123",
            "title": "Test",
            "type": "invalid_type",  # Invalid content type
        },
    ]


# ============================================================================
# Model Serialization Test Fixtures
# ============================================================================


@pytest.fixture
def confluence_model_serialization_cases():
    """
    Fixture providing test cases for Confluence model serialization/deserialization.

    Returns:
        List[Dict[str, Any]]: Test cases with expected serialization results
    """
    return [
        {
            "name": "basic_page",
            "input": ConfluencePageFactory.create("serial123"),
            "expected_fields": ["id", "title", "type", "space", "body"],
        },
        {
            "name": "page_with_metadata",
            "input": ConfluencePageFactory.create(
                "serial456",
                version={"number": 2},
                metadata={"labels": {"results": [{"name": "test"}]}},
            ),
            "expected_fields": ["id", "title", "version", "metadata"],
        },
    ]


# ============================================================================
# Real Data Integration Fixtures
# ============================================================================


@pytest.fixture
def use_real_confluence_data() -> bool:
    """
    Check if we should use real Confluence data from the API.

    This will only return True if:
    1. The CONFLUENCE_URL, CONFLUENCE_USERNAME, and CONFLUENCE_API_TOKEN environment variables are set
    2. The USE_REAL_DATA environment variable is set to "true"

    Note: This fixture is maintained for backward compatibility.
    """
    required_vars = ["CONFLUENCE_URL", "CONFLUENCE_USERNAME", "CONFLUENCE_API_TOKEN"]
    if not all(os.environ.get(var) for var in required_vars):
        return False

    return is_env_truthy("USE_REAL_DATA")


@pytest.fixture
def default_confluence_page_id() -> str:
    """
    Provides a default Confluence page ID to use for tests.

    Skips the test if CONFLUENCE_TEST_PAGE_ID environment variable is not set.

    Note: This fixture is maintained for backward compatibility.
    """
    page_id = os.environ.get("CONFLUENCE_TEST_PAGE_ID")
    if not page_id:
        pytest.skip("CONFLUENCE_TEST_PAGE_ID environment variable not set")
    return page_id


# ============================================================================
# Model Performance Test Fixtures
# ============================================================================


@pytest.fixture
def large_confluence_dataset():
    """
    Fixture providing a large dataset for performance testing.

    Returns:
        List[Dict[str, Any]]: Large list of Confluence pages for performance tests
    """
    return [
        ConfluencePageFactory.create(f"perf{i}", title=f"Performance Test Page {i}")
        for i in range(1, 101)  # 100 pages
    ]


# ============================================================================
# Model Composition Fixtures
# ============================================================================


@pytest.fixture
def model_test_suite():
    """
    Comprehensive test suite for model testing.

    This fixture provides a complete set of test data for thorough
    model validation, including edge cases and error conditions.

    Returns:
        Dict[str, Any]: Complete model test suite
    """

    def get_complete_confluence_data():
        return ConfluencePageFactory.create(
            page_id="complete123", title="Complete Test Page"
        )

    def get_invalid_confluence_data():
        return [
            {},  # Empty data
            {"id": None},  # Null ID
            {"id": ""},  # Empty ID
        ]

    return {
        "confluence": {
            "valid": [
                ConfluencePageFactory.create("suite1"),
                ConfluencePageFactory.create("suite2", title="Suite Page 2"),
                get_complete_confluence_data(),
            ],
            "invalid": get_invalid_confluence_data(),
            "edge_cases": [
                ConfluencePageFactory.create("edge1", body={}),
                ConfluencePageFactory.create("edge2", space={}),
            ],
        },
        "errors": [
            ErrorResponseFactory.create_api_error(400, "Bad Request"),
            ErrorResponseFactory.create_api_error(404, "Not Found"),
            ErrorResponseFactory.create_auth_error(),
        ],
    }
