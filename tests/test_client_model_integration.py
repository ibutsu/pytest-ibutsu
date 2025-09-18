"""Integration tests for ibutsu-client-python model usage and validation.

This module tests the integration between pytest-ibutsu and the ibutsu-client-python
models, ensuring proper serialization, validation, and API compatibility after
the Pydantic v2 compatibility fixes.
"""

import json
import uuid
from datetime import datetime, UTC
from unittest.mock import Mock, patch

import pytest
from ibutsu_client.models.result import Result as ClientResult
from ibutsu_client.models.run import Run as ClientRun
from ibutsu_client.api.result_api import ResultApi
from ibutsu_client.api.run_api import RunApi
from pydantic import ValidationError

from pytest_ibutsu.modeling import IbutsuTestResult, IbutsuTestRun
from pytest_ibutsu.sender import IbutsuSender


class TestClientModelCompatibility:
    """Test compatibility between pytest-ibutsu models and ibutsu-client models."""

    def test_result_model_instantiation_with_valid_data(self):
        """Test that ClientResult can be instantiated with valid data."""
        # Test data that should be accepted by the Result model
        valid_data = {
            "test_id": "test_function_123",
            "result": "passed",
            "component": "auth",
            "env": "production",
            "source": "pytest-ibutsu",
            "start_time": datetime.now(UTC).isoformat(),
            "duration": 2.5,
        }

        # Should not raise any validation errors
        result = ClientResult(**valid_data)

        assert result.test_id == "test_function_123"
        assert result.result == "passed"
        assert result.component == "auth"
        assert result.env == "production"
        assert result.source == "pytest-ibutsu"
        assert result.duration == 2.5

    @pytest.mark.parametrize(
        "result_value",
        [
            "passed",
            "failed",
            "error",
            "skipped",
            "xpassed",
            "xfailed",
            "manual",
            "blocked",
        ],
    )
    def test_result_model_field_validation(self, result_value):
        """Test that Result model properly validates the result field enum."""
        # Should not raise validation error
        result = ClientResult(test_id="test_123", result=result_value)
        assert result.result == result_value

    @pytest.mark.parametrize(
        "invalid_case_value",
        [
            "PASSED",
            "Failed",
            "ERROR",
            "Skipped",
            "XPASSED",
            "XFailed",
            "MANUAL",
            "Blocked",
        ],
    )
    def test_result_enum_case_sensitivity(self, invalid_case_value):
        """Test that Result model validates case sensitivity in result enum values."""
        with pytest.raises(ValidationError) as exc_info:
            ClientResult(test_id="test_123", result=invalid_case_value)

        error_str = str(exc_info.value)
        assert "must be one of enum values" in error_str

    def test_result_model_invalid_result_enum(self):
        """Test that Result model rejects invalid result enum values."""
        with pytest.raises(ValidationError) as exc_info:
            ClientResult(test_id="test_123", result="invalid_status")

        # Should mention that it's not one of the valid enum values
        error_str = str(exc_info.value)
        assert "must be one of enum values" in error_str
        assert "passed" in error_str
        assert "failed" in error_str

    def test_run_model_instantiation_with_valid_data(self):
        """Test that ClientRun can be instantiated with valid data."""
        valid_data = {
            "component": "authentication",
            "env": "staging",
            "source": "pytest-ibutsu",
            "start_time": datetime.now(UTC).isoformat(),
            "duration": 120.5,
            "metadata": {"build": "123", "branch": "main"},
        }

        # Should not raise any validation errors
        run = ClientRun(**valid_data)

        assert run.component == "authentication"
        assert run.env == "staging"
        assert run.source == "pytest-ibutsu"
        assert run.duration == 120.5
        assert run.metadata == {"build": "123", "branch": "main"}

    def test_uuid_field_handling(self):
        """Test that UUID fields are properly handled in client models."""
        # Test with string UUID
        uuid_str = str(uuid.uuid4())
        result = ClientResult(test_id="test_123", id=uuid_str)

        # The id should be accepted and stored
        assert str(result.id) == uuid_str

        # Test with actual UUID object
        uuid_obj = uuid.uuid4()
        result2 = ClientResult(test_id="test_123", id=uuid_obj)
        assert result2.id == uuid_obj

        # Test with None (should be allowed for optional UUID fields)
        result3 = ClientResult(test_id="test_123", run_id=None, project_id=None)
        assert result3.run_id is None
        assert result3.project_id is None

    @pytest.mark.parametrize(
        "invalid_uuid",
        [
            "not-a-uuid",
            "12345678-1234-1234-1234",  # Too short
            "12345678-1234-1234-1234-12345678901234",  # Too long
            "",
            "random-string",
            123,  # Not a string
            [],  # Not a string
        ],
    )
    def test_uuid_field_invalid_formats(self, invalid_uuid):
        """Test that invalid UUID formats and types are properly rejected."""
        with pytest.raises(ValidationError):
            ClientResult(test_id="test_123", id=invalid_uuid)

    @pytest.mark.parametrize(
        "model_class,field_name",
        [
            (ClientResult, "id"),
            (ClientResult, "run_id"),
            (ClientResult, "project_id"),
            (ClientRun, "id"),
            (ClientRun, "project_id"),
        ],
    )
    def test_uuid_field_validation(self, model_class, field_name):
        """Test UUID field validation for various models and fields."""
        # Valid UUID should work
        valid_uuid = str(uuid.uuid4())
        kwargs = {"test_id": "test"} if model_class == ClientResult else {}
        kwargs[field_name] = valid_uuid

        instance = model_class(**kwargs)
        assert getattr(instance, field_name) is not None

    def test_model_json_serialization(self):
        """Test that client models can be serialized to JSON."""
        # Create a Result with various data types
        result = ClientResult(
            test_id="test_function",
            result="passed",
            component="auth",
            env="production",
            source="pytest-ibutsu",
            id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            start_time=datetime.now(UTC).isoformat(),
            duration=1.5,
            metadata={"key": "value", "nested": {"data": [1, 2, 3]}},
            params={"param1": "value1", "param2": 42},
        )

        # Should be able to serialize to JSON
        json_str = result.model_dump_json()
        assert isinstance(json_str, str)

        # Should be valid JSON
        parsed = json.loads(json_str)
        assert parsed["test_id"] == "test_function"
        assert parsed["result"] == "passed"
        assert parsed["metadata"]["key"] == "value"

    def test_model_dict_conversion(self):
        """Test that client models can be converted to dictionaries."""
        run = ClientRun(
            component="web",
            env="test",
            source="pytest-ibutsu",
            id=uuid.uuid4(),
            metadata={"version": "1.0", "commit": "abc123"},
        )

        # Convert to dict
        run_dict = run.model_dump()

        assert isinstance(run_dict, dict)
        assert run_dict["component"] == "web"
        assert run_dict["env"] == "test"
        assert run_dict["source"] == "pytest-ibutsu"
        assert "id" in run_dict
        assert run_dict["metadata"]["version"] == "1.0"


class TestIbutsuTestResultToClientResultConversion:
    """Test conversion from IbutsuTestResult to client Result model."""

    def test_ibutsu_result_to_client_result_conversion(self):
        """Test converting IbutsuTestResult to client Result model."""
        # Create an IbutsuTestResult
        ibutsu_result = IbutsuTestResult(
            test_id="test_login_success",
            result="passed",
            component="auth",
            env="staging",
            source="pytest-ibutsu",
            duration=1.25,
            metadata={"browser": "chrome", "version": "90.0"},
            params={"username": "testuser", "password": "hidden"},
        )

        # Convert to dict (as would be done in sender)
        result_dict = ibutsu_result.to_dict()

        # Should be able to create client Result from this dict
        client_result = ClientResult(**result_dict)

        assert client_result.test_id == "test_login_success"
        assert client_result.result == "passed"
        assert client_result.component == "auth"
        assert client_result.env == "staging"
        assert client_result.source == "pytest-ibutsu"
        assert client_result.duration == 1.25

    def test_ibutsu_result_with_invalid_result_status(self):
        """Test that invalid result status is caught when converting to client model."""
        # Create IbutsuTestResult with invalid status (this shouldn't happen in practice)
        ibutsu_result = IbutsuTestResult(test_id="test_1")
        ibutsu_result.result = "invalid_status"  # Manually set invalid status

        result_dict = ibutsu_result.to_dict()

        # Client model should reject this
        with pytest.raises(ValidationError):
            ClientResult(**result_dict)

    def test_ibutsu_result_with_complex_metadata(self):
        """Test IbutsuTestResult with complex metadata converts properly."""
        ibutsu_result = IbutsuTestResult(
            test_id="complex_test",
            metadata={
                "markers": [{"name": "slow", "args": [], "kwargs": {}}],
                "durations": {"setup": 0.1, "call": 2.0, "teardown": 0.05},
                "statuses": {"setup": ("passed", False), "call": ("passed", False)},
                "user_properties": {"custom_key": "custom_value"},
                "fspath": "tests/test_auth.py",
                "node_id": "tests/test_auth.py::test_login_success[user1]",
            },
        )

        result_dict = ibutsu_result.to_dict()

        # Should be able to create client model even with complex metadata
        client_result = ClientResult(**result_dict)
        assert client_result.metadata is not None
        assert "markers" in client_result.metadata
        assert "durations" in client_result.metadata

    def test_uuid_field_consistency(self):
        """Test that UUID fields are consistent between models."""
        test_uuid = uuid.uuid4()
        run_uuid = uuid.uuid4()

        ibutsu_result = IbutsuTestResult(
            test_id="uuid_test", id=str(test_uuid), run_id=str(run_uuid)
        )

        result_dict = ibutsu_result.to_dict()
        client_result = ClientResult(**result_dict)

        # UUIDs should be preserved
        assert str(client_result.id) == str(test_uuid)
        assert str(client_result.run_id) == str(run_uuid)


class TestIbutsuTestRunToClientRunConversion:
    """Test conversion from IbutsuTestRun to client Run model."""

    def test_ibutsu_run_to_client_run_conversion(self):
        """Test converting IbutsuTestRun to client Run model."""
        ibutsu_run = IbutsuTestRun(
            component="api-tests",
            env="production",
            source="pytest-ibutsu",
            start_time=datetime.now(UTC).isoformat(),
            duration=45.5,
            metadata={"build_id": "build-123", "commit_sha": "abc123def"},
        )

        run_dict = ibutsu_run.to_dict()

        # Should be able to create client Run from this dict
        client_run = ClientRun(**run_dict)

        assert client_run.component == "api-tests"
        assert client_run.env == "production"
        assert client_run.source == "pytest-ibutsu"
        assert client_run.duration == 45.5

    def test_ibutsu_run_with_jenkins_metadata(self):
        """Test IbutsuTestRun with Jenkins metadata converts properly."""
        ibutsu_run = IbutsuTestRun()
        ibutsu_run.metadata["jenkins"] = {
            "job_name": "test-job",
            "build_number": "123",
            "build_url": "http://jenkins.example.com/job/test-job/123",
        }

        run_dict = ibutsu_run.to_dict()
        client_run = ClientRun(**run_dict)

        assert "jenkins" in client_run.metadata
        assert client_run.metadata["jenkins"]["job_name"] == "test-job"
        assert client_run.metadata["jenkins"]["build_number"] == "123"


class TestSenderModelIntegration:
    """Test IbutsuSender integration with client models."""

    @patch("pytest_ibutsu.sender.ApiClient")
    def test_sender_add_result_with_client_model_data(self, mock_api_client):
        """Test that sender properly sends IbutsuTestResult data to client API."""
        sender = IbutsuSender("http://example.com/api")

        # Mock the result API
        mock_result_api = Mock(spec=ResultApi)
        sender.result_api = mock_result_api
        sender._make_call = Mock()

        # Create a test result
        result = IbutsuTestResult(
            test_id="test_api_endpoint",
            result="passed",
            component="api",
            env="test",
            duration=0.5,
        )

        # Call add_result
        sender.add_result(result)

        # Verify _make_call was called with correct arguments
        sender._make_call.assert_called_once_with(
            sender.result_api.add_result, result=result.to_dict()
        )

        # Verify the dict can be used to create a valid client Result
        result_dict = result.to_dict()
        client_result = ClientResult(**result_dict)
        assert client_result.test_id == "test_api_endpoint"
        assert client_result.result == "passed"

    @patch("pytest_ibutsu.sender.ApiClient")
    def test_sender_add_run_with_client_model_data(self, mock_api_client):
        """Test that sender properly sends IbutsuTestRun data to client API."""
        sender = IbutsuSender("http://example.com/api")

        # Mock the run API
        mock_run_api = Mock(spec=RunApi)
        sender.run_api = mock_run_api
        sender._make_call = Mock(return_value=None)  # Simulate new run

        # Create a test run
        run = IbutsuTestRun(
            component="web-tests", env="staging", source="pytest-ibutsu"
        )

        # Call add_or_update_run
        sender.add_or_update_run(run)

        # Should make two calls: get_run (returns None) and add_run
        assert sender._make_call.call_count == 2

        # Verify the run dict can be used to create a valid client Run
        run_dict = run.to_dict()
        client_run = ClientRun(**run_dict)
        assert client_run.component == "web-tests"
        assert client_run.env == "staging"

    def test_result_serialization_for_api_calls(self):
        """Test that result serialization produces API-compatible data."""
        # Create a result with all possible fields
        result = IbutsuTestResult(
            test_id="comprehensive_test",
            result="failed",
            component="database",
            env="production",
            source="pytest-ibutsu",
            run_id=str(uuid.uuid4()),
            start_time=datetime.now(UTC).isoformat(),
            duration=10.5,
            metadata={
                "exception_name": "AssertionError",
                "short_tb": "AssertionError: Values don't match",
                "markers": [{"name": "database", "args": [], "kwargs": {}}],
                "durations": {"setup": 0.1, "call": 10.0, "teardown": 0.4},
                "statuses": {"setup": ("passed", False), "call": ("failed", False)},
                "classification": "test_failure",
            },
            params={"table_name": "users", "record_count": 1000},
        )

        # Serialize for API
        result_dict = result.to_dict()

        # Should be JSON serializable
        json_str = json.dumps(result_dict)
        assert json_str != ""

        # Should be valid for client model
        client_result = ClientResult(**result_dict)
        assert client_result.test_id == "comprehensive_test"
        assert client_result.result == "failed"
        assert client_result.metadata["exception_name"] == "AssertionError"

    def test_run_serialization_for_api_calls(self):
        """Test that run serialization produces API-compatible data."""
        # Create a run with comprehensive data
        run = IbutsuTestRun(
            component="integration-tests",
            env="production",
            source="pytest-ibutsu",
            start_time=datetime.now(UTC).isoformat(),
            duration=300.0,
            metadata={
                "jenkins": {
                    "job_name": "nightly-tests",
                    "build_number": "456",
                    "build_url": "http://jenkins.example.com/job/nightly-tests/456",
                },
                "env_id": "prod-env-123",
                "git_commit": "def456abc",
                "git_branch": "release/v2.0",
            },
        )

        # Serialize for API
        run_dict = run.to_dict()

        # Should be JSON serializable
        json_str = json.dumps(run_dict)
        assert json_str != ""

        # Should be valid for client model
        client_run = ClientRun(**run_dict)
        assert client_run.component == "integration-tests"
        assert client_run.env == "production"
        assert client_run.metadata["jenkins"]["job_name"] == "nightly-tests"


class TestModelFieldCompatibility:
    """Test field compatibility between pytest-ibutsu and client models."""

    def test_result_field_mapping_completeness(self):
        """Test that all IbutsuTestResult fields map to ClientResult fields."""
        # Create IbutsuTestResult with all public fields
        result = IbutsuTestResult(
            test_id="field_mapping_test",
            component="auth",
            env="test",
            result="passed",
            id=str(uuid.uuid4()),
            metadata={"key": "value"},
            params={"param": "value"},
            run_id=str(uuid.uuid4()),
            source="pytest-ibutsu",
            start_time=datetime.now(UTC).isoformat(),
            duration=1.0,
        )

        result_dict = result.to_dict()

        # Should not have private fields
        assert not any(key.startswith("_") for key in result_dict.keys())

        # All public fields should be present and mappable to ClientResult
        client_result = ClientResult(**result_dict)

        # Verify key fields are mapped correctly
        assert client_result.test_id == result.test_id
        assert client_result.component == result.component
        assert client_result.env == result.env
        assert client_result.result == result.result
        assert str(client_result.id) == result.id
        assert client_result.metadata == result.metadata
        assert client_result.params == result.params
        assert client_result.source == result.source
        assert client_result.duration == result.duration

    def test_run_field_mapping_completeness(self):
        """Test that all IbutsuTestRun fields map to ClientRun fields."""
        # Create IbutsuTestRun with all public fields
        run = IbutsuTestRun(
            component="api",
            env="staging",
            id=str(uuid.uuid4()),
            metadata={"build": "123"},
            source="pytest-ibutsu",
            start_time=datetime.now(UTC).isoformat(),
            duration=60.0,
        )

        run_dict = run.to_dict()

        # Should not have private fields
        assert not any(key.startswith("_") for key in run_dict.keys())

        # All public fields should be mappable to ClientRun
        client_run = ClientRun(**run_dict)

        # Verify key fields are mapped correctly
        assert client_run.component == run.component
        assert client_run.env == run.env
        assert str(client_run.id) == run.id
        assert client_run.metadata == run.metadata
        assert client_run.source == run.source
        assert client_run.duration == run.duration

    def test_optional_fields_handling(self):
        """Test that optional fields are properly handled in both models."""
        # Create minimal result
        result = IbutsuTestResult(test_id="minimal_test")
        result_dict = result.to_dict()

        # Should create valid client result with defaults
        client_result = ClientResult(**result_dict)
        assert client_result.test_id == "minimal_test"
        assert client_result.result == "passed"  # default
        assert client_result.source == "local"  # default

        # Create minimal run
        run = IbutsuTestRun()
        run_dict = run.to_dict()

        # Should create valid client run with defaults
        client_run = ClientRun(**run_dict)
        assert client_run.component is None  # optional field
        assert client_run.env is None  # optional field


class TestErrorHandling:
    """Test error handling in model integration scenarios."""

    def test_api_exception_with_invalid_data(self):
        """Test handling of API exceptions when sending invalid data."""
        # This simulates what would happen if somehow invalid data
        # made it through to the client API
        with pytest.raises(ValidationError):
            ClientResult(test_id="test", result="invalid_result_value")

    def test_malformed_uuid_handling(self):
        """Test handling of malformed UUID strings."""
        # Invalid UUID string should raise ValidationError
        with pytest.raises(ValidationError):
            ClientResult(test_id="test", id="not-a-uuid")

    def test_missing_required_fields(self):
        """Test handling of missing required fields."""
        # ClientResult allows None for test_id (it's optional in this version)
        result = ClientResult(result="passed")  # Missing test_id is allowed
        assert result.test_id is None
        assert result.result == "passed"

    def test_type_validation_errors(self):
        """Test type validation in client models."""
        # Duration should be numeric
        with pytest.raises(ValidationError):
            ClientResult(test_id="test", duration="not_a_number")

        # Metadata should be dict if provided
        with pytest.raises(ValidationError):
            ClientResult(test_id="test", metadata="not_a_dict")

    @pytest.mark.parametrize(
        "invalid_result", ["unknown", "custom_status", "", 123, ["passed"]]
    )
    def test_result_enum_validation_comprehensive(self, invalid_result):
        """Test comprehensive result enum validation."""
        with pytest.raises((ValidationError, TypeError)):
            ClientResult(test_id="test", result=invalid_result)

    def test_result_none_value_allowed(self):
        """Test that None is allowed for result field."""
        # None is allowed as a valid value
        result = ClientResult(test_id="test", result=None)
        assert result.result is None


class TestBackwardsCompatibility:
    """Test backwards compatibility with existing pytest-ibutsu usage."""

    def test_existing_result_creation_still_works(self):
        """Test that existing ways of creating results still work."""
        # This simulates the typical way results are created in pytest-ibutsu
        result = IbutsuTestResult(test_id="backwards_compat_test")
        result.result = "passed"
        result.component = "legacy_component"
        result.duration = 2.5
        result.metadata = {"legacy": True}

        # Should still convert to client model successfully
        result_dict = result.to_dict()
        client_result = ClientResult(**result_dict)

        assert client_result.test_id == "backwards_compat_test"
        assert client_result.result == "passed"
        assert client_result.component == "legacy_component"

    def test_metadata_serialization_compatibility(self):
        """Test that complex metadata still serializes correctly."""
        # Test the type of complex metadata that pytest-ibutsu typically creates
        result = IbutsuTestResult(test_id="metadata_compat_test")
        result.metadata = {
            "statuses": {"setup": ("passed", False), "call": ("passed", False)},
            "durations": {"setup": 0.1, "call": 1.0, "teardown": 0.05},
            "markers": [
                {"name": "slow", "args": [], "kwargs": {}},
                {"name": "parametrize", "args": ["input", "expected"], "kwargs": {}},
            ],
            "user_properties": [("custom_prop", "custom_value")],
            "fspath": "tests/test_example.py",
            "node_id": "tests/test_example.py::test_function[param1]",
        }

        # Should serialize and be accepted by client model
        result_dict = result.to_dict()
        client_result = ClientResult(**result_dict)

        assert "statuses" in client_result.metadata
        assert "durations" in client_result.metadata
        assert "markers" in client_result.metadata
