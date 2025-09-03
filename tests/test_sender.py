"""Comprehensive tests for the sender module."""

import os
import tempfile
from unittest.mock import Mock, patch, call

import pytest
from http.client import RemoteDisconnected, BadStatusLine
from urllib3.exceptions import ProtocolError, NewConnectionError, ConnectTimeoutError


from pytest_ibutsu.sender import (
    IbutsuSender,
    send_data_to_ibutsu,
    UPLOAD_LIMIT,
    CA_BUNDLE_ENVS,
    MAX_CALL_RETRIES,
)
from pytest_ibutsu.modeling import TestRun, TestResult


pytest_plugins = "pytester"


class TestIbutsuSender:
    """Test the IbutsuSender class methods."""

    @patch("pytest_ibutsu.sender.ApiClient")
    def test_init_basic(self, mock_api_client):
        """Test basic initialization without CA bundle."""
        sender = IbutsuSender("http://example.com/api", "test-token")

        assert sender._has_server_error is False
        assert sender._server_error_tbs == []
        assert sender._sender_cache == []
        mock_api_client.assert_called_once()

    @patch("pytest_ibutsu.sender.ApiClient")
    def test_init_with_ca_bundle(self, mock_api_client, monkeypatch):
        """Test initialization with CA bundle environment variables."""
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/path/to/cert.pem")

        _ = IbutsuSender("http://example.com/api")

        # Verify configuration was called with ssl_ca_cert
        config_arg = mock_api_client.call_args[0][0]
        assert config_arg.ssl_ca_cert == "/path/to/cert.pem"

    @patch("pytest_ibutsu.sender.ApiClient")
    def test_init_with_ibutsu_ca_bundle(self, mock_api_client, monkeypatch):
        """Test initialization with IBUTSU_CA_BUNDLE environment variable."""
        monkeypatch.setenv("IBUTSU_CA_BUNDLE", "/custom/cert.pem")

        _ = IbutsuSender("http://example.com/api")

        config_arg = mock_api_client.call_args[0][0]
        assert config_arg.ssl_ca_cert == "/custom/cert.pem"

    def test_from_ibutsu_plugin(self):
        """Test creating IbutsuSender from IbutsuPlugin."""
        mock_plugin = Mock()
        mock_plugin.ibutsu_server = "http://example.com"
        mock_plugin.ibutsu_token = "test-token"

        with patch.object(IbutsuSender, "__init__", return_value=None) as mock_init:
            _ = IbutsuSender.from_ibutsu_plugin(mock_plugin)
            mock_init.assert_called_once_with(
                server_url="http://example.com/api", token="test-token"
            )

    def test_from_ibutsu_plugin_with_trailing_slash(self):
        """Test URL handling with trailing slash."""
        mock_plugin = Mock()
        mock_plugin.ibutsu_server = "http://example.com/"
        mock_plugin.ibutsu_token = "test-token"

        with patch.object(IbutsuSender, "__init__", return_value=None) as mock_init:
            _ = IbutsuSender.from_ibutsu_plugin(mock_plugin)
            mock_init.assert_called_once_with(
                server_url="http://example.com/api", token="test-token"
            )

    def test_from_ibutsu_plugin_with_api_suffix(self):
        """Test URL handling when /api already present."""
        mock_plugin = Mock()
        mock_plugin.ibutsu_server = "http://example.com/api"
        mock_plugin.ibutsu_token = "test-token"

        with patch.object(IbutsuSender, "__init__", return_value=None) as mock_init:
            _ = IbutsuSender.from_ibutsu_plugin(mock_plugin)
            mock_init.assert_called_once_with(
                server_url="http://example.com/api", token="test-token"
            )

    def test_frontend_url_property(self):
        """Test frontend_url property."""
        sender = IbutsuSender("http://example.com/api")

        # Mock the health API response
        mock_health_info = Mock()
        mock_health_info.frontend = "http://frontend.example.com"
        sender.health_api.get_health_info = Mock(return_value=mock_health_info)

        assert sender.frontend_url == "http://frontend.example.com"

    def test_get_buffered_reader_with_bytes(self):
        """Test _get_buffered_reader with bytes input."""
        data = b"test content"
        filename = "test.txt"

        reader, size = IbutsuSender._get_buffered_reader(data, filename)

        assert size == len(data)
        assert reader.name == filename
        assert reader.read() == data
        reader.close()

    def test_get_buffered_reader_with_file_path(self):
        """Test _get_buffered_reader with file path."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("test content")
            file_path = f.name

        try:
            reader, size = IbutsuSender._get_buffered_reader(file_path, "test.txt")

            assert size == len("test content")
            assert reader.read() == b"test content"
            reader.close()
        finally:
            os.unlink(file_path)

    def test_does_run_exist_true(self):
        """Test does_run_exist when run exists."""
        sender = IbutsuSender("http://example.com/api")
        run = TestRun(id="test-run-id")

        sender._make_call = Mock(return_value={"id": "test-run-id"})

        assert sender.does_run_exist(run) is True
        sender._make_call.assert_called_once_with(
            sender.run_api.get_run, id="test-run-id"
        )

    def test_does_run_exist_false(self):
        """Test does_run_exist when run doesn't exist."""
        sender = IbutsuSender("http://example.com/api")
        run = TestRun(id="test-run-id")

        sender._make_call = Mock(return_value=None)

        assert sender.does_run_exist(run) is False

    def test_add_or_update_run_existing(self):
        """Test add_or_update_run when run exists."""
        sender = IbutsuSender("http://example.com/api")
        run = TestRun(id="test-run-id")

        sender.does_run_exist = Mock(return_value=True)
        sender._make_call = Mock()

        sender.add_or_update_run(run)

        sender._make_call.assert_called_once_with(
            sender.run_api.update_run, id=run.id, run=run.to_dict()
        )

    def test_add_or_update_run_new(self):
        """Test add_or_update_run when run is new."""
        sender = IbutsuSender("http://example.com/api")
        run = TestRun(id="test-run-id")

        sender.does_run_exist = Mock(return_value=False)
        sender._make_call = Mock()

        sender.add_or_update_run(run)

        sender._make_call.assert_called_once_with(
            sender.run_api.add_run, run=run.to_dict()
        )

    def test_add_result(self):
        """Test add_result method."""
        sender = IbutsuSender("http://example.com/api")
        result = TestResult(test_id="test-result")

        sender._make_call = Mock()

        sender.add_result(result)

        sender._make_call.assert_called_once_with(
            sender.result_api.add_result, result=result.to_dict()
        )

    def test_upload_artifacts_run(self):
        """Test upload_artifacts for TestRun."""
        sender = IbutsuSender("http://example.com/api")
        run = TestRun(id="test-run")
        run.attach_artifact("test.txt", b"content")

        sender._upload_artifact = Mock()

        sender.upload_artifacts(run)

        sender._upload_artifact.assert_called_once_with(
            "test-run", "test.txt", b"content", True
        )

    def test_upload_artifacts_result(self):
        """Test upload_artifacts for TestResult."""
        sender = IbutsuSender("http://example.com/api")
        result = TestResult(test_id="test-result")
        result.attach_artifact("log.txt", b"log content")

        sender._upload_artifact = Mock()

        sender.upload_artifacts(result)

        sender._upload_artifact.assert_called_once_with(
            result.id, "log.txt", b"log content", False
        )

    def test_upload_artifacts_file_not_found(self):
        """Test upload_artifacts continues when file not found."""
        sender = IbutsuSender("http://example.com/api")
        result = TestResult(test_id="test-result")
        result.attach_artifact("missing.txt", "/nonexistent/file.txt")

        sender._upload_artifact = Mock(side_effect=FileNotFoundError())

        # Should not raise exception
        sender.upload_artifacts(result)

        sender._upload_artifact.assert_called_once()

    @patch("builtins.print")
    def test_upload_artifact_under_limit(self, mock_print):
        """Test _upload_artifact with file under size limit."""
        sender = IbutsuSender("http://example.com/api")
        sender._make_call = Mock()

        content = b"small content"
        sender._upload_artifact("result-id", "test.txt", content, False)

        sender._make_call.assert_called_once()
        # Verify it's called with artifact API
        args, kwargs = sender._make_call.call_args
        assert args[0] == sender.artifact_api.upload_artifact

    @patch("builtins.print")
    def test_upload_artifact_over_limit(self, mock_print):
        """Test _upload_artifact with file over size limit."""
        sender = IbutsuSender("http://example.com/api")
        sender._make_call = Mock()

        # Create content larger than UPLOAD_LIMIT
        large_content = b"x" * (UPLOAD_LIMIT + 1)
        sender._upload_artifact("result-id", "large.txt", large_content, False)

        # Should not call API due to size limit
        sender._make_call.assert_not_called()
        mock_print.assert_called_with("Artifact size is greater than upload limit")

    @patch("builtins.print")
    def test_upload_artifact_api_value_error(self, mock_print):
        """Test _upload_artifact handling ApiValueError."""
        from ibutsu_client.exceptions import ApiValueError

        sender = IbutsuSender("http://example.com/api")
        sender._make_call = Mock(side_effect=ApiValueError("File closed"))

        content = b"test content"
        sender._upload_artifact("result-id", "test.txt", content, False)

        mock_print.assert_called_with(
            "Uploading artifact 'test.txt' failed as the file closed prematurely."
        )


class TestSendDataToIbutsu:
    """Test the send_data_to_ibutsu function."""

    @patch.object(IbutsuSender, "from_ibutsu_plugin")
    def test_send_data_success(self, mock_from_plugin):
        """Test successful data sending."""
        # Mock the sender
        mock_sender = Mock()
        mock_sender._has_server_error = False
        mock_sender.frontend_url = "http://frontend.example.com"
        mock_from_plugin.return_value = mock_sender

        # Mock the plugin
        mock_plugin = Mock()
        mock_plugin.run = TestRun(id="test-run")
        mock_plugin.results = {
            "test1": TestResult(test_id="test1"),
            "test2": TestResult(test_id="test2"),
        }

        send_data_to_ibutsu(mock_plugin)

        # Verify sender methods were called
        assert mock_sender.add_or_update_run.call_count == 2  # Called twice
        assert mock_sender.upload_artifacts.call_count == 3  # Run + 2 results
        assert mock_sender.add_result.call_count == 2  # 2 results

    @patch.object(IbutsuSender, "from_ibutsu_plugin")
    @patch("builtins.print")
    def test_send_data_success_with_frontend_url(self, mock_print, mock_from_plugin):
        """Test successful data sending prints frontend URL."""
        mock_sender = Mock()
        mock_sender._has_server_error = False
        mock_sender.frontend_url = "http://frontend.example.com"
        mock_from_plugin.return_value = mock_sender

        mock_plugin = Mock()
        mock_plugin.run = TestRun(id="test-run-123")
        mock_plugin.results = {}

        send_data_to_ibutsu(mock_plugin)

        mock_print.assert_called_with(
            "Results can be viewed on: http://frontend.example.com/runs/test-run-123"
        )

    @patch.object(IbutsuSender, "from_ibutsu_plugin")
    @patch("builtins.print")
    def test_send_data_with_server_error(self, mock_print, mock_from_plugin):
        """Test data sending with server error doesn't print URL."""
        mock_sender = Mock()
        mock_sender._has_server_error = True
        mock_from_plugin.return_value = mock_sender

        mock_plugin = Mock()
        mock_plugin.run = TestRun(id="test-run")
        mock_plugin.results = {}

        send_data_to_ibutsu(mock_plugin)

        # Should not print success message
        mock_print.assert_not_called()


class TestCABundleHandling:
    """Test CA bundle environment variable handling."""

    def test_ca_bundle_env_vars_constant(self):
        """Test that CA_BUNDLE_ENVS contains expected values."""
        assert "REQUESTS_CA_BUNDLE" in CA_BUNDLE_ENVS
        assert "IBUTSU_CA_BUNDLE" in CA_BUNDLE_ENVS

    @patch("pytest_ibutsu.sender.ApiClient")
    def test_no_ca_bundle_set(self, mock_api_client, monkeypatch):
        """Test initialization when no CA bundle env vars are set."""
        # Ensure no CA bundle env vars are set
        for env_var in CA_BUNDLE_ENVS:
            monkeypatch.delenv(env_var, raising=False)

        _ = IbutsuSender("http://example.com/api")

        config_arg = mock_api_client.call_args[0][0]
        # ssl_ca_cert should not be set
        assert not hasattr(config_arg, "ssl_ca_cert") or config_arg.ssl_ca_cert is None


class TestSenderRetry:
    """Test retry functionality for network failures in IbutsuSender."""

    def test_successful_call_no_retry(self):
        """Test that successful calls don't trigger retry logic."""
        sender = IbutsuSender("http://example.com/api")
        mock_method = Mock(return_value="success")

        result = sender._make_call(mock_method, "arg1", kwarg1="value1")

        assert result == "success"
        mock_method.assert_called_once_with("arg1", kwarg1="value1")

    @patch("pytest_ibutsu.sender.time.sleep")
    def test_retry_on_network_errors(self, mock_sleep):
        """Test that network errors trigger retry with exponential backoff."""
        sender = IbutsuSender("http://example.com/api")
        mock_method = Mock()

        # Fail twice, then succeed
        mock_method.side_effect = [
            RemoteDisconnected("Connection closed"),
            ProtocolError("Protocol error"),
            "success",
        ]

        result = sender._make_call(mock_method, "test_arg")

        assert result == "success"
        assert mock_method.call_count == 3

        # Check that sleep was called with exponential backoff
        expected_calls = [call(1.0), call(2.0)]  # 1.0 * 2^0, 1.0 * 2^1
        mock_sleep.assert_has_calls(expected_calls)

    @patch("pytest_ibutsu.sender.time.sleep")
    def test_max_retries_exceeded(self, mock_sleep):
        """Test that TooManyRetriesError is raised when max retries is exceeded."""
        sender = IbutsuSender("http://example.com/api")
        mock_method = Mock()

        # Always fail with network error
        mock_method.side_effect = RemoteDisconnected("Connection always fails")

        result = sender._make_call(mock_method, "test_arg")

        # Should return None due to TooManyRetriesError being caught
        assert result is None
        assert mock_method.call_count == MAX_CALL_RETRIES
        assert sender._has_server_error is True
        assert len(sender._server_error_tbs) == 1

        # Check exponential backoff delays
        expected_calls = [call(1.0), call(2.0)]  # Only 2 delays for 3 attempts
        mock_sleep.assert_has_calls(expected_calls)

    @pytest.mark.parametrize(
        "exception_type,exception_args",
        [
            (RemoteDisconnected, ("Test error",)),
            (ProtocolError, ("Test error",)),
            (BadStatusLine, ("Test error",)),
            (
                NewConnectionError,
                (Mock(), "Test error"),
            ),  # NewConnectionError needs a pool and message
            (ConnectTimeoutError, ("Test error",)),
        ],
    )
    @patch("pytest_ibutsu.sender.time.sleep")
    def test_retry_on_different_network_exceptions(
        self, mock_sleep, exception_type, exception_args
    ):
        """Test that all expected network exceptions trigger retry."""
        sender = IbutsuSender("http://example.com/api")
        mock_method = Mock()

        # Fail once with the specific exception, then succeed
        mock_method.side_effect = [exception_type(*exception_args), "success"]

        result = sender._make_call(mock_method)

        assert result == "success"
        assert mock_method.call_count == 2
        mock_sleep.assert_called_once_with(1.0)

    @patch("pytest_ibutsu.sender.time.sleep")
    @patch("builtins.print")
    def test_retry_logging(self, mock_print, mock_sleep):
        """Test that retry attempts are properly logged."""
        sender = IbutsuSender("http://example.com/api")
        mock_method = Mock()

        # Fail twice, then succeed
        mock_method.side_effect = [
            RemoteDisconnected("Connection failed"),
            ProtocolError("Protocol failed"),
            "success",
        ]

        result = sender._make_call(mock_method)

        assert result == "success"

        # Check that proper log messages were printed
        print_calls = [call.args[0] for call in mock_print.call_args_list]

        # Should have 2 retry log messages
        assert len(print_calls) == 2
        assert (
            "Network error (attempt 1/3): RemoteDisconnected: Connection failed. Retrying in 1.0 seconds..."
            in print_calls[0]
        )
        assert (
            "Network error (attempt 2/3): ProtocolError: Protocol failed. Retrying in 2.0 seconds..."
            in print_calls[1]
        )

    def test_non_retryable_exceptions_not_retried(self):
        """Test that non-network exceptions are not retried."""
        sender = IbutsuSender("http://example.com/api")
        mock_method = Mock()

        # Raise a non-retryable exception
        mock_method.side_effect = ValueError("Not a network error")

        with pytest.raises(ValueError):
            sender._make_call(mock_method)

        # Should only be called once (no retry)
        assert mock_method.call_count == 1

    @patch("pytest_ibutsu.sender.time.sleep")
    def test_async_request_caching_with_retry(self, mock_sleep):
        """Test that async requests are still cached properly after retry."""
        sender = IbutsuSender("http://example.com/api")
        mock_method = Mock()
        mock_result = Mock()
        mock_result.ready.return_value = False

        # Fail once, then succeed with async result
        mock_method.side_effect = [RemoteDisconnected("Connection failed"), mock_result]

        result = sender._make_call(mock_method, async_req=True)

        assert result == mock_result
        assert mock_result in sender._sender_cache
        mock_sleep.assert_called_once_with(1.0)

    @patch("pytest_ibutsu.sender.time.sleep")
    def test_exponential_backoff_calculation(self, mock_sleep):
        """Test that exponential backoff delays are calculated correctly."""
        sender = IbutsuSender("http://example.com/api")
        mock_method = Mock()

        # Fail exactly MAX_CALL_RETRIES times to test all delays
        mock_method.side_effect = [RemoteDisconnected("Fail")] * MAX_CALL_RETRIES

        result = sender._make_call(mock_method)

        assert result is None  # Should fail after max retries

        # Check exponential backoff: 1.0, 2.0 (for 3 total attempts)
        expected_delays = [1.0 * (2.0**i) for i in range(MAX_CALL_RETRIES - 1)]
        expected_calls = [call(delay) for delay in expected_delays]
        mock_sleep.assert_has_calls(expected_calls)
