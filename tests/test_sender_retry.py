import time
from unittest.mock import Mock, patch, call
from http.client import RemoteDisconnected, BadStatusLine
from urllib3.exceptions import ProtocolError, NewConnectionError, ConnectTimeoutError

import pytest

from pytest_ibutsu.sender import IbutsuSender, TooManyRetriesError, MAX_CALL_RETRIES


class TestSenderRetry:
    """Test retry functionality for network failures in IbutsuSender."""

    def test_successful_call_no_retry(self):
        """Test that successful calls don't trigger retry logic."""
        sender = IbutsuSender("http://example.com/api")
        mock_method = Mock(return_value="success")
        
        result = sender._make_call(mock_method, "arg1", kwarg1="value1")
        
        assert result == "success"
        mock_method.assert_called_once_with("arg1", kwarg1="value1")

    @patch('pytest_ibutsu.sender.time.sleep')
    def test_retry_on_network_errors(self, mock_sleep):
        """Test that network errors trigger retry with exponential backoff."""
        sender = IbutsuSender("http://example.com/api")
        mock_method = Mock()
        
        # Fail twice, then succeed
        mock_method.side_effect = [
            RemoteDisconnected("Connection closed"),
            ProtocolError("Protocol error"), 
            "success"
        ]
        
        result = sender._make_call(mock_method, "test_arg")
        
        assert result == "success"
        assert mock_method.call_count == 3
        
        # Check that sleep was called with exponential backoff
        expected_calls = [call(1.0), call(2.0)]  # 1.0 * 2^0, 1.0 * 2^1
        mock_sleep.assert_has_calls(expected_calls)

    @patch('pytest_ibutsu.sender.time.sleep')
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

    @pytest.mark.parametrize("exception_type,exception_args", [
        (RemoteDisconnected, ("Test error",)),
        (ProtocolError, ("Test error",)), 
        (BadStatusLine, ("Test error",)),
        (NewConnectionError, (Mock(), "Test error")),  # NewConnectionError needs a pool and message
        (ConnectTimeoutError, ("Test error",)),
    ])
    @patch('pytest_ibutsu.sender.time.sleep')
    def test_retry_on_different_network_exceptions(self, mock_sleep, exception_type, exception_args):
        """Test that all expected network exceptions trigger retry."""
        sender = IbutsuSender("http://example.com/api")
        mock_method = Mock()
        
        # Fail once with the specific exception, then succeed
        mock_method.side_effect = [exception_type(*exception_args), "success"]
        
        result = sender._make_call(mock_method)
        
        assert result == "success"
        assert mock_method.call_count == 2
        mock_sleep.assert_called_once_with(1.0)

    @patch('pytest_ibutsu.sender.time.sleep')
    @patch('builtins.print')
    def test_retry_logging(self, mock_print, mock_sleep):
        """Test that retry attempts are properly logged."""
        sender = IbutsuSender("http://example.com/api")
        mock_method = Mock()
        
        # Fail twice, then succeed
        mock_method.side_effect = [
            RemoteDisconnected("Connection failed"),
            ProtocolError("Protocol failed"),
            "success"
        ]
        
        result = sender._make_call(mock_method)
        
        assert result == "success"
        
        # Check that proper log messages were printed
        print_calls = [call.args[0] for call in mock_print.call_args_list]
        
        # Should have 2 retry log messages
        assert len(print_calls) == 2
        assert "Network error (attempt 1/3): RemoteDisconnected: Connection failed. Retrying in 1.0 seconds..." in print_calls[0]
        assert "Network error (attempt 2/3): ProtocolError: Protocol failed. Retrying in 2.0 seconds..." in print_calls[1]

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

    @patch('pytest_ibutsu.sender.time.sleep')
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

    @patch('pytest_ibutsu.sender.time.sleep')
    def test_exponential_backoff_calculation(self, mock_sleep):
        """Test that exponential backoff delays are calculated correctly."""
        sender = IbutsuSender("http://example.com/api")
        mock_method = Mock()
        
        # Fail exactly MAX_CALL_RETRIES times to test all delays
        mock_method.side_effect = [RemoteDisconnected("Fail")] * MAX_CALL_RETRIES
        
        result = sender._make_call(mock_method)
        
        assert result is None  # Should fail after max retries
        
        # Check exponential backoff: 1.0, 2.0 (for 3 total attempts)
        expected_delays = [1.0 * (2.0 ** i) for i in range(MAX_CALL_RETRIES - 1)]
        expected_calls = [call(delay) for delay in expected_delays]
        mock_sleep.assert_has_calls(expected_calls)
