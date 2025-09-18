"""Tests specifically for Pydantic v2 compatibility and field validation.

This module tests the fixes applied for Pydantic v2 compatibility, ensuring that
field validators work correctly and that the UUID import consolidation doesn't
break functionality.
"""

import json
import uuid
from datetime import datetime, UTC

import pytest
from pydantic import ValidationError
from ibutsu_client.models.result import Result as ClientResult
from ibutsu_client.models.run import Run as ClientRun


class TestPydanticV2FieldValidators:
    """Test that Pydantic v2 field validators work correctly."""

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
    def test_result_enum_validator_accepts_valid_values(self, result_value):
        """Test that the result field validator accepts all valid enum values."""
        # Should not raise ValidationError
        result = ClientResult(test_id="test_123", result=result_value)
        assert result.result == result_value

    @pytest.mark.parametrize(
        "invalid_value",
        [
            "unknown",
            "pending",
            "running",
            "cancelled",
            "timeout",
            "abort",
            "",
            123,
            [],
        ],
    )
    def test_result_enum_validator_rejects_invalid_values(self, invalid_value):
        """Test that the result field validator rejects invalid enum values."""
        with pytest.raises((ValidationError, TypeError)):
            ClientResult(test_id="test_123", result=invalid_value)

    def test_result_none_value_is_allowed(self):
        """Test that None is allowed for result field."""
        # None is a special case that's allowed
        result = ClientResult(test_id="test_123", result=None)
        assert result.result is None

    def test_result_enum_validator_error_message(self):
        """Test that the result field validator provides helpful error messages."""
        with pytest.raises(ValidationError) as exc_info:
            ClientResult(test_id="test_123", result="invalid_status")

        error_str = str(exc_info.value)
        # Should mention the valid enum values
        assert "must be one of enum values" in error_str
        assert "passed" in error_str
        assert "failed" in error_str
        assert "error" in error_str

    def test_field_validator_is_classmethod(self):
        """Test that field validators are implemented as class methods."""
        # This is a structural test to ensure the Pydantic v2 fix is in place
        from ibutsu_client.models.result import Result

        # The validator should exist and be accessible
        validator_method = getattr(Result, "result_validate_enum", None)
        assert validator_method is not None

        # Should be callable (classmethod behavior)
        assert callable(validator_method)

    def test_model_instantiation_with_validator(self):
        """Test that models can be instantiated successfully with validators active."""
        # This tests that the @classmethod fix doesn't break normal instantiation
        result = ClientResult(
            test_id="validator_test",
            result="passed",
            component="auth",
            env="production",
        )

        assert result.test_id == "validator_test"
        assert result.result == "passed"
        assert result.component == "auth"

    def test_validator_with_none_values(self):
        """Test that validators handle None values correctly."""
        # Result field is optional and can be None in some contexts
        result = ClientResult(test_id="none_test")
        # Default should be None or a valid enum value
        assert result.result in [
            None,
            "passed",
            "failed",
            "error",
            "skipped",
            "xpassed",
            "xfailed",
            "manual",
            "blocked",
        ]

    def test_json_serialization_with_validators(self):
        """Test that JSON serialization works with field validators active."""
        result = ClientResult(
            test_id="json_test",
            result="failed",
            component="database",
            env="staging",
            metadata={"error": "Connection timeout"},
            duration=30.5,
        )

        # Should serialize to JSON without issues
        json_str = result.model_dump_json()
        assert isinstance(json_str, str)

        # Should be valid JSON
        parsed = json.loads(json_str)
        assert parsed["test_id"] == "json_test"
        assert parsed["result"] == "failed"

    def test_model_copy_with_validators(self):
        """Test that model copying works with field validators."""
        original = ClientResult(test_id="copy_test", result="passed")

        # Should be able to copy with valid changes
        copied = original.model_copy(update={"result": "failed"})
        assert copied.result == "failed"
        assert copied.test_id == "copy_test"

        # Note: model_copy doesn't re-validate, so we test initial creation validation instead
        with pytest.raises(ValidationError):
            ClientResult(test_id="copy_test", result="invalid_value")


class TestUUIDImportConsolidation:
    """Test that UUID import consolidation doesn't break functionality."""

    def test_uuid_fields_in_result_model(self):
        """Test that UUID fields work correctly in Result model."""
        test_uuid = uuid.uuid4()
        run_uuid = uuid.uuid4()
        project_uuid = uuid.uuid4()

        result = ClientResult(
            test_id="uuid_test", id=test_uuid, run_id=run_uuid, project_id=project_uuid
        )

        assert result.id == test_uuid
        assert result.run_id == run_uuid
        assert result.project_id == project_uuid

    def test_uuid_fields_in_run_model(self):
        """Test that UUID fields work correctly in Run model."""
        run_uuid = uuid.uuid4()
        project_uuid = uuid.uuid4()

        run = ClientRun(
            id=run_uuid, project_id=project_uuid, component="api", env="test"
        )

        assert run.id == run_uuid
        assert run.project_id == project_uuid

    def test_uuid_string_conversion(self):
        """Test that UUID string conversion works correctly."""
        uuid_str = str(uuid.uuid4())

        result = ClientResult(test_id="string_uuid_test", id=uuid_str)

        # Should accept string UUIDs and convert appropriately
        assert str(result.id) == uuid_str

    @pytest.mark.parametrize(
        "invalid_uuid",
        [
            "not-a-uuid",
            "12345678-1234-1234-1234",  # Too short
            "12345678-1234-1234-1234-12345678901234",  # Too long
            "",
            "random-string",
        ],
    )
    def test_uuid_validation_with_invalid_strings(self, invalid_uuid):
        """Test that invalid UUID strings are properly rejected."""
        with pytest.raises(ValidationError):
            ClientResult(test_id="invalid_uuid_test", id=invalid_uuid)

    def test_uuid_fields_json_serialization(self):
        """Test that UUID fields serialize correctly to JSON."""
        test_uuid = uuid.uuid4()

        result = ClientResult(test_id="uuid_json_test", id=test_uuid, result="passed")

        json_str = result.model_dump_json()
        parsed = json.loads(json_str)

        # UUID should be serialized as string
        assert parsed["id"] == str(test_uuid)
        assert isinstance(parsed["id"], str)

    def test_all_models_can_be_imported(self):
        """Test that all client models can be imported successfully."""
        # This tests that the UUID import consolidation doesn't break imports
        from ibutsu_client.models.result import Result
        from ibutsu_client.models.run import Run
        from ibutsu_client.models.project import Project
        from ibutsu_client.models.artifact import Artifact

        # Should be able to instantiate basic instances
        models_to_test = [
            (Result, {"test_id": "test"}),
            (Run, {}),
            (Project, {"name": "test_project"}),
            (Artifact, {}),
        ]

        for model_class, kwargs in models_to_test:
            try:
                instance = model_class(**kwargs)
                assert instance is not None
            except Exception as e:
                pytest.fail(f"Failed to instantiate {model_class.__name__}: {e}")

    @pytest.mark.parametrize(
        "model_class,kwargs",
        [
            (
                lambda: __import__(
                    "ibutsu_client.models.result", fromlist=["Result"]
                ).Result,
                {"test_id": "test"},
            ),
            (lambda: __import__("ibutsu_client.models.run", fromlist=["Run"]).Run, {}),
            (
                lambda: __import__(
                    "ibutsu_client.models.project", fromlist=["Project"]
                ).Project,
                {"name": "test_project"},
            ),
            (
                lambda: __import__(
                    "ibutsu_client.models.artifact", fromlist=["Artifact"]
                ).Artifact,
                {},
            ),
        ],
    )
    def test_model_instantiation_parametrized(self, model_class, kwargs):
        """Test that different client models can be instantiated with basic data."""
        model_cls = model_class()  # Call the lambda to get the actual class
        try:
            instance = model_cls(**kwargs)
            assert instance is not None
        except Exception as e:
            pytest.fail(f"Failed to instantiate {model_cls.__name__}: {e}")


class TestModelValidationEdgeCases:
    """Test edge cases in model validation."""

    def test_result_model_with_all_fields(self):
        """Test Result model with all possible fields populated."""
        result = ClientResult(
            id=uuid.uuid4(),
            test_id="comprehensive_test",
            start_time=datetime.now(UTC).isoformat(),
            duration=45.5,
            result="failed",
            component="payment",
            env="production",
            run_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            metadata={
                "error_type": "TimeoutError",
                "stack_trace": "Full stack trace here...",
                "browser": "chrome",
                "version": "95.0",
                "retry_count": 3,
            },
            params={
                "amount": 100.50,
                "currency": "USD",
                "payment_method": "credit_card",
            },
            source="pytest-ibutsu",
        )

        # Should be valid and serializable
        json_str = result.model_dump_json()
        assert len(json_str) > 0

        # Should be deserializable
        parsed = json.loads(json_str)
        recreated = ClientResult(**parsed)
        assert recreated.test_id == "comprehensive_test"
        assert recreated.result == "failed"

    def test_run_model_with_all_fields(self):
        """Test Run model with all possible fields populated."""
        run = ClientRun(
            id=uuid.uuid4(),
            component="e2e-tests",
            env="staging",
            project_id=uuid.uuid4(),
            metadata={
                "jenkins": {
                    "job_name": "nightly-e2e",
                    "build_number": "789",
                    "build_url": "http://jenkins.example.com/job/nightly-e2e/789",
                },
                "git": {
                    "commit": "abc123def456",
                    "branch": "feature/new-payment",
                    "author": "developer@example.com",
                },
                "environment": {
                    "database_version": "13.4",
                    "api_version": "2.1.0",
                    "frontend_version": "1.5.2",
                },
            },
            source="pytest-ibutsu",
            start_time=datetime.now(UTC).isoformat(),
            duration=1800.0,  # 30 minutes
        )

        # Should be valid and serializable
        json_str = run.model_dump_json()
        assert len(json_str) > 0

        # Should be deserializable
        parsed = json.loads(json_str)
        recreated = ClientRun(**parsed)
        assert recreated.component == "e2e-tests"
        assert recreated.env == "staging"

    def test_nested_metadata_validation(self):
        """Test that deeply nested metadata is handled correctly."""
        complex_metadata = {
            "level1": {
                "level2": {
                    "level3": {
                        "test_data": [1, 2, 3],
                        "config": {
                            "timeout": 30,
                            "retries": 3,
                            "endpoints": {
                                "api": "http://api.example.com",
                                "auth": "http://auth.example.com",
                            },
                        },
                    }
                }
            },
            "parallel_data": {
                "browser_configs": [
                    {"name": "chrome", "version": "95.0"},
                    {"name": "firefox", "version": "93.0"},
                ]
            },
        }

        result = ClientResult(test_id="nested_metadata_test", metadata=complex_metadata)

        # Should handle complex nested structures
        assert result.metadata["level1"]["level2"]["level3"]["test_data"] == [1, 2, 3]
        assert (
            result.metadata["parallel_data"]["browser_configs"][0]["name"] == "chrome"
        )

    def test_special_characters_in_strings(self):
        """Test handling of special characters in string fields."""
        special_chars_data = {
            "test_id": "test_with_ñáéíóú_and_中文_and_🚀",
            "component": "auth-with-special-chars_äöü",
            "env": "test-environment_with_hyphen-and_underscore",
            "result": "passed",
            "metadata": {
                "error_message": "Error with quotes: \"nested quotes\" and 'single quotes'",
                "unicode_test": "Testing unicode: ñáéíóú 中文 🚀 🎉",
                "special_symbols": "Test with symbols: @#$%^&*()_+-=[]{}|;:,.<>?",
            },
        }

        result = ClientResult(**special_chars_data)

        # Should handle special characters correctly
        assert "ñáéíóú" in result.test_id
        assert "中文" in result.test_id
        assert "🚀" in result.test_id

        # Should serialize and deserialize correctly
        json_str = result.model_dump_json()
        parsed = json.loads(json_str)
        recreated = ClientResult(**parsed)

        assert recreated.test_id == special_chars_data["test_id"]
        assert (
            recreated.metadata["unicode_test"]
            == special_chars_data["metadata"]["unicode_test"]
        )

    def test_large_metadata_handling(self):
        """Test handling of large metadata objects."""
        # Create a large metadata object
        large_metadata = {
            f"key_{i}": {
                "data": list(range(100)),
                "text": "Large text field with repeated content " * 50,
                "nested": {f"nested_key_{j}": f"nested_value_{j}" for j in range(50)},
            }
            for i in range(10)
        }

        result = ClientResult(test_id="large_metadata_test", metadata=large_metadata)

        # Should handle large metadata without issues
        assert len(result.metadata) == 10
        assert len(result.metadata["key_0"]["data"]) == 100

        # Should be serializable (though it might be large)
        json_str = result.model_dump_json()
        assert len(json_str) > 1000  # Should be a substantial JSON string

    @pytest.mark.parametrize(
        "test_data",
        [
            # Duration boundary values - positive
            {"duration": 0.0},
            {"duration": 0.001},  # Very small positive
            {"duration": 999999.999},  # Very large
            # Duration boundary values - negative and special
            {"duration": -0.001},  # Very small negative
            {"duration": -999999.999},  # Very large negative
            {"duration": float("nan")},  # NaN
            {"duration": float("inf")},  # Infinity
            {"duration": float("-inf")},  # -Infinity
            # Metadata with boundary values - positive
            {"metadata": {"count": 0}},
            {"metadata": {"count": 2**31 - 1}},  # Max 32-bit int
            {"metadata": {"ratio": 0.0}},
            {"metadata": {"ratio": 1.0}},
            # Metadata with boundary values - negative and special
            {"metadata": {"count": -1}},  # Negative count
            {"metadata": {"count": -(2**31)}},  # Min 32-bit int
            {"metadata": {"ratio": -1.0}},  # Negative ratio
            {"metadata": {"ratio": float("nan")}},  # NaN ratio
            {"metadata": {"ratio": float("inf")}},  # Infinity ratio
            {"metadata": {"ratio": float("-inf")}},  # -Infinity ratio
        ],
    )
    def test_boundary_values(self, test_data):
        """Test boundary values for numeric fields including negative and NaN values."""
        result = ClientResult(test_id="boundary_test", **test_data)
        # Should create successfully without validation errors
        assert result.test_id == "boundary_test"

    def test_empty_and_null_values(self):
        """Test handling of empty and null values."""
        # Test with minimal required fields only
        minimal_result = ClientResult(test_id="minimal")
        assert minimal_result.test_id == "minimal"

        # Test with explicit None values for optional fields
        result_with_nones = ClientResult(
            test_id="nones_test",
            component=None,
            env=None,
            run_id=None,
            project_id=None,
            metadata=None,
            params=None,
        )
        assert result_with_nones.test_id == "nones_test"
        assert result_with_nones.component is None

        # Test with empty collections
        result_with_empties = ClientResult(
            test_id="empties_test", metadata={}, params={}
        )
        assert result_with_empties.metadata == {}
        assert result_with_empties.params == {}


class TestRealWorldScenarios:
    """Test real-world usage scenarios for model validation."""

    def test_pytest_parameterized_test_result(self):
        """Test result from a parameterized pytest test."""
        # Simulate a parameterized test result
        result = ClientResult(
            test_id="test_login[user1-password1]",
            result="passed",
            component="authentication",
            env="staging",
            source="pytest-ibutsu",
            duration=1.25,
            metadata={
                "markers": [
                    {
                        "name": "parametrize",
                        "args": [
                            "username,password",
                            [("user1", "password1"), ("user2", "password2")],
                        ],
                        "kwargs": {},
                    }
                ],
                "node_id": "tests/test_auth.py::test_login[user1-password1]",
                "fspath": "tests/test_auth.py",
            },
            params={"username": "user1", "password": "password1"},
        )

        assert result.test_id == "test_login[user1-password1]"
        assert result.params["username"] == "user1"
        assert "parametrize" in [m["name"] for m in result.metadata["markers"]]

    def test_failed_test_with_exception_info(self):
        """Test result from a failed test with exception information."""
        result = ClientResult(
            test_id="test_database_connection",
            result="failed",
            component="database",
            env="production",
            source="pytest-ibutsu",
            duration=5.0,
            metadata={
                "exception_name": "ConnectionError",
                "short_tb": "ConnectionError: Could not connect to database\n  at database.py:45",
                "statuses": {
                    "setup": ("passed", False),
                    "call": ("failed", False),
                    "teardown": ("passed", False),
                },
                "durations": {"setup": 0.1, "call": 4.8, "teardown": 0.1},
                "classification": "environment_failure",
            },
        )

        assert result.result == "failed"
        assert result.metadata["exception_name"] == "ConnectionError"
        assert result.metadata["classification"] == "environment_failure"

    def test_xfailed_test_result(self):
        """Test result from an expected failure (xfail)."""
        result = ClientResult(
            test_id="test_known_bug",
            result="xfailed",
            component="payments",
            env="test",
            source="pytest-ibutsu",
            duration=0.5,
            metadata={
                "markers": [
                    {
                        "name": "xfail",
                        "args": [],
                        "kwargs": {
                            "reason": "Known issue with payment gateway",
                            "strict": False,
                        },
                    }
                ],
                "xfail_reason": "Known issue with payment gateway",
                "statuses": {"setup": ("passed", False), "call": ("skipped", True)},
            },
        )

        assert result.result == "xfailed"
        assert result.metadata["xfail_reason"] == "Known issue with payment gateway"

    def test_integration_test_run(self):
        """Test run data from a comprehensive integration test suite."""
        run = ClientRun(
            component="full-stack-integration",
            env="staging",
            source="pytest-ibutsu",
            start_time=datetime.now(UTC).isoformat(),
            duration=1200.0,  # 20 minutes
            metadata={
                "jenkins": {
                    "job_name": "integration-tests",
                    "build_number": "567",
                    "build_url": "http://jenkins.example.com/job/integration-tests/567",
                },
                "environment": {
                    "api_version": "v2.3.1",
                    "database_version": "PostgreSQL 13.4",
                    "redis_version": "6.2.5",
                    "elasticsearch_version": "7.15.0",
                },
                "test_counts": {"total": 45, "passed": 42, "failed": 2, "skipped": 1},
                "git_info": {
                    "commit": "a1b2c3d4e5f6",
                    "branch": "develop",
                    "author": "dev-team@example.com",
                },
            },
        )

        assert run.component == "full-stack-integration"
        assert run.duration == 1200.0
        assert run.metadata["test_counts"]["total"] == 45
        assert run.metadata["jenkins"]["job_name"] == "integration-tests"
