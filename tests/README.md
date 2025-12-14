# MCP Confluence Test Fixtures Documentation

This document describes the enhanced test fixture system implemented for the Better Confluence MCP project.

## Overview

The test fixture system has been significantly improved to provide:

- **Session-scoped fixtures** for expensive operations
- **Factory-based fixtures** for customizable test data
- **Better fixture composition** and reusability
- **Backward compatibility** with existing tests
- **Integration with test utilities** framework

## Architecture

```
tests/
├── conftest.py                 # Root fixtures with session-scoped data
├── unit/
│   ├── confluence/conftest.py # Confluence-specific fixtures
│   └── models/conftest.py     # Model testing fixtures
├── utils/                     # Test utilities framework
│   ├── factories.py          # Data factories
│   ├── mocks.py              # Mock utilities
│   ├── base.py               # Base test classes
│   └── assertions.py         # Custom assertions
└── fixtures/                  # Legacy mock data
    └── confluence_mocks.py   # Static Confluence mock data
```

## Key Features

### 1. Session-Scoped Fixtures

These fixtures are computed once per test session to improve performance:

- `session_auth_configs`: Authentication configuration templates
- `session_mock_data`: Mock data templates for API responses
- `session_confluence_spaces`: Confluence space definitions

```python
# Example usage
def test_with_session_data(session_mock_data):
    # Uses cached data, computed once per session
    assert session_mock_data["confluence_page"] is not None
```

### 2. Factory-Based Fixtures

These fixtures return factory functions for creating customizable test data:

- `make_confluence_page`: Create Confluence pages with custom properties
- `make_auth_config`: Create authentication configurations
- `make_api_error`: Create API error responses

```python
# Example usage
def test_custom_page(make_confluence_page):
    page = make_confluence_page(
        title="Custom Page",
        space={"key": "CUSTOM"}
    )
    assert page["title"] == "Custom Page"
    assert page["space"]["key"] == "CUSTOM"
```

### 3. Environment Management

Enhanced environment fixtures for testing different authentication scenarios:

- `clean_environment`: No authentication variables
- `basic_auth_environment`: Basic auth setup
- `parametrized_auth_env`: Parameterized auth testing

```python
# Example usage
@pytest.mark.parametrize("parametrized_auth_env",
                       ["basic_auth"], indirect=True)
def test_auth_scenarios(parametrized_auth_env):
    # Test runs with basic auth environment
    pass
```

### 4. Enhanced Mock Clients

Improved mock clients with better integration:

- `mock_confluence_client`: Pre-configured mock Confluence client
- `enhanced_mock_confluence_client`: Factory-integrated Confluence client

### 5. Specialized Data Fixtures

Domain-specific fixtures for complex testing scenarios:

- `make_confluence_page_with_content`: Pages with rich content
- `make_confluence_search_results`: CQL search results

## Migration Guide

### For New Tests

Use the enhanced factory-based fixtures:

```python
def test_new_functionality(make_confluence_page):
    # Create custom test data
    page = make_confluence_page(title="New Test Page")

    # Test your functionality
    assert page["title"] == "New Test Page"
```

### For Existing Tests

Existing tests continue to work without changes due to backward compatibility:

```python
def test_existing_functionality(confluence_page_data):
    # These fixtures still work as before
    assert confluence_page_data["title"] == "Test Page"
```

### Performance Testing

Use large dataset fixtures for performance tests:

```python
def test_performance(large_confluence_dataset):
    # 100 pages for performance testing
    assert len(large_confluence_dataset) == 100
```

## Best Practices

### 1. Choose the Right Fixture

- Use **factory fixtures** for customizable data
- Use **session-scoped fixtures** for static, expensive data
- Use **legacy fixtures** only for backward compatibility

### 2. Session-Scoped Data

Take advantage of session-scoped fixtures for data that doesn't change:

```python
# Good: Uses session-scoped data
def test_space_parsing(session_confluence_spaces):
    parser = SpaceParser(session_confluence_spaces)
    assert parser.is_valid()

# Avoid: Creates new data every time
def test_space_parsing():
    spaces = create_space_definitions()  # Expensive operation
    parser = SpaceParser(spaces)
    assert parser.is_valid()
```

### 3. Factory Customization

Use factories to create exactly the data you need:

```python
# Good: Creates minimal required data
def test_page_id_validation(make_confluence_page):
    page = make_confluence_page(page_id="123456")
    assert validate_id(page["id"])

# Avoid: Uses complex data when simple would do
def test_page_id_validation(complete_confluence_page_data):
    assert validate_id(complete_confluence_page_data["id"])
```

### 4. Environment Testing

Use parametrized fixtures for testing multiple scenarios:

```python
@pytest.mark.parametrize("parametrized_auth_env",
                       ["basic_auth", "clean"], indirect=True)
def test_auth_detection(parametrized_auth_env):
    # Test with different auth environments
    detector = AuthDetector()
    auth_type = detector.detect_auth_type()
    assert auth_type in ["basic", None]
```

## Backward Compatibility

All existing tests continue to work without modification. The enhanced fixtures:

1. **Maintain existing interfaces**: Old fixture names and return types unchanged
2. **Preserve mock data**: Original mock responses still available
3. **Support gradual migration**: Teams can adopt new fixtures incrementally

## Performance Improvements

The enhanced fixture system provides significant performance improvements:

1. **Session-scoped caching**: Expensive data created once per session
2. **Lazy loading**: Data only created when needed
3. **Efficient factories**: Minimal object creation overhead
4. **Reduced duplication**: Shared fixtures across test modules

## Examples

### Basic Usage

```python
def test_confluence_page_creation(make_confluence_page):
    # Create a custom page
    page = make_confluence_page(
        page_id="123456",
        title="Custom test page"
    )

    # Test the page
    model = ConfluencePage.from_dict(page)
    assert model.id == "123456"
    assert model.title == "Custom test page"
```

### Advanced Usage

```python
def test_complex_workflow(
    make_confluence_page_with_content,
    basic_auth_environment
):
    # Create page with content
    page = make_confluence_page_with_content(
        title="Workflow Documentation",
        content="<h1>Workflow</h1><p>Process documentation</p>",
        labels=["workflow", "documentation"]
    )

    # Test workflow with basic auth environment
    workflow = ComplexWorkflow(page)
    result = workflow.execute()

    assert result.success
    assert "Workflow Documentation" in result.documentation
```

### Integration Testing

```python
def test_real_api_integration(
    confluence_integration_client,
    use_real_confluence_data
):
    if not use_real_confluence_data:
        pytest.skip("Real Confluence data not available")

    # Test with real API clients
    pages = confluence_integration_client.get_space_pages("TEST")

    assert len(pages) >= 0
```

## Conclusion

The enhanced fixture system provides a powerful, flexible, and efficient foundation for testing the Better Confluence MCP project. It maintains backward compatibility while offering significant improvements in performance, reusability, and developer experience.

Key benefits:

- **Faster test execution** through session-scoped caching
- **More flexible test data** through factory functions
- **Better reusability** across test modules
- **Improved maintainability** with clear separation of concerns
- **Backward compatibility** with existing tests

For questions or suggestions about the fixture system, please refer to the test utilities documentation in `tests/utils/`.
