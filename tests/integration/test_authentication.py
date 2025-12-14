"""Integration tests for authentication functionality."""

from unittest.mock import MagicMock, patch

import pytest

from mcp_atlassian.confluence.client import ConfluenceClient
from mcp_atlassian.confluence.config import ConfluenceConfig
from tests.utils.mocks import MockEnvironment


@pytest.mark.integration
class TestBasicAuthValidation:
    """Test basic authentication validation against real endpoints."""

    @patch("mcp_atlassian.confluence.client.Confluence")
    def test_confluence_basic_auth_success(self, mock_confluence_class):
        """Test successful Confluence basic authentication."""
        with MockEnvironment.basic_auth_env() as auth_env:
            # Create mock Confluence instance
            mock_confluence = MagicMock()
            mock_confluence_class.return_value = mock_confluence

            # Create Confluence client
            config = ConfluenceConfig.from_env()
            client = ConfluenceClient(config)

            # Verify Confluence was initialized with correct params
            mock_confluence_class.assert_called_once_with(
                url=auth_env["CONFLUENCE_URL"],
                username=auth_env["CONFLUENCE_USERNAME"],
                password=auth_env["CONFLUENCE_API_TOKEN"],
                cloud=True,
                verify_ssl=True,
            )


@pytest.mark.integration
class TestPATTokenValidation:
    """Test Personal Access Token (PAT) validation and precedence."""

    @patch("mcp_atlassian.confluence.client.Confluence")
    def test_confluence_pat_token_success(self, mock_confluence_class):
        """Test successful Confluence PAT authentication."""
        # Clear existing auth env vars first
        with MockEnvironment.clean_env():
            with patch.dict(
                "os.environ",
                {
                    "CONFLUENCE_URL": "https://confluence.company.com",  # Server URL for PAT
                    "CONFLUENCE_PERSONAL_TOKEN": "test-personal-access-token",
                },
            ):
                # Create mock Confluence instance
                mock_confluence = MagicMock()
                mock_confluence_class.return_value = mock_confluence

                # Create Confluence client
                config = ConfluenceConfig.from_env()
                client = ConfluenceClient(config)

                # Verify Confluence was initialized with PAT token
                mock_confluence_class.assert_called_once_with(
                    url="https://confluence.company.com",
                    token="test-personal-access-token",
                    cloud=False,  # Server instance
                    verify_ssl=True,
                )


@pytest.mark.integration
class TestAuthenticationPriority:
    """Test authentication method detection priority from environment."""

    def test_pat_takes_precedence_over_basic_for_server(self):
        """Test that PAT takes precedence over basic auth for server URLs."""
        with MockEnvironment.clean_env():
            with patch.dict(
                "os.environ",
                {
                    "CONFLUENCE_URL": "https://confluence.company.com",  # Server URL
                    "CONFLUENCE_PERSONAL_TOKEN": "personal-token",
                    "CONFLUENCE_USERNAME": "user@example.com",
                    "CONFLUENCE_API_TOKEN": "api-token",
                },
            ):
                config = ConfluenceConfig.from_env()
                assert config.auth_type == "pat"

    def test_basic_auth_for_cloud(self):
        """Test that basic auth is used for cloud URLs."""
        with MockEnvironment.clean_env():
            with patch.dict(
                "os.environ",
                {
                    "CONFLUENCE_URL": "https://test.atlassian.net/wiki",
                    "CONFLUENCE_USERNAME": "user@example.com",
                    "CONFLUENCE_API_TOKEN": "api-token",
                },
            ):
                config = ConfluenceConfig.from_env()
                assert config.auth_type == "basic"

    def test_cloud_vs_server_authentication(self):
        """Test authentication differences between cloud and server instances."""
        # Cloud instance (default)
        with MockEnvironment.clean_env():
            with patch.dict(
                "os.environ",
                {
                    "CONFLUENCE_URL": "https://example.atlassian.net/wiki",
                    "CONFLUENCE_USERNAME": "user@example.com",
                    "CONFLUENCE_API_TOKEN": "api-token",
                },
            ):
                config = ConfluenceConfig.from_env()
                assert config.is_cloud is True

        # Server instance
        with MockEnvironment.clean_env():
            with patch.dict(
                "os.environ",
                {
                    "CONFLUENCE_URL": "https://confluence.company.com",
                    "CONFLUENCE_USERNAME": "user@example.com",
                    "CONFLUENCE_API_TOKEN": "api-token",
                },
            ):
                config = ConfluenceConfig.from_env()
                assert config.is_cloud is False


@pytest.mark.integration
class TestConfluenceSSLAndProxy:
    """Test SSL verification and proxy settings work with authentication."""

    def test_ssl_and_proxy_with_authentication(self):
        """Test SSL verification and proxy settings work with authentication."""
        with MockEnvironment.clean_env():
            with patch.dict(
                "os.environ",
                {
                    "CONFLUENCE_URL": "https://test.atlassian.net/wiki",
                    "CONFLUENCE_USERNAME": "user@example.com",
                    "CONFLUENCE_API_TOKEN": "api-token",
                    "CONFLUENCE_SSL_VERIFY": "false",
                    "HTTPS_PROXY": "http://proxy.company.com:8080",
                },
            ):
                config = ConfluenceConfig.from_env()
                assert config.ssl_verify is False
                assert config.https_proxy == "http://proxy.company.com:8080"

                with patch("mcp_atlassian.confluence.client.Confluence") as mock_conf:
                    client = ConfluenceClient(config)
                    # Verify SSL verification was disabled
                    mock_conf.assert_called_with(
                        url="https://test.atlassian.net/wiki",
                        username="user@example.com",
                        password="api-token",
                        cloud=True,
                        verify_ssl=False,
                    )
