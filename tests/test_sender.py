"""Comprehensive tests for the sender module."""

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
from pytest_ibutsu.modeling import IbutsuTestRun, IbutsuTestResult


pytest_plugins = "pytester"


def _create_data_capturing_sender():
    """Helper to create a sender that captures data content during _make_call."""
    sender = IbutsuSender("http://example.com/api")
    captured_data_content = None

    def capture_data(*args, **kwargs):
        nonlocal captured_data_content
        captured_data_content = kwargs.get("file", captured_data_content)

    sender._make_call = Mock(side_effect=capture_data)
    sender._captured_data_content = lambda: captured_data_content
    return sender


def _create_multi_data_capturing_sender():
    """Helper to create a sender that captures multiple data contents during _make_call."""
    sender = IbutsuSender("http://example.com/api")
    captured_calls = []

    def capture_data(*args, **kwargs):
        data = kwargs.get("file")
        # Store the call with captured content
        captured_calls.append({"args": args, "kwargs": kwargs, "data_content": data})

    sender._make_call = Mock(side_effect=capture_data)
    sender._captured_calls = lambda: captured_calls
    return sender


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

    def test_add_or_update_run_existing(self):
        """Test add_or_update_run when run exists."""
        sender = IbutsuSender("http://example.com/api")
        run = IbutsuTestRun(id="test-run-id")

        # Mock _make_call to return a truthy value for get_run (run exists) and None for update_run
        sender._make_call = Mock(side_effect=[{"id": "test-run-id"}, None])

        sender.add_or_update_run(run)

        # Should be called twice: once to check if run exists, once to update
        assert sender._make_call.call_count == 2
        sender._make_call.assert_any_call(
            sender.run_api.get_run, hide_exception=True, id=run.id
        )
        sender._make_call.assert_any_call(
            sender.run_api.update_run, id=run.id, run=run.to_dict()
        )

    def test_add_or_update_run_new(self):
        """Test add_or_update_run when run is new."""
        sender = IbutsuSender("http://example.com/api")
        run = IbutsuTestRun(id="test-run-id")

        # Mock _make_call to return None for get_run (run doesn't exist) and None for add_run
        sender._make_call = Mock(side_effect=[None, None])

        sender.add_or_update_run(run)

        # Should be called twice: once to check if run exists, once to add
        assert sender._make_call.call_count == 2
        sender._make_call.assert_any_call(
            sender.run_api.get_run, hide_exception=True, id=run.id
        )
        sender._make_call.assert_any_call(sender.run_api.add_run, run=run.to_dict())

    def test_add_result(self):
        """Test add_result method."""
        sender = IbutsuSender("http://example.com/api")
        result = IbutsuTestResult(test_id="test-result")

        sender._make_call = Mock()

        sender.add_result(result)

        sender._make_call.assert_called_once_with(
            sender.result_api.add_result, result=result.to_dict()
        )

    def test_upload_artifacts_run(self):
        """Test upload_artifacts for IbutsuTestRun."""
        sender = IbutsuSender("http://example.com/api")
        run = IbutsuTestRun(id="test-run")
        run.attach_artifact("test.txt", b"content")

        sender._upload_artifact = Mock()

        sender.upload_artifacts(run)

        sender._upload_artifact.assert_called_once_with(
            "test-run", "test.txt", b"content", True
        )

    def test_upload_artifacts_result(self):
        """Test upload_artifacts for IbutsuTestResult."""
        sender = IbutsuSender("http://example.com/api")
        result = IbutsuTestResult(test_id="test-result")
        result.attach_artifact("log.txt", b"log content")

        sender._upload_artifact = Mock()

        sender.upload_artifacts(result)

        sender._upload_artifact.assert_called_once_with(
            result.id, "log.txt", b"log content", False
        )

    def test_upload_artifacts_file_not_found(self):
        """Test upload_artifacts continues when file not found."""
        sender = IbutsuSender("http://example.com/api")
        result = IbutsuTestResult(test_id="test-result")
        result.attach_artifact("missing.txt", "/nonexistent/file.txt")

        sender._upload_artifact = Mock(side_effect=FileNotFoundError())

        # Should not raise exception
        sender.upload_artifacts(result)

        sender._upload_artifact.assert_called_once()

    def test_upload_artifact_bytes_under_limit(self):
        """Test _upload_artifact with bytes data under size limit."""
        sender = _create_data_capturing_sender()

        content = b"small content"
        sender._upload_artifact("result-id", "test.txt", content, False)

        sender._make_call.assert_called_once()
        # Verify it's called with artifact API and correct arguments
        args, kwargs = sender._make_call.call_args
        assert args[0] == sender.artifact_api.upload_artifact
        assert kwargs["filename"] == "test.txt"
        # Verify the data content was captured correctly
        assert sender._captured_data_content() == content  # data passed directly
        assert kwargs["result_id"] == "result-id"

    def test_upload_artifact_string_content(self):
        """Test _upload_artifact with string content (not a file path)."""
        sender = _create_data_capturing_sender()

        content = "text content"
        sender._upload_artifact("result-id", "test.txt", content, False)

        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        assert args[0] == sender.artifact_api.upload_artifact
        assert kwargs["filename"] == "test.txt"
        # Verify the data content was captured correctly - should be passed as string
        assert sender._captured_data_content() == content  # String passed directly
        assert kwargs["result_id"] == "result-id"

    def test_upload_artifact_file_path(self, tmp_path):
        """Test _upload_artifact with file path - should read file content."""
        sender = _create_data_capturing_sender()

        # Create a test file
        test_file = tmp_path / "test_content.txt"
        test_content = "file content from disk"
        test_file.write_text(test_content)

        sender._upload_artifact("result-id", "test.txt", str(test_file), False)

        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        assert args[0] == sender.artifact_api.upload_artifact
        assert kwargs["filename"] == "test.txt"
        # Verify the data content was captured correctly - should be file content as bytes
        assert (
            sender._captured_data_content() == test_content.encode()
        )  # Should be read as bytes
        assert kwargs["result_id"] == "result-id"

    def test_upload_artifact_binary_file_path(self, tmp_path):
        """Test _upload_artifact with binary file path (simulating image upload)."""
        sender = _create_data_capturing_sender()

        # Create a binary file (simulating an image)
        test_file = tmp_path / "test_image.png"
        binary_content = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"  # PNG header
        )
        test_file.write_bytes(binary_content)

        sender._upload_artifact("result-id", "test_image.png", str(test_file), False)

        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        assert args[0] == sender.artifact_api.upload_artifact
        assert kwargs["filename"] == "test_image.png"
        # Verify the data content was captured correctly
        assert (
            sender._captured_data_content() == binary_content
        )  # Should be read as bytes
        assert kwargs["result_id"] == "result-id"

    def test_upload_artifact_non_utf8_file_path(self, tmp_path):
        """Test _upload_artifact with non-UTF-8 encoded files."""
        sender = _create_data_capturing_sender()

        # Create a file with latin-1 encoding
        test_file = tmp_path / "latin1_file.txt"
        latin1_content = "Café con leña"  # Contains non-ASCII characters
        test_file.write_bytes(latin1_content.encode("latin-1"))

        sender._upload_artifact("result-id", "latin1_file.txt", str(test_file), False)

        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        assert args[0] == sender.artifact_api.upload_artifact
        assert kwargs["filename"] == "latin1_file.txt"
        # Verify the data content was captured correctly
        assert sender._captured_data_content() == latin1_content.encode("latin-1")
        assert kwargs["result_id"] == "result-id"

    def test_upload_artifact_bytes_over_limit(self, caplog):
        """Test _upload_artifact with bytes data over size limit."""
        sender = IbutsuSender("http://example.com/api")
        sender._make_call = Mock()

        # Create content larger than UPLOAD_LIMIT
        large_content = b"x" * (UPLOAD_LIMIT + 1)
        sender._upload_artifact("result-id", "large.txt", large_content, False)

        # Should not call API due to size limit
        sender._make_call.assert_not_called()
        assert "Artifact size is greater than upload limit" in caplog.text

    def test_upload_artifact_file_over_limit(self, tmp_path, caplog):
        """Test _upload_artifact with file path over size limit."""
        sender = IbutsuSender("http://example.com/api")
        sender._make_call = Mock()

        # Create a large file
        test_file = tmp_path / "large_file.txt"
        large_content = "x" * (UPLOAD_LIMIT + 1)
        test_file.write_text(large_content)

        sender._upload_artifact("result-id", "large_file.txt", str(test_file), False)

        # Should not call API due to size limit
        sender._make_call.assert_not_called()
        assert "Artifact size is greater than upload limit" in caplog.text

    def test_upload_artifact_run_id_parameter(self):
        """Test _upload_artifact with is_run=True sets run_id parameter."""
        sender = IbutsuSender("http://example.com/api")
        sender._make_call = Mock()

        content = b"run artifact content"
        sender._upload_artifact("run-id", "run_artifact.log", content, True)

        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        assert kwargs["run_id"] == "run-id"
        assert "result_id" not in kwargs

    def test_upload_artifact_api_value_error(self, caplog):
        """Test _upload_artifact handling ApiValueError."""
        from ibutsu_client.exceptions import ApiValueError

        sender = IbutsuSender("http://example.com/api")
        sender._make_call = Mock(side_effect=ApiValueError("File closed"))

        content = b"test content"
        sender._upload_artifact("result-id", "test.txt", content, False)

        assert (
            "Uploading artifact 'test.txt' failed as the file closed prematurely."
            in caplog.text
        )

    def test_upload_artifact_file_not_found(self, caplog):
        """Test _upload_artifact handling file not found."""
        sender = _create_data_capturing_sender()

        # Try to upload a non-existent file
        nonexistent_file = "/path/that/does/not/exist.txt"
        sender._upload_artifact("result-id", "missing.txt", nonexistent_file, False)

        # Should still call the API with the string data (fallback behavior)
        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        # Verify the data content was captured correctly - non-existent file treated as string
        assert (
            sender._captured_data_content() == nonexistent_file
        )  # String passed directly

    def test_upload_artifact_url_string_not_treated_as_file(self):
        """Test that URL strings are not treated as file paths."""
        sender = _create_data_capturing_sender()

        url_content = "http://example.com/some/resource"
        sender._upload_artifact("result-id", "url.txt", url_content, False)

        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        # Verify the data content was captured correctly - URL treated as string
        assert (
            sender._captured_data_content() == url_content
        )  # URL string passed directly

    def test_upload_artifact_https_string_not_treated_as_file(self):
        """Test that HTTPS URL strings are not treated as file paths."""
        sender = _create_data_capturing_sender()

        url_content = "https://example.com/some/resource"
        sender._upload_artifact("result-id", "url.txt", url_content, False)

        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        # Verify the data content was captured correctly - HTTPS URL treated as string
        assert (
            sender._captured_data_content() == url_content
        )  # HTTPS URL string passed directly

    def test_upload_artifact_permission_error(self, caplog):
        """Test _upload_artifact handling PermissionError when accessing files."""
        sender = IbutsuSender("http://example.com/api")
        sender._make_call = Mock()

        # Mock Path to raise PermissionError on both stat() and read_bytes()
        with patch("pytest_ibutsu.sender.Path") as mock_path:
            mock_path_instance = Mock()
            mock_path.return_value = mock_path_instance
            mock_path_instance.is_file.return_value = True
            # Make stat() raise PermissionError so size check fails early
            mock_path_instance.stat.side_effect = PermissionError("Permission denied")
            mock_path_instance.read_bytes.side_effect = PermissionError(
                "Permission denied"
            )

            restricted_file = "/restricted/file.txt"
            sender._upload_artifact(
                "result-id", "restricted.txt", restricted_file, False
            )

        # Should not call API due to permission error
        sender._make_call.assert_not_called()
        assert "Permission denied when accessing artifact file" in caplog.text
        assert restricted_file in caplog.text

    def test_upload_artifact_file_path_over_limit(self, tmp_path, caplog):
        """Test _upload_artifact with file path over size limit."""
        sender = IbutsuSender("http://example.com/api")
        sender._make_call = Mock()

        # Create a large file
        test_file = tmp_path / "large_file.txt"
        large_content = "x" * (UPLOAD_LIMIT + 1)
        test_file.write_text(large_content)

        sender._upload_artifact("result-id", "large_file.txt", str(test_file), False)

        # Should not call API due to size limit
        sender._make_call.assert_not_called()
        assert "Artifact size is greater than upload limit" in caplog.text

    def test_upload_artifact_empty_string(self):
        """Test that uploading an empty string is handled correctly."""
        sender = _create_data_capturing_sender()

        empty_string = ""
        sender._upload_artifact("result-id", "empty.txt", empty_string, False)

        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        assert args[0] == sender.artifact_api.upload_artifact
        assert kwargs["filename"] == "empty.txt"
        # Verify the data content was captured correctly - empty string passed as string
        assert sender._captured_data_content() == ""  # Empty string passed directly

    def test_upload_artifact_data_none(self):
        """Test that uploading artifact with data=None is handled gracefully."""
        sender = _create_data_capturing_sender()

        # None data should be converted to string representation
        sender._upload_artifact("result-id", "none_data.txt", None, False)

        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        assert args[0] == sender.artifact_api.upload_artifact
        assert kwargs["filename"] == "none_data.txt"
        # None gets converted to "None" string
        assert sender._captured_data_content() == "None"


class TestArtifactUploadIntegration:
    """Integration tests for artifact uploads simulating real iqe-core usage."""

    def test_text_log_upload_like_iqe_core(self, tmp_path):
        """Test text log upload like iqe-core does with iqe.log."""
        sender = _create_data_capturing_sender()

        # Simulate iqe-core creating a log entry and attaching it as bytes
        log_content = "2025-09-24 22:32:27 INFO Starting test execution\n2025-09-24 22:32:28 DEBUG Test step 1\n2025-09-24 22:32:29 ERROR Test failed"
        log_bytes = log_content.encode("utf-8")

        # Create a test result and attach the log as iqe-core would
        result = IbutsuTestResult(test_id="test_log_upload")
        result.attach_artifact("iqe.log", log_bytes)

        # Upload the artifacts
        sender.upload_artifacts(result)

        # Verify the upload was called correctly
        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        assert args[0] == sender.artifact_api.upload_artifact
        assert kwargs["filename"] == "iqe.log"
        # Verify the data content was captured correctly
        assert sender._captured_data_content() == log_bytes  # Should be the exact bytes
        assert kwargs["result_id"] == result.id

    def test_network_log_upload_like_iqe_core(self):
        """Test network log upload like iqe-core does with net.log."""
        sender = _create_data_capturing_sender()

        # Simulate iqe-core creating network log and attaching it as bytes
        net_log_content = (
            "1672531947.123 - 150 - 200 - GET - https://example.com/api/users - etag123 - req-id-456\n"
            "1672531947.456 - 75 - 200 - POST - https://example.com/api/login - etag789 - req-id-789\n"
        )
        net_log_bytes = net_log_content.encode("utf-8")

        result = IbutsuTestResult(test_id="test_net_log_upload")
        result.attach_artifact("net.log", net_log_bytes)

        sender.upload_artifacts(result)

        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        assert kwargs["filename"] == "net.log"
        # Verify the data content was captured correctly
        assert sender._captured_data_content() == net_log_bytes

    def test_browser_log_upload_like_iqe_core(self):
        """Test browser log upload like iqe-core does with browser.log."""
        sender = _create_data_capturing_sender()

        # Simulate iqe-core creating browser log and attaching it as bytes
        browser_log_content = (
            "[INFO] (2025-09-24T22:32:27.123Z): Page loaded successfully\n"
            "[ERROR] (2025-09-24T22:32:28.456Z): JavaScript error: Uncaught TypeError\n"
        )
        browser_log_bytes = browser_log_content.encode("utf-8")

        result = IbutsuTestResult(test_id="test_browser_log_upload")
        result.attach_artifact("browser.log", browser_log_bytes)

        sender.upload_artifacts(result)

        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        assert kwargs["filename"] == "browser.log"
        # Verify the data content was captured correctly
        assert sender._captured_data_content() == browser_log_bytes

    def test_screenshot_upload_like_iqe_core(self):
        """Test screenshot upload like iqe-core does with screenshot.png."""
        sender = _create_data_capturing_sender()

        # Simulate iqe-core taking a screenshot and attaching it as bytes
        # This simulates selenium.get_screenshot_as_png() output
        mock_png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x10\x00\x00\x00\x10"
            b"\x08\x02\x00\x00\x00\x90\x91h6\x00\x00\x00\x19tEXtSoftware\x00Adobe"
            b"\x00ImageReadyq\xc9e<\x00\x00\x00\x0eIDATx\xdab\x00\x02\x00\x00\x05"
            b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        result = IbutsuTestResult(test_id="test_screenshot_upload")
        result.attach_artifact("screenshot.png", mock_png_data)

        sender.upload_artifacts(result)

        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        assert kwargs["filename"] == "screenshot.png"
        # Verify the data content was captured correctly
        assert (
            sender._captured_data_content() == mock_png_data
        )  # Should be the exact binary data

    def test_navigation_gif_upload_like_iqe_core(self):
        """Test navigation GIF upload like iqe-core does with nav.gif."""
        sender = _create_data_capturing_sender()

        # Simulate iqe-core creating a navigation GIF and reading it from file
        # This simulates nav_gif.read_bytes() output
        mock_gif_data = (
            b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04"
            b"\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02"
            b"\x04\x01\x00;"
        )

        result = IbutsuTestResult(test_id="test_nav_gif_upload")
        result.attach_artifact("nav.gif", mock_gif_data)

        sender.upload_artifacts(result)

        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        assert kwargs["filename"] == "nav.gif"
        # Verify the data content was captured correctly
        assert (
            sender._captured_data_content() == mock_gif_data
        )  # Should be the exact binary data

    def test_traceback_log_upload_like_iqe_core(self):
        """Test traceback log upload like iqe-core does with traceback.log."""
        sender = _create_data_capturing_sender()

        # Simulate iqe-core creating a traceback log from exception
        traceback_content = (
            "Traceback (most recent call last):\n"
            '  File "/tests/test_example.py", line 42, in test_function\n'
            "    assert response.status_code == 200\n"
            "AssertionError: Expected 200 but got 404\n"
        )
        traceback_bytes = traceback_content.encode("utf-8")

        result = IbutsuTestResult(test_id="test_traceback_upload")
        result.attach_artifact("traceback.log", traceback_bytes)

        sender.upload_artifacts(result)

        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        assert kwargs["filename"] == "traceback.log"
        # Verify the data content was captured correctly
        assert sender._captured_data_content() == traceback_bytes

    def test_multiple_artifacts_upload_like_iqe_core(self):
        """Test multiple artifacts upload like iqe-core does in a single test."""
        sender = _create_multi_data_capturing_sender()

        # Simulate iqe-core attaching multiple artifacts to one test result
        result = IbutsuTestResult(test_id="test_multiple_artifacts")

        # Text artifacts
        log_content = "Test execution log content"
        result.attach_artifact("iqe.log", log_content.encode("utf-8"))

        # Binary artifacts
        screenshot_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR..."
        result.attach_artifact("screenshot.png", screenshot_data)

        # Network log
        net_log = "Network requests log"
        result.attach_artifact("net.log", net_log.encode("utf-8"))

        sender.upload_artifacts(result)

        # Should be called once for each artifact
        assert sender._make_call.call_count == 3

        # Check that all artifacts were uploaded with correct data
        captured_calls = sender._captured_calls()
        uploaded_files = {
            call["kwargs"]["filename"]: call["data_content"] for call in captured_calls
        }

        assert "iqe.log" in uploaded_files
        assert "screenshot.png" in uploaded_files
        assert "net.log" in uploaded_files
        assert uploaded_files["iqe.log"] == log_content.encode("utf-8")
        assert uploaded_files["screenshot.png"] == screenshot_data
        assert uploaded_files["net.log"] == net_log.encode("utf-8")

    def test_run_artifact_upload_like_iqe_core(self):
        """Test run-level artifact upload like iqe-core does."""
        sender = _create_data_capturing_sender()

        # Simulate iqe-core attaching run-level artifacts
        run = IbutsuTestRun(id="test-run")

        # Run-level log file
        run_log_content = "Run-level configuration and setup logs"
        run.attach_artifact("run_setup.log", run_log_content.encode("utf-8"))

        sender.upload_artifacts(run)

        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args
        assert kwargs["filename"] == "run_setup.log"
        # Verify the data content was captured correctly
        assert sender._captured_data_content() == run_log_content.encode("utf-8")
        assert kwargs["run_id"] == run.id
        assert "result_id" not in kwargs

    def test_buffered_reader_artifact_upload(self):
        """Test that BufferedReader objects are properly handled during artifact upload."""
        import io

        sender = _create_data_capturing_sender()

        # Create a test result
        result = IbutsuTestResult(test_id="test_buffered_reader")

        # Create a BufferedReader with test content
        test_content = b"This is test log content from a BufferedReader"
        buffered_reader = io.BufferedReader(io.BytesIO(test_content))

        # Attach the BufferedReader as an artifact
        result.attach_artifact("test.log", buffered_reader)

        # Upload artifacts
        sender.upload_artifacts(result)

        # Verify the call was made
        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args

        # Verify the filename
        assert kwargs["filename"] == "test.log"

        # Verify the data content was properly converted from BufferedReader to bytes
        captured_data = sender._captured_data_content()
        assert captured_data == test_content
        assert isinstance(captured_data, bytes)

        # Verify correct API parameters
        assert kwargs["result_id"] == result.id
        assert "run_id" not in kwargs

    def test_text_buffered_reader_artifact_upload(self):
        """Test that text-mode BufferedReader objects are properly handled."""
        import io

        sender = _create_data_capturing_sender()

        # Create a test result
        result = IbutsuTestResult(test_id="test_text_buffered_reader")

        # Create a text-mode BufferedReader with test content
        test_content = "This is test log content from a text BufferedReader"
        text_stream = io.StringIO(test_content)

        # Attach the text stream as an artifact
        result.attach_artifact("test.log", text_stream)

        # Upload artifacts
        sender.upload_artifacts(result)

        # Verify the call was made
        sender._make_call.assert_called_once()
        args, kwargs = sender._make_call.call_args

        # Verify the filename
        assert kwargs["filename"] == "test.log"

        # Verify the data content was properly converted from text stream to bytes
        captured_data = sender._captured_data_content()
        assert captured_data == test_content.encode("utf-8")
        assert isinstance(captured_data, bytes)

        # Verify correct API parameters
        assert kwargs["result_id"] == result.id
        assert "run_id" not in kwargs


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
        mock_plugin.run = IbutsuTestRun(id="test-run")
        mock_plugin.results = {
            "test1": IbutsuTestResult(test_id="test1"),
            "test2": IbutsuTestResult(test_id="test2"),
        }
        # Initialize summary_info as a proper dict to support item assignment
        mock_plugin.summary_info = {
            "server_uploaded": False,
            "server_url": None,
            "frontend_url": None,
            "errors": [],
        }

        send_data_to_ibutsu(mock_plugin)

        # Verify sender methods were called
        assert mock_sender.add_or_update_run.call_count == 2  # Called twice
        assert mock_sender.upload_artifacts.call_count == 3  # Run + 2 results
        assert mock_sender.add_result.call_count == 2  # 2 results

    def test_send_data_success_with_frontend_url(self, caplog):
        """Test successful data sending logs frontend URL."""
        import logging
        from unittest.mock import patch

        caplog.set_level(logging.INFO)

        # Create a real sender but mock its methods
        sender = IbutsuSender("http://example.com/api")
        sender._has_server_error = False

        # Mock the health API to return the frontend URL
        mock_health_info = Mock()
        mock_health_info.frontend = "http://frontend.example.com"
        sender.health_api.get_health_info = Mock(return_value=mock_health_info)

        sender.add_or_update_run = Mock()
        sender.upload_artifacts = Mock()
        sender.add_result = Mock()

        mock_plugin = Mock()
        mock_plugin.run = IbutsuTestRun(id="test-run-123")
        mock_plugin.results = {}
        # Initialize summary_info as a proper dict to support item assignment
        mock_plugin.summary_info = {
            "server_uploaded": False,
            "server_url": None,
            "frontend_url": None,
            "errors": [],
        }

        with patch.object(IbutsuSender, "from_ibutsu_plugin", return_value=sender):
            send_data_to_ibutsu(mock_plugin)

        # Verify that the frontend URL was stored in summary_info
        assert mock_plugin.summary_info["frontend_url"] == "http://frontend.example.com"
        assert mock_plugin.summary_info["server_uploaded"] is True

    def test_send_data_with_server_error(self, caplog):
        """Test data sending with server error doesn't log URL."""
        import logging
        from unittest.mock import patch

        caplog.set_level(logging.INFO)

        # Create a real sender but mock its methods
        sender = IbutsuSender("http://example.com/api")
        sender._has_server_error = True
        sender.add_or_update_run = Mock()
        sender.upload_artifacts = Mock()
        sender.add_result = Mock()

        mock_plugin = Mock()
        mock_plugin.run = IbutsuTestRun(id="test-run")
        mock_plugin.results = {}
        # Initialize summary_info as a proper dict to support item assignment
        mock_plugin.summary_info = {
            "server_uploaded": False,
            "server_url": None,
            "frontend_url": None,
            "errors": [],
        }

        with patch.object(IbutsuSender, "from_ibutsu_plugin", return_value=sender):
            send_data_to_ibutsu(mock_plugin)

        # Should not log success message
        assert "Results can be viewed on:" not in caplog.text


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
    def test_retry_logging(self, mock_sleep, caplog):
        """Test that retry attempts are properly logged."""
        import logging

        caplog.set_level(logging.DEBUG)

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

        # Check that proper log messages were logged
        assert (
            "Network error (attempt 1/3): RemoteDisconnected: Connection failed. Retrying in 1.0 seconds..."
            in caplog.text
        )
        assert (
            "Network error (attempt 2/3): ProtocolError: Protocol failed. Retrying in 2.0 seconds..."
            in caplog.text
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
