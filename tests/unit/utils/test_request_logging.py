"""Tests for request logging utility."""

from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mcp_atlassian.utils.request_logging import (
    REQUESTS_LOG_FILE,
    install_request_logging,
    is_request_logging_enabled,
    log_request,
)


def test_log_request_writes_to_file(tmp_path, monkeypatch):
    """Test that log_request writes request info to the log file."""
    # Change to temp directory so log file is created there
    monkeypatch.chdir(tmp_path)

    # Create mock response
    mock_request = MagicMock()
    mock_request.method = "GET"
    mock_request.url = "https://example.com/api/test"

    mock_response = MagicMock()
    mock_response.request = mock_request
    mock_response.status_code = 200
    mock_response.elapsed = timedelta(milliseconds=150)

    # Call the log function
    log_request(mock_response)

    # Verify log file was created and contains expected content
    log_path = tmp_path / REQUESTS_LOG_FILE
    assert log_path.exists()

    content = log_path.read_text()
    assert "GET" in content
    assert "200" in content
    assert "150ms" in content
    assert "https://example.com/api/test" in content


def test_log_request_appends_multiple_requests(tmp_path, monkeypatch):
    """Test that multiple requests are appended to the log file."""
    monkeypatch.chdir(tmp_path)

    # Log two requests
    for i, (method, status) in enumerate([("GET", 200), ("POST", 201)]):
        mock_request = MagicMock()
        mock_request.method = method
        mock_request.url = f"https://example.com/api/{i}"

        mock_response = MagicMock()
        mock_response.request = mock_request
        mock_response.status_code = status
        mock_response.elapsed = timedelta(milliseconds=100)

        log_request(mock_response)

    log_path = tmp_path / REQUESTS_LOG_FILE
    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 2
    assert "GET" in lines[0]
    assert "POST" in lines[1]


def test_is_request_logging_enabled_false_by_default(monkeypatch):
    """Test that request logging is disabled by default."""
    monkeypatch.delenv("CONFLUENCE_LOG_REQUESTS", raising=False)
    assert is_request_logging_enabled() is False


def test_is_request_logging_enabled_true(monkeypatch):
    """Test that request logging can be enabled via env var."""
    monkeypatch.setenv("CONFLUENCE_LOG_REQUESTS", "true")
    assert is_request_logging_enabled() is True

    monkeypatch.setenv("CONFLUENCE_LOG_REQUESTS", "1")
    assert is_request_logging_enabled() is True

    monkeypatch.setenv("CONFLUENCE_LOG_REQUESTS", "yes")
    assert is_request_logging_enabled() is True


def test_install_request_logging_adds_hook(monkeypatch):
    """Test that install_request_logging adds the hook to session when enabled."""
    monkeypatch.setenv("CONFLUENCE_LOG_REQUESTS", "true")

    mock_session = MagicMock()
    mock_session.hooks = {"response": []}

    install_request_logging(mock_session)

    assert log_request in mock_session.hooks["response"]


def test_install_request_logging_skips_when_disabled(monkeypatch):
    """Test that install_request_logging does nothing when disabled."""
    monkeypatch.delenv("CONFLUENCE_LOG_REQUESTS", raising=False)

    mock_session = MagicMock()
    mock_session.hooks = {"response": []}

    install_request_logging(mock_session)

    assert log_request not in mock_session.hooks["response"]


def test_install_request_logging_no_duplicate_hooks(monkeypatch):
    """Test that hook is not added twice."""
    monkeypatch.setenv("CONFLUENCE_LOG_REQUESTS", "true")

    mock_session = MagicMock()
    mock_session.hooks = {"response": []}

    install_request_logging(mock_session)
    install_request_logging(mock_session)

    assert mock_session.hooks["response"].count(log_request) == 1


def test_log_request_handles_errors_gracefully(tmp_path, monkeypatch):
    """Test that logging errors don't crash the application."""
    monkeypatch.chdir(tmp_path)

    # Create a response that will cause an error
    mock_response = MagicMock()
    mock_response.request = None  # This will cause an AttributeError

    # Should not raise an exception
    log_request(mock_response)
