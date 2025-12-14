"""Tests for the environment utilities module."""

import logging

import pytest

from mcp_atlassian.utils.environment import get_available_services
from tests.utils.assertions import assert_log_contains
from tests.utils.mocks import MockEnvironment


@pytest.fixture(autouse=True)
def setup_logger():
    """Ensure logger is set to INFO level for capturing log messages."""
    logger = logging.getLogger("mcp-atlassian.utils.environment")
    original_level = logger.level
    logger.setLevel(logging.INFO)
    yield
    logger.setLevel(original_level)


@pytest.fixture
def env_scenarios():
    """Environment configuration scenarios for testing."""
    return {
        "basic_auth_cloud": {
            "CONFLUENCE_URL": "https://company.atlassian.net",
            "CONFLUENCE_USERNAME": "user@company.com",
            "CONFLUENCE_API_TOKEN": "api_token",
        },
        "pat_server": {
            "CONFLUENCE_URL": "https://confluence.company.com",
            "CONFLUENCE_PERSONAL_TOKEN": "pat_token",
        },
        "basic_auth_server": {
            "CONFLUENCE_URL": "https://confluence.company.com",
            "CONFLUENCE_USERNAME": "admin",
            "CONFLUENCE_API_TOKEN": "password",
        },
    }


def _assert_service_availability(result, confluence_expected):
    """Helper to assert service availability."""
    assert result == {"confluence": confluence_expected}


def _assert_authentication_logs(caplog, auth_type, services):
    """Helper to assert authentication log messages."""
    log_patterns = {
        "cloud_basic": "Cloud Basic Authentication (API Token)",
        "server": "Server/Data Center authentication (PAT or Basic Auth)",
        "not_configured": "is not configured or required environment variables are missing",
    }

    for service in services:
        service_name = service.title()
        if auth_type == "not_configured":
            assert_log_contains(
                caplog, "INFO", f"{service_name} {log_patterns[auth_type]}"
            )
        else:
            assert_log_contains(
                caplog, "INFO", f"Using {service_name} {log_patterns[auth_type]}"
            )


class TestGetAvailableServices:
    """Test cases for get_available_services function."""

    def test_no_services_configured(self, caplog):
        """Test that no services are available when no environment variables are set."""
        with MockEnvironment.clean_env():
            result = get_available_services()
            _assert_service_availability(result, confluence_expected=False)
            _assert_authentication_logs(caplog, "not_configured", ["confluence"])

    @pytest.mark.parametrize(
        "scenario,expected_confluence",
        [
            ("basic_auth_cloud", True),
            ("pat_server", True),
            ("basic_auth_server", True),
        ],
    )
    def test_valid_authentication_scenarios(
        self, env_scenarios, scenario, expected_confluence, caplog
    ):
        """Test various valid authentication scenarios."""
        with MockEnvironment.clean_env():
            for key, value in env_scenarios[scenario].items():
                import os

                os.environ[key] = value

            result = get_available_services()
            _assert_service_availability(
                result,
                confluence_expected=expected_confluence,
            )

            # Verify appropriate log messages based on scenario
            if scenario == "basic_auth_cloud":
                _assert_authentication_logs(caplog, "cloud_basic", ["confluence"])
            elif scenario in ["pat_server", "basic_auth_server"]:
                _assert_authentication_logs(caplog, "server", ["confluence"])

    @pytest.mark.parametrize(
        "missing_basic_vars",
        [
            ["CONFLUENCE_USERNAME"],
            ["CONFLUENCE_API_TOKEN"],
        ],
    )
    def test_basic_auth_missing_credentials(
        self, env_scenarios, missing_basic_vars
    ):
        """Test that basic auth fails when credentials are missing."""
        with MockEnvironment.clean_env():
            basic_config = env_scenarios["basic_auth_cloud"].copy()

            # Remove required variables
            for var in missing_basic_vars:
                del basic_config[var]

            for key, value in basic_config.items():
                import os

                os.environ[key] = value

            result = get_available_services()
            _assert_service_availability(result, confluence_expected=False)

    def test_return_value_structure(self):
        """Test that the return value has the correct structure."""
        with MockEnvironment.clean_env():
            result = get_available_services()

            assert isinstance(result, dict)
            assert set(result.keys()) == {"confluence"}
            assert all(isinstance(v, bool) for v in result.values())

    @pytest.mark.parametrize(
        "invalid_vars",
        [
            {"CONFLUENCE_URL": ""},  # Empty strings
            {"confluence_url": "https://test.com"},  # Wrong case
        ],
    )
    def test_invalid_environment_variables(self, invalid_vars, caplog):
        """Test behavior with invalid environment variables."""
        with MockEnvironment.clean_env():
            for key, value in invalid_vars.items():
                import os

                os.environ[key] = value

            result = get_available_services()
            _assert_service_availability(result, confluence_expected=False)
            _assert_authentication_logs(caplog, "not_configured", ["confluence"])
