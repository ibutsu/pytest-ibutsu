"""Integration tests for API scenarios with ibutsu-client-python models.

This module tests real-world API integration scenarios, ensuring that the
pytest-ibutsu plugin properly interacts with the ibutsu-client-python API
after the Pydantic v2 compatibility fixes.
"""

import json
import uuid
from datetime import datetime, UTC
from unittest.mock import Mock, patch

import pytest
from ibutsu_client.models.result import Result as ClientResult
from ibutsu_client.models.run import Run as ClientRun
from ibutsu_client.exceptions import ApiException
from urllib3.exceptions import NewConnectionError, ConnectTimeoutError

from pytest_ibutsu.modeling import IbutsuTestResult, IbutsuTestRun
from pytest_ibutsu.sender import IbutsuSender, send_data_to_ibutsu


class TestAPIDataFlow:
    """Test the complete data flow from pytest-ibutsu models to API calls."""

    @patch("pytest_ibutsu.sender.ApiClient")
    def test_complete_result_submission_flow(self, mock_api_client):
        """Test complete flow of submitting a test result to the API."""
        sender = IbutsuSender("http://example.com/api", "test-token")

        # Mock the API methods
        sender.result_api.add_result = Mock()
        sender._make_call = Mock()

        # Create a comprehensive test result
        result = IbutsuTestResult(
            test_id="test_user_registration",
            result="passed",
            component="user-management",
            env="production",
            source="pytest-ibutsu",
            run_id=str(uuid.uuid4()),
            start_time=datetime.now(UTC).isoformat(),
            duration=2.75,
            metadata={
                "markers": [{"name": "integration", "args": [], "kwargs": {}}],
                "durations": {"setup": 0.25, "call": 2.0, "teardown": 0.5},
                "statuses": {
                    "setup": ("passed", False),
                    "call": ("passed", False),
                    "teardown": ("passed", False),
                },
                "user_properties": {"test_type": "integration", "priority": "high"},
                "fspath": "tests/integration/test_user_management.py",
                "node_id": "tests/integration/test_user_management.py::test_user_registration",
            },
            params={"username": "newuser", "email": "newuser@example.com"},
        )

        # Submit the result
        sender.add_result(result)

        # Verify _make_call was called with the correct data
        sender._make_call.assert_called_once()
        call_args = sender._make_call.call_args

        # Verify the result dict can create a valid ClientResult
        result_dict = call_args.kwargs["result"]
        client_result = ClientResult(**result_dict)

        assert client_result.test_id == "test_user_registration"
        assert client_result.result == "passed"
        assert client_result.component == "user-management"
        assert client_result.duration == 2.75
        assert "markers" in client_result.metadata

    @patch("pytest_ibutsu.sender.ApiClient")
    def test_complete_run_submission_flow(self, mock_api_client):
        """Test complete flow of submitting a test run to the API."""
        sender = IbutsuSender("http://example.com/api", "test-token")

        # Mock API methods
        sender.run_api.get_run = Mock(return_value=None)  # Run doesn't exist
        sender.run_api.add_run = Mock()
        sender._make_call = Mock(
            side_effect=[None, None]
        )  # get_run returns None, add_run succeeds

        # Create a comprehensive test run
        run = IbutsuTestRun(
            component="e2e-checkout-flow",
            env="staging",
            source="pytest-ibutsu",
            start_time=datetime.now(UTC).isoformat(),
            duration=300.0,
            metadata={
                "jenkins": {
                    "job_name": "nightly-e2e",
                    "build_number": "1234",
                    "build_url": "http://jenkins.example.com/job/nightly-e2e/1234",
                },
                "git": {
                    "commit": "abc123def456",
                    "branch": "feature/checkout-improvements",
                },
                "test_summary": {"total": 25, "passed": 23, "failed": 1, "skipped": 1},
            },
        )

        # Submit the run
        sender.add_or_update_run(run)

        # Verify two calls were made: get_run and add_run
        assert sender._make_call.call_count == 2

        # Verify the run dict from the add_run call can create a valid ClientRun
        add_run_call = [
            call
            for call in sender._make_call.call_args_list
            if call.args[0] == sender.run_api.add_run
        ][0]
        run_dict = add_run_call.kwargs["run"]
        client_run = ClientRun(**run_dict)

        assert client_run.component == "e2e-checkout-flow"
        assert client_run.env == "staging"
        assert client_run.duration == 300.0
        assert "jenkins" in client_run.metadata

    @pytest.mark.parametrize(
        "ibutsu_status,expected_client_status",
        [
            ("passed", "passed"),
            ("failed", "failed"),
            ("error", "error"),
            ("skipped", "skipped"),
            ("xfailed", "xfailed"),
            ("xpassed", "xpassed"),
        ],
    )
    def test_result_with_all_status_types(self, ibutsu_status, expected_client_status):
        """Test results with different status types are handled correctly."""
        result = IbutsuTestResult(test_id=f"test_{ibutsu_status}", result=ibutsu_status)

        result_dict = result.to_dict()
        client_result = ClientResult(**result_dict)

        assert client_result.result == expected_client_status

    @patch("pytest_ibutsu.sender.ApiClient")
    def test_artifact_upload_integration(self, mock_api_client):
        """Test artifact upload integration with client models."""
        sender = IbutsuSender("http://example.com/api")
        sender.artifact_api.upload_artifact = Mock()
        sender._make_call = Mock()

        # Create result with artifact
        result = IbutsuTestResult(test_id="test_with_artifacts")
        result.attach_artifact("test_log.txt", b"Test execution log content")
        result.attach_artifact("screenshot.png", b"PNG image data here")

        # Upload artifacts
        sender.upload_artifacts(result)

        # Should make two calls for two artifacts
        assert sender._make_call.call_count == 2

    def test_large_metadata_serialization(self):
        """Test that large metadata structures serialize properly for API calls."""
        # Create metadata that might be problematic
        large_metadata = {
            "test_output": "A" * 1000,  # Large string
            "nested_data": {
                f"level_{i}": {
                    "data": list(range(50)),
                    "text": f"Text for level {i}" * 10,
                }
                for i in range(20)
            },
            "exception_details": {
                "traceback": "\n".join([f"  File line {i}" for i in range(100)]),
                "variables": {f"var_{i}": f"value_{i}" for i in range(50)},
            },
        }

        result = IbutsuTestResult(
            test_id="large_metadata_test", metadata=large_metadata
        )

        # Should serialize without issues
        result_dict = result.to_dict()
        client_result = ClientResult(**result_dict)

        # Should be JSON serializable
        json_str = client_result.model_dump_json()
        assert len(json_str) > 5000  # Should be substantial

        # Should be deserializable
        parsed = json.loads(json_str)
        assert parsed["test_id"] == "large_metadata_test"


class TestAPIErrorHandling:
    """Test error handling in API integration scenarios."""

    @patch("pytest_ibutsu.sender.ApiClient")
    def test_api_exception_during_result_submission(self, mock_api_client):
        """Test handling of API exceptions during result submission."""
        sender = IbutsuSender("http://example.com/api")

        # Mock the actual API method to raise an exception
        sender.result_api.add_result = Mock(side_effect=ApiException("Server error"))

        result = IbutsuTestResult(test_id="test_api_error")

        # Should not raise exception (errors are logged and _make_call returns None)
        sender.add_result(result)

        # Error should be tracked
        assert sender._has_server_error is True

    @patch("pytest_ibutsu.sender.ApiClient")
    def test_api_call_with_invalid_authentication_token(self, mock_api_client):
        """Test handling of API calls with invalid or expired authentication tokens."""
        sender = IbutsuSender("http://example.com/api", "invalid-token")

        # Mock the API method to raise an authentication exception
        auth_error = ApiException(status=401, reason="Unauthorized")
        auth_error.body = '{"detail": "Invalid authentication credentials"}'
        sender.result_api.add_result = Mock(side_effect=auth_error)

        result = IbutsuTestResult(test_id="test_auth_error")

        # Should not raise exception (errors are logged and _make_call returns None)
        sender.add_result(result)

        # Error should be tracked and reported correctly
        assert sender._has_server_error is True

    @patch("pytest_ibutsu.sender.ApiClient")
    def test_network_error_during_run_submission(self, mock_api_client):
        """Test handling of network errors during run submission."""
        sender = IbutsuSender("http://example.com/api")

        # Mock both get_run and add_run to raise network errors
        # get_run is called with hide_exception=True, so it won't set error flag
        # add_run is called with hide_exception=False, so it will set error flag
        sender.run_api.get_run = Mock(
            side_effect=NewConnectionError(Mock(), "Connection failed")
        )
        sender.run_api.add_run = Mock(
            side_effect=NewConnectionError(Mock(), "Connection failed")
        )

        run = IbutsuTestRun(component="network_test")

        # Should not raise exception
        sender.add_or_update_run(run)

        # Error should be tracked from the add_run call (not hidden)
        assert sender._has_server_error is True

    @patch("pytest_ibutsu.sender.ApiClient")
    def test_timeout_during_artifact_upload(self, mock_api_client):
        """Test handling of timeouts during artifact upload."""
        sender = IbutsuSender("http://example.com/api")
        sender._make_call = Mock(side_effect=ConnectTimeoutError("Upload timeout"))

        result = IbutsuTestResult(test_id="timeout_test")
        result.attach_artifact("large_file.log", b"x" * 1000000)  # 1MB

        # Should handle timeout gracefully
        sender.upload_artifacts(result)

        # Should attempt upload despite timeout
        sender._make_call.assert_called()

    def test_invalid_data_validation_before_api_call(self):
        """Test that invalid data is caught before making API calls."""
        # Create result with data that would be invalid for ClientResult
        result = IbutsuTestResult(test_id="validation_test")
        result.result = "invalid_status"  # This would fail ClientResult validation

        result_dict = result.to_dict()

        # Should fail when trying to create ClientResult
        with pytest.raises(Exception):  # ValidationError or similar
            ClientResult(**result_dict)


class TestConcurrentOperations:
    """Test concurrent operations and async scenarios."""

    @patch("pytest_ibutsu.sender.ApiClient")
    def test_multiple_results_submission(self, mock_api_client):
        """Test submitting multiple results in sequence."""
        sender = IbutsuSender("http://example.com/api")
        sender._make_call = Mock()

        results = [
            IbutsuTestResult(test_id=f"test_{i}", result="passed") for i in range(5)
        ]

        # Submit all results
        for result in results:
            sender.add_result(result)

        # Should make 5 API calls
        assert sender._make_call.call_count == 5

    @patch("pytest_ibutsu.sender.ApiClient")
    def test_run_and_results_submission_sequence(self, mock_api_client):
        """Test the typical sequence of run creation followed by result submissions."""
        sender = IbutsuSender("http://example.com/api")
        sender._make_call = Mock(return_value=None)

        # Create run and results
        run = IbutsuTestRun(component="sequence_test")
        results = [
            IbutsuTestResult(test_id=f"test_{i}", run_id=run.id) for i in range(3)
        ]

        # Submit run first
        sender.add_or_update_run(run)

        # Then submit results
        for result in results:
            sender.add_result(result)

        # Should make 5 calls total: 2 for run (get + add), 3 for results
        assert sender._make_call.call_count == 5

    @patch("pytest_ibutsu.sender.ApiClient")
    def test_async_request_handling(self, mock_api_client):
        """Test handling of async requests."""
        sender = IbutsuSender("http://example.com/api")

        # Mock async result
        mock_async_result = Mock()
        mock_async_result.ready.return_value = False

        # Mock the actual API method to return the async result
        sender.result_api.add_result = Mock(return_value=mock_async_result)

        result = IbutsuTestResult(test_id="async_test")

        # Make async call through _make_call
        response = sender._make_call(
            sender.result_api.add_result, result=result.to_dict(), async_req=True
        )

        # Should cache the async result
        assert response == mock_async_result
        assert mock_async_result in sender._sender_cache


class TestDataConsistency:
    """Test data consistency between models and API calls."""

    def test_uuid_consistency_across_models(self):
        """Test that UUIDs remain consistent across model conversions."""
        # Create UUIDs
        result_id = uuid.uuid4()
        run_id = uuid.uuid4()

        # Create IbutsuTestResult
        ibutsu_result = IbutsuTestResult(
            test_id="uuid_consistency_test", id=str(result_id), run_id=str(run_id)
        )

        # Convert to dict and back to ClientResult
        result_dict = ibutsu_result.to_dict()
        client_result = ClientResult(**result_dict)

        # UUIDs should be preserved
        assert client_result.id == result_id
        assert client_result.run_id == run_id

        # Should serialize and deserialize consistently
        json_str = client_result.model_dump_json()
        parsed = json.loads(json_str)

        assert parsed["id"] == str(result_id)
        assert parsed["run_id"] == str(run_id)

    def test_timestamp_consistency(self):
        """Test that timestamps remain consistent across conversions."""
        timestamp = datetime.now(UTC).isoformat()

        result = IbutsuTestResult(test_id="timestamp_test", start_time=timestamp)

        result_dict = result.to_dict()
        client_result = ClientResult(**result_dict)

        assert client_result.start_time == timestamp

    def test_duration_precision_consistency(self):
        """Test that duration precision is maintained."""
        precise_duration = 123.456789

        result = IbutsuTestResult(test_id="duration_test", duration=precise_duration)

        result_dict = result.to_dict()
        client_result = ClientResult(**result_dict)

        assert client_result.duration == precise_duration

    def test_metadata_structure_consistency(self):
        """Test that complex metadata structures remain consistent."""
        complex_metadata = {
            "nested": {
                "level1": {
                    "level2": ["item1", "item2", "item3"],
                    "numbers": [1, 2, 3.14159],
                    "boolean": True,
                }
            },
            "unicode": "Testing: ñáéíóú 中文 🚀",
            "null_value": None,
            "empty_dict": {},
            "empty_list": [],
        }

        result = IbutsuTestResult(
            test_id="metadata_consistency_test", metadata=complex_metadata
        )

        result_dict = result.to_dict()
        client_result = ClientResult(**result_dict)

        # Deep comparison of metadata
        assert client_result.metadata["nested"]["level1"]["level2"] == [
            "item1",
            "item2",
            "item3",
        ]
        assert client_result.metadata["nested"]["level1"]["numbers"] == [1, 2, 3.14159]
        assert client_result.metadata["unicode"] == "Testing: ñáéíóú 中文 🚀"
        assert client_result.metadata["null_value"] is None


class TestFullIntegrationScenarios:
    """Test full integration scenarios mimicking real pytest-ibutsu usage."""

    @patch("pytest_ibutsu.sender.IbutsuSender.from_ibutsu_plugin")
    def test_complete_test_session_flow(self, mock_from_plugin):
        """Test a complete test session flow with multiple results."""
        # Mock sender
        mock_sender = Mock()
        mock_sender._has_server_error = False
        mock_sender.frontend_url = "http://frontend.example.com"
        mock_from_plugin.return_value = mock_sender

        # Mock plugin with realistic data
        mock_plugin = Mock()

        # Create realistic test run
        run = IbutsuTestRun(
            component="payment-service", env="staging", source="pytest-ibutsu"
        )
        run.start_timer()
        run.metadata.update(
            {
                "jenkins": {"job_name": "payment-service-tests", "build_number": "456"},
                "git": {"commit": "abc123", "branch": "develop"},
            }
        )
        mock_plugin.run = run

        # Create realistic test results
        test_results = {
            "test_process_payment": IbutsuTestResult(
                test_id="test_process_payment",
                result="passed",
                component="payment-service",
                env="staging",
                duration=1.5,
                metadata={
                    "markers": [{"name": "integration", "args": [], "kwargs": {}}],
                    "statuses": {"call": ("passed", False)},
                },
            ),
            "test_invalid_card": IbutsuTestResult(
                test_id="test_invalid_card",
                result="failed",
                component="payment-service",
                env="staging",
                duration=0.8,
                metadata={
                    "exception_name": "ValidationError",
                    "statuses": {"call": ("failed", False)},
                },
            ),
            "test_expired_card": IbutsuTestResult(
                test_id="test_expired_card",
                result="skipped",
                component="payment-service",
                env="staging",
                duration=0.0,
                metadata={
                    "skip_reason": "Test card expired",
                    "statuses": {"setup": ("skipped", False)},
                },
            ),
        }
        mock_plugin.results = test_results
        mock_plugin.summary_info = {"errors": []}

        # Run the complete flow
        send_data_to_ibutsu(mock_plugin)

        # Verify all operations were performed
        assert mock_sender.add_or_update_run.call_count == 2  # Start and end
        assert mock_sender.add_result.call_count == 3  # Three test results
        assert mock_sender.upload_artifacts.call_count == 4  # Run + 3 results

        # Verify all results can be converted to valid client models
        for result in test_results.values():
            result_dict = result.to_dict()
            client_result = ClientResult(**result_dict)
            assert client_result.test_id is not None

    @pytest.mark.parametrize(
        "username,password,expected_result",
        [
            ("user1", "password1", "passed"),
            ("user2", "password2", "passed"),
            ("invalid_user", "wrong_pass", "failed"),
        ],
    )
    def test_parameterized_test_results_integration(
        self, username, password, expected_result
    ):
        """Test integration with parameterized test results."""
        result = IbutsuTestResult(
            test_id=f"test_login[{username}-{password}]",
            result=expected_result,
            component="authentication",
            params={"username": username, "password": password},
            metadata={
                "markers": [
                    {
                        "name": "parametrize",
                        "args": ["username,password", [(username, password)]],
                        "kwargs": {},
                    }
                ],
                "node_id": f"tests/test_auth.py::test_login[{username}-{password}]",
            },
        )

        # Should convert to valid client model
        result_dict = result.to_dict()
        client_result = ClientResult(**result_dict)
        assert "[" in client_result.test_id  # Contains parameter info
        assert client_result.params["username"] in client_result.test_id

    def test_xdist_parallel_execution_integration(self):
        """Test integration with pytest-xdist parallel execution."""
        # Simulate multiple worker runs that need to be merged
        worker_runs = []
        for worker_id in ["gw0", "gw1", "gw2"]:
            run = IbutsuTestRun(
                component="parallel-tests", env="ci", source="pytest-ibutsu"
            )
            run.metadata["worker_id"] = worker_id

            # Add some results to each worker
            for i in range(2):
                result = IbutsuTestResult(
                    test_id=f"test_{worker_id}_{i}", result="passed", run_id=run.id
                )
                run._results.append(result)

            worker_runs.append(run)

        # Merge runs (as would happen in xdist scenario)
        merged_run = IbutsuTestRun.from_xdist_test_runs(worker_runs)

        # Should convert to valid client model
        run_dict = merged_run.to_dict()
        client_run = ClientRun(**run_dict)

        assert client_run.component == "parallel-tests"
        assert len(merged_run._results) == 6  # 3 workers × 2 tests each
