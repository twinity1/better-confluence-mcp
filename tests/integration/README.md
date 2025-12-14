# Integration Tests

This directory contains integration tests for the Better Confluence MCP project. These tests validate the interaction between different components and services.

## Test Categories

### 1. Authentication Integration (`test_authentication.py`)
Tests various authentication flows including Basic Auth and PAT tokens.

- **Basic Auth**: Tests username/password authentication
- **PAT Tokens**: Tests Personal Access Token authentication

### 2. Content Processing Integration (`test_content_processing.py`)
Tests HTML/Markdown conversion and content preprocessing.

- **Roundtrip Conversion**: HTML ↔ Markdown accuracy
- **Macro Preservation**: Confluence macro handling
- **Performance**: Large content processing (>1MB)
- **Edge Cases**: Empty content, malformed HTML, Unicode

## Running Integration Tests

### Basic Execution
```bash
# Run all integration tests (mocked)
uv run pytest tests/integration/ --integration

# Run specific test file
uv run pytest tests/integration/test_authentication.py --integration

# Run with coverage
uv run pytest tests/integration/ --integration --cov=src/mcp_atlassian
```

### Real API Testing
```bash
# Run tests against real Confluence APIs
uv run pytest tests/integration/ --integration --use-real-data

# Required environment variables for real API tests:
export CONFLUENCE_URL=https://your-domain.atlassian.net/wiki
export CONFLUENCE_USERNAME=your-email@example.com
export CONFLUENCE_API_TOKEN=your-api-token
export CONFLUENCE_TEST_SPACE_KEY=TEST
```

### Test Markers
- `@pytest.mark.integration` - All integration tests
- `@pytest.mark.anyio` - Async tests supporting multiple backends

## Environment Setup

### For Mocked Tests
No special setup required. Tests use the utilities from `tests/utils/` for mocking.

### For Real API Tests
1. Create a test space in Confluence (e.g., "TEST")
2. Generate API tokens from your Atlassian account
3. Set environment variables as shown above
4. Ensure your account has permissions to create/delete in test areas

## Test Data Management

### Automatic Cleanup
Real API tests implement automatic cleanup using pytest fixtures:
- Created pages are tracked and deleted after each test
- Attachments are cleaned up with their parent items

### Manual Cleanup
If tests fail and leave data behind:
```cql
# Use CQL to find test pages
space = TEST AND title ~ "Integration Test*"
```

## Writing New Integration Tests

### Best Practices
1. **Use Test Utilities**: Leverage helpers from `tests/utils/`
2. **Mark Appropriately**: Use `@pytest.mark.integration`
3. **Mock by Default**: Only use real APIs with explicit flag
4. **Clean Up**: Always clean up created test data
5. **Unique Identifiers**: Use UUIDs to avoid conflicts
6. **Error Handling**: Test both success and failure paths

### Example Test Structure
```python
import pytest
from tests.utils.base import BaseAuthTest
from tests.utils.mocks import MockEnvironment

@pytest.mark.integration
class TestNewIntegration(BaseAuthTest):
    def test_feature(self):
        with MockEnvironment.basic_auth_env():
            # Test implementation
            pass
```

## Troubleshooting

### Common Issues

1. **SSL Errors**: Set `CONFLUENCE_SSL_VERIFY=false`
2. **Proxy Issues**: Check `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` settings
3. **Rate Limiting**: Add delays between requests or reduce test frequency
4. **Permission Errors**: Ensure test user has appropriate permissions
5. **Cleanup Failures**: Manually delete test data using CQL queries

### Debug Mode
```bash
# Run with verbose output
uv run pytest tests/integration/ --integration -v

# Run with debug logging
uv run pytest tests/integration/ --integration --log-cli-level=DEBUG
```

## CI/CD Integration

### GitHub Actions Example
```yaml
- name: Run Integration Tests
  env:
    CONFLUENCE_URL: ${{ secrets.CONFLUENCE_URL }}
    CONFLUENCE_USERNAME: ${{ secrets.CONFLUENCE_USERNAME }}
    CONFLUENCE_API_TOKEN: ${{ secrets.CONFLUENCE_API_TOKEN }}
  run: |
    uv run pytest tests/integration/ --integration
```

### Skip Patterns
- Integration tests are skipped by default without `--integration` flag
- Real API tests require both `--integration` and `--use-real-data` flags
- Tests skip gracefully when required environment variables are missing
