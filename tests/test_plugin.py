import os
from datetime import datetime
from datetime import timezone
from typing import Generator

import pytest
from jose import jwt
from pytest_ibutsu.pytest_plugin import ExpiredTokenError
from pytest_ibutsu.pytest_plugin import IbutsuPlugin

pytest_plugins = "pytester"


@pytest.fixture
def isolate_ibutsu_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """
    Fixture to isolate all ibutsu environment variables during tests.

    This fixture stores the current values of all ibutsu-related environment variables,
    clears them for the duration of the test, and restores them afterwards.
    This prevents environment variables from the test session from affecting the
    pytester session and ensures test isolation.
    """
    # List of all environment variables that ibutsu uses
    ibutsu_env_vars = [
        "IBUTSU_MODE",
        "IBUTSU_TOKEN",
        "IBUTSU_SOURCE",
        "IBUTSU_PROJECT",
        "IBUTSU_RUN_ID",
        "IBUTSU_NO_ARCHIVE",
        "IBUTSU_DATA",
        "IBUTSU_CA_BUNDLE",
        "IBUTSU_ENV_ID",
        # Also include related AWS/S3 variables that might affect tests
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
        "AWS_BUCKET",
        "REQUESTS_CA_BUNDLE",
    ]

    # Store original values
    original_values = {}
    for var in ibutsu_env_vars:
        original_values[var] = os.environ.get(var)
        # Clear the environment variable
        if var in os.environ:
            monkeypatch.delenv(var, raising=False)

    # Set clean defaults for variables that should have default values
    monkeypatch.setenv("IBUTSU_PROJECT", "")  # Explicitly empty for most tests

    yield

    # Restoration is handled automatically by monkeypatch fixture cleanup


def test_from_config_without_project(
    isolate_ibutsu_env_vars: None, pytester: pytest.Pytester
) -> None:
    """Test the from_config classmethod raises a UsageError when no project is specified"""
    test_config = pytester.parseconfig("--ibutsu", "http://localhost:8080/api")

    with pytest.raises(pytest.UsageError):
        IbutsuPlugin.from_config(test_config)


def test_from_config_with_project(
    isolate_ibutsu_env_vars: None, pytester: pytest.Pytester
) -> None:
    """Test the from_config classmethod does not raise a UsageError when a project is specified"""
    test_config = pytester.parseconfig(
        "--ibutsu", "archive", "--ibutsu-project", "test-project"
    )
    IbutsuPlugin.from_config(test_config)


def test_expired_token(
    isolate_ibutsu_env_vars: None, pytester: pytest.Pytester
) -> None:
    """Test the ExpiredTokenError is raised when expired token is passed"""
    min_timestamp = datetime.min.replace(
        second=0, microsecond=0, tzinfo=timezone.utc
    ).timestamp()
    token = jwt.encode({"exp": min_timestamp}, "secret", algorithm="HS256")
    test_config = pytester.parseconfig(
        "--ibutsu",
        "archive",
        "--ibutsu-project",
        "test-project",
        "--ibutsu-token",
        token,
    )
    with pytest.raises(ExpiredTokenError):
        IbutsuPlugin.from_config(test_config)


def test_valid_token(isolate_ibutsu_env_vars: None, pytester: pytest.Pytester) -> None:
    """Test the ExpiredTokenError is NOT raised when valid token is passed"""
    max_timestamp = datetime.max.replace(
        second=0, microsecond=0, tzinfo=timezone.utc
    ).timestamp()
    token = jwt.encode({"exp": max_timestamp}, "secret", algorithm="HS256")
    test_config = pytester.parseconfig(
        "--ibutsu",
        "archive",
        "--ibutsu-project",
        "test-project",
        "--ibutsu-token",
        token,
    )
    IbutsuPlugin.from_config(test_config)


def test_ibutsu_data_single_pair(
    isolate_ibutsu_env_vars: None, pytester: pytest.Pytester
) -> None:
    """Test --ibutsu-data with a single KEY=VALUE pair"""
    test_config = pytester.parseconfig(
        "--ibutsu", "archive", "--ibutsu-data", "key=value"
    )
    plugin = IbutsuPlugin.from_config(test_config)
    assert plugin.extra_data == {"key": "value"}


def test_ibutsu_data_multiple_pairs_separate_flags(
    isolate_ibutsu_env_vars: None, pytester: pytest.Pytester
) -> None:
    """Test --ibutsu-data with multiple KEY=VALUE pairs using separate flags"""
    test_config = pytester.parseconfig(
        "--ibutsu",
        "archive",
        "--ibutsu-data",
        "key1=value1",
        "--ibutsu-data",
        "key2=value2",
        "--ibutsu-data",
        "key3=value3",
    )
    plugin = IbutsuPlugin.from_config(test_config)
    expected = {"key1": "value1", "key2": "value2", "key3": "value3"}
    assert plugin.extra_data == expected


def test_ibutsu_data_nested_keys(
    isolate_ibutsu_env_vars: None, pytester: pytest.Pytester
) -> None:
    """Test --ibutsu-data with nested keys using dot notation"""
    test_config = pytester.parseconfig(
        "--ibutsu",
        "archive",
        "--ibutsu-data",
        "outer.inner.key=value",
        "--ibutsu-data",
        "outer.another=value2",
        "--ibutsu-data",
        "simple=value3",
    )
    plugin = IbutsuPlugin.from_config(test_config)
    expected = {
        "outer": {"inner": {"key": "value"}, "another": "value2"},
        "simple": "value3",
    }
    assert plugin.extra_data == expected


def test_ibutsu_data_argument_order_after_mode(
    isolate_ibutsu_env_vars: None, pytester: pytest.Pytester
) -> None:
    """Test that --ibutsu-data works when placed after --ibutsu mode (issue #8)"""
    test_config = pytester.parseconfig(
        "--ibutsu", "archive", "--ibutsu-data", "key=value"
    )
    plugin = IbutsuPlugin.from_config(test_config)
    assert plugin.extra_data == {"key": "value"}


def test_ibutsu_data_argument_order_before_mode(
    isolate_ibutsu_env_vars: None, pytester: pytest.Pytester
) -> None:
    """Test that --ibutsu-data works when placed before --ibutsu mode"""
    test_config = pytester.parseconfig(
        "--ibutsu-data", "key=value", "--ibutsu", "archive"
    )
    plugin = IbutsuPlugin.from_config(test_config)
    assert plugin.extra_data == {"key": "value"}


def test_ibutsu_data_mixed_order_with_multiple_values(
    isolate_ibutsu_env_vars: None, pytester: pytest.Pytester
) -> None:
    """Test --ibutsu-data with multiple values in various argument positions"""
    test_config = pytester.parseconfig(
        "--ibutsu-data",
        "before=value1",
        "--ibutsu",
        "archive",
        "--ibutsu-data",
        "after=value2",
        "--ibutsu-project",
        "test-project",
        "--ibutsu-data",
        "end=value3",
    )
    plugin = IbutsuPlugin.from_config(test_config)
    expected = {"before": "value1", "after": "value2", "end": "value3"}
    assert plugin.extra_data == expected


def test_ibutsu_data_invalid_format_no_equals(
    isolate_ibutsu_env_vars: None, pytester: pytest.Pytester
) -> None:
    """Test that --ibutsu-data with invalid format (no equals) raises ValueError"""
    test_config = pytester.parseconfig(
        "--ibutsu", "archive", "--ibutsu-data", "invalid_format"
    )
    with pytest.raises(
        ValueError, match="Invalid --ibutsu-data format.*Expected format: key=value"
    ):
        IbutsuPlugin.from_config(test_config)


def test_ibutsu_data_invalid_format_empty_key(
    isolate_ibutsu_env_vars: None, pytester: pytest.Pytester
) -> None:
    """Test that --ibutsu-data with empty key part is handled"""
    test_config = pytester.parseconfig("--ibutsu", "archive", "--ibutsu-data", "=value")
    plugin = IbutsuPlugin.from_config(test_config)
    # Empty key should create an empty string key
    assert plugin.extra_data == {"": "value"}


def test_ibutsu_data_empty_value(
    isolate_ibutsu_env_vars: None, pytester: pytest.Pytester
) -> None:
    """Test that --ibutsu-data with empty value part is handled"""
    test_config = pytester.parseconfig("--ibutsu", "archive", "--ibutsu-data", "key=")
    plugin = IbutsuPlugin.from_config(test_config)
    assert plugin.extra_data == {"key": ""}


def test_ibutsu_data_multiple_equals_in_value(
    isolate_ibutsu_env_vars: None, pytester: pytest.Pytester
) -> None:
    """Test that --ibutsu-data handles values with multiple equals signs"""
    test_config = pytester.parseconfig(
        "--ibutsu", "archive", "--ibutsu-data", "key=value=with=equals"
    )
    plugin = IbutsuPlugin.from_config(test_config)
    assert plugin.extra_data == {"key": "value=with=equals"}


def test_ibutsu_data_environment_variable_support(
    isolate_ibutsu_env_vars: None,
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that IBUTSU_DATA environment variable works"""
    monkeypatch.setenv("IBUTSU_DATA", "env_key=env_value another_key=another_value")
    test_config = pytester.parseconfig("--ibutsu", "archive")
    plugin = IbutsuPlugin.from_config(test_config)
    expected = {"env_key": "env_value", "another_key": "another_value"}
    assert plugin.extra_data == expected


def test_ibutsu_data_cli_overrides_environment(
    isolate_ibutsu_env_vars: None,
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that CLI --ibutsu-data takes precedence over environment variable"""
    monkeypatch.setenv("IBUTSU_DATA", "env_key=env_value")
    test_config = pytester.parseconfig(
        "--ibutsu", "archive", "--ibutsu-data", "cli_key=cli_value"
    )
    plugin = IbutsuPlugin.from_config(test_config)
    # CLI should override environment
    assert plugin.extra_data == {"cli_key": "cli_value"}
