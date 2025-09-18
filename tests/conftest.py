"""
Session-scoped fixtures for sharing temporary files across tests.

This module provides pytest fixtures that use tmpdir_factory to create
session-scoped temporary directories and files that can be shared across
multiple test functions, following pytest best practices.

It also provides fixtures for creating mock objects consistently across tests,
migrated from test_utils.py to enable parametrization and broader application.
"""

import pytest
import uuid
from pathlib import Path
from datetime import datetime, UTC
from unittest.mock import Mock

from pytest_ibutsu.s3_uploader import S3Uploader
from pytest_ibutsu.modeling import IbutsuTestRun


@pytest.fixture(scope="session")
def shared_test_files(tmpdir_factory) -> Path:
    """Create session-scoped temporary directory with common test files.

    This fixture creates a temporary directory with commonly used test files
    that can be shared across multiple tests, reducing redundant file creation.

    Returns:
        Path: Session-scoped temporary directory containing test files
    """
    # Create session-scoped temporary directory
    session_dir = tmpdir_factory.mktemp("shared_test_files")
    session_path = Path(str(session_dir))

    # Create common test content files that multiple tests can use
    test_content_file = session_path / "test_content.txt"
    test_content_file.write_text("test content")

    binary_content_file = session_path / "binary_content.bin"
    binary_content_file.write_bytes(b"test content")

    # Create files with different sizes for S3 upload testing
    small_file = session_path / "small_file.txt"
    small_file.write_text("small")

    medium_file = session_path / "medium_file.txt"
    medium_file.write_text("medium content with more data")

    # Create UUID-patterned .tar.gz files for S3 testing
    uuid_archive_1 = session_path / "12345678-1234-1234-1234-123456789abc.tar.gz"
    uuid_archive_1.write_bytes(b"archive content 1")

    uuid_archive_2 = session_path / "87654321-4321-4321-4321-fedcba987654.tar.gz"
    uuid_archive_2.write_bytes(b"archive content 2")

    # Create non-UUID .tar.gz files for negative testing
    regular_archive = session_path / "regular_archive.tar.gz"
    regular_archive.write_bytes(b"regular archive content")

    # Create XML files for file filtering tests
    xml_file = session_path / "data.ibutsu.xml"
    xml_file.write_text("<xml>test data</xml>")

    return session_path


@pytest.fixture(scope="session")
def s3_uploader_instance() -> S3Uploader:
    """Create session-scoped S3Uploader instance.

    This fixture provides a shared S3Uploader instance with standard
    test configuration that can be reused across multiple test functions.

    Returns:
        S3Uploader: Configured uploader instance for testing
    """
    return S3Uploader(bucket_name="test-bucket")


@pytest.fixture
def archive_name(tmp_path) -> str:
    """Create function-scoped archive name.

    This fixture provides unique archive names for each test function
    while utilizing the tmp_path fixture for proper cleanup.

    Args:
        tmp_path: pytest's tmp_path fixture

    Returns:
        str: Full path to archive (without .tar.gz extension)
    """
    return str(tmp_path / "test_archive")


@pytest.fixture
def isolated_test_file(tmp_path) -> Path:
    """Create function-scoped test file for tests needing isolation.

    This fixture creates a unique test file for each test function
    that needs file isolation and cannot share session-scoped files.

    Args:
        tmp_path: pytest's tmp_path fixture

    Returns:
        Path: Unique test file path
    """
    test_file = tmp_path / "isolated_test_file.txt"
    test_file.write_text("isolated test content")
    return test_file


# Mock object fixtures migrated from test_utils.py


@pytest.fixture
def mock_ibutsu_plugin_config(request):
    """Configuration factory for mock_ibutsu_plugin fixture.

    This fixture can be parametrized to customize the mock plugin creation.
    Use pytest.param() with indirect=True to pass custom configuration.

    Default configuration that can be overridden:
    {
        "enabled": True,
        "run_id": "test-run",
        "results": None,
        "summary_info": None,
        "plugin_attrs": {}
    }
    """
    default_config = {
        "enabled": True,
        "run_id": "test-run",
        "results": None,
        "summary_info": None,
        "plugin_attrs": {},
    }

    # If parametrized (indirect=True), use the parameter value
    if hasattr(request, "param"):
        config = default_config.copy()
        config.update(request.param)
        return config

    return default_config


@pytest.fixture
def mock_ibutsu_plugin(mock_ibutsu_plugin_config):
    """Create a mock IbutsuPlugin with consistent structure.

    This fixture can be customized by parametrizing mock_ibutsu_plugin_config.

    Returns:
        Mock object configured as an IbutsuPlugin
    """
    config = mock_ibutsu_plugin_config
    mock_plugin = Mock()
    mock_plugin.enabled = config["enabled"]
    mock_plugin.run = IbutsuTestRun(id=config["run_id"])
    mock_plugin.results = config["results"] or {}

    # Set default summary_info structure
    summary_info = config["summary_info"]
    if summary_info is None:
        summary_info = {
            "archive_created": False,
            "archive_path": None,
            "s3_uploaded": False,
            "s3_upload_count": 0,
            "s3_upload_errors": 0,
            "s3_bucket": None,
            "server_uploaded": False,
            "server_url": None,
            "frontend_url": None,
            "errors": [],
        }
    mock_plugin.summary_info = summary_info

    # Set additional plugin attributes
    for attr, value in config["plugin_attrs"].items():
        setattr(mock_plugin, attr, value)

    return mock_plugin


@pytest.fixture
def mock_archive_plugin_config(request):
    """Configuration factory for mock_archive_plugin fixture.

    Default configuration that can be overridden:
    {
        "run_id": "test-run",
        "results": None,
        "archive_created": False,
        "archive_path": None
    }
    """
    default_config = {
        "run_id": "test-run",
        "results": None,
        "archive_created": False,
        "archive_path": None,
    }

    # If parametrized (indirect=True), use the parameter value
    if hasattr(request, "param"):
        config = default_config.copy()
        config.update(request.param)
        return config

    return default_config


@pytest.fixture
def mock_archive_plugin(mock_archive_plugin_config):
    """Create a mock plugin specifically for archive testing.

    This fixture can be customized by parametrizing mock_archive_plugin_config.

    Returns:
        Mock object configured for archive operations
    """
    config = mock_archive_plugin_config
    summary_info = {
        "archive_created": config["archive_created"],
        "archive_path": config["archive_path"],
        "server_uploaded": False,
        "server_url": None,
        "frontend_url": None,
        "errors": [],
    }

    # Use the base plugin fixture logic
    plugin_config = {
        "enabled": True,
        "run_id": config["run_id"],
        "results": config["results"],
        "summary_info": summary_info,
        "plugin_attrs": {},
    }

    mock_plugin = Mock()
    mock_plugin.enabled = plugin_config["enabled"]
    mock_plugin.run = IbutsuTestRun(id=plugin_config["run_id"])
    mock_plugin.results = plugin_config["results"] or {}
    mock_plugin.summary_info = summary_info

    return mock_plugin


@pytest.fixture
def mock_sender_plugin_config():
    """Configuration factory for mock_sender_plugin fixture.

    Default configuration that can be overridden:
    {
        "run_id": "test-run",
        "results": None,
        "server_uploaded": False,
        "server_url": None,
        "frontend_url": None,
        "errors": None
    }
    """
    return {
        "run_id": "test-run",
        "results": None,
        "server_uploaded": False,
        "server_url": None,
        "frontend_url": None,
        "errors": None,
    }


@pytest.fixture
def mock_sender_plugin(mock_sender_plugin_config):
    """Create a mock plugin specifically for sender testing.

    This fixture can be customized by parametrizing mock_sender_plugin_config.

    Returns:
        Mock object configured for sender operations
    """
    config = mock_sender_plugin_config
    summary_info = {
        "archive_created": False,
        "archive_path": None,
        "server_uploaded": config["server_uploaded"],
        "server_url": config["server_url"],
        "frontend_url": config["frontend_url"],
        "errors": config["errors"] or [],
    }

    # Use the base plugin fixture logic
    plugin_config = {
        "enabled": True,
        "run_id": config["run_id"],
        "results": config["results"],
        "summary_info": summary_info,
        "plugin_attrs": {},
    }

    mock_plugin = Mock()
    mock_plugin.enabled = plugin_config["enabled"]
    mock_plugin.run = IbutsuTestRun(id=plugin_config["run_id"])
    mock_plugin.results = plugin_config["results"] or {}
    mock_plugin.summary_info = summary_info

    return mock_plugin


@pytest.fixture
def mock_terminal_summary_plugin_config(request):
    """Configuration factory for mock_terminal_summary_plugin fixture.

    Default configuration that can be overridden:
    {
        "enabled": True,
        "is_archive_mode": False,
        "is_server_mode": False,
        "is_s3_mode": False,
        "run_id": "test-run",
        "summary_info_overrides": {}
    }
    """
    default_config = {
        "enabled": True,
        "is_archive_mode": False,
        "is_server_mode": False,
        "is_s3_mode": False,
        "run_id": "test-run",
        "summary_info_overrides": {},
    }

    # If parametrized (indirect=True), use the parameter value
    if hasattr(request, "param"):
        config = default_config.copy()
        config.update(request.param)
        return config

    return default_config


@pytest.fixture
def mock_terminal_summary_plugin(mock_terminal_summary_plugin_config):
    """Create a mock plugin for terminal summary testing.

    This fixture can be customized by parametrizing mock_terminal_summary_plugin_config.

    Returns:
        Mock object configured for terminal summary
    """
    config = mock_terminal_summary_plugin_config
    summary_info = {
        "archive_created": False,
        "archive_path": None,
        "s3_uploaded": False,
        "s3_upload_count": 0,
        "s3_upload_errors": 0,
        "s3_bucket": None,
        "server_uploaded": False,
        "server_url": None,
        "frontend_url": None,
        "errors": [],
    }
    summary_info.update(config["summary_info_overrides"])

    # Use the base plugin fixture logic
    plugin_config = {
        "enabled": config["enabled"],
        "run_id": config["run_id"],
        "results": {},
        "summary_info": summary_info,
        "plugin_attrs": {
            "is_archive_mode": config["is_archive_mode"],
            "is_server_mode": config["is_server_mode"],
            "is_s3_mode": config["is_s3_mode"],
        },
    }

    mock_plugin = Mock()
    mock_plugin.enabled = plugin_config["enabled"]
    mock_plugin.run = IbutsuTestRun(id=plugin_config["run_id"])
    mock_plugin.results = plugin_config["results"]
    mock_plugin.summary_info = summary_info

    # Set mode attributes explicitly to ensure they're set correctly
    mock_plugin.is_archive_mode = config["is_archive_mode"]
    mock_plugin.is_server_mode = config["is_server_mode"]
    mock_plugin.is_s3_mode = config["is_s3_mode"]

    # Set default plugin attributes that are expected by pytest_report_header
    mock_plugin.ibutsu_mode = "archive"  # Default mode
    mock_plugin.ibutsu_project = None
    mock_plugin.ibutsu_source = "local"
    mock_plugin.ibutsu_no_archive = False

    return mock_plugin


@pytest.fixture
def mock_stash_config_options(request):
    """Configuration factory for mock_stash_config fixture.

    Default configuration that can be overridden:
    {
        "plugin": None,
        "raise_keyerror": False
    }
    """
    default_config = {
        "plugin": None,
        "raise_keyerror": False,
    }

    # If parametrized (indirect=True), use the parameter value
    if hasattr(request, "param"):
        config = default_config.copy()
        config.update(request.param)
        return config

    return default_config


@pytest.fixture
def mock_stash_config(mock_stash_config_options):
    """Create a mock config object with stash for testing.

    This fixture can be customized by parametrizing mock_stash_config_options.

    Returns:
        Mock config object with properly configured stash
    """
    from pytest_ibutsu.pytest_plugin import ibutsu_plugin_key

    options = mock_stash_config_options
    config = Mock()
    config.getoption.return_value = None

    def mock_stash_getitem(self, key):
        if key == ibutsu_plugin_key:
            if options["raise_keyerror"]:
                raise KeyError()
            return options["plugin"]
        raise KeyError()

    config.stash.__getitem__ = mock_stash_getitem
    return config


# Common test data fixtures to reduce duplication


@pytest.fixture
def valid_result_enum_values():
    """Provide valid result enum values for testing."""
    return [
        "passed",
        "failed",
        "error",
        "skipped",
        "xpassed",
        "xfailed",
        "manual",
        "blocked",
    ]


@pytest.fixture
def invalid_result_enum_values():
    """Provide invalid result enum values for testing."""
    return [
        "unknown",
        "pending",
        "running",
        "cancelled",
        "timeout",
        "abort",
        "",
        123,
        [],
        "PASSED",  # Case sensitivity test
        "Failed",  # Case sensitivity test
        "XFAILED",  # Case sensitivity test
    ]


@pytest.fixture
def sample_uuids():
    """Provide sample UUIDs for testing."""
    return {
        "result_id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
    }


@pytest.fixture
def invalid_uuid_strings():
    """Provide invalid UUID strings for testing."""
    return [
        "not-a-uuid",
        "12345678-1234-1234-1234",  # Too short
        "12345678-1234-1234-1234-12345678901234",  # Too long
        "",
        "random-string",
        123,  # Not a string
        [],  # Not a string
        None,  # None should be allowed in some contexts but invalid in others
    ]


@pytest.fixture
def valid_result_data(sample_uuids):
    """Provide valid test result data for ClientResult creation."""
    return {
        "test_id": "test_function_123",
        "result": "passed",
        "component": "auth",
        "env": "production",
        "source": "pytest-ibutsu",
        "id": sample_uuids["result_id"],
        "run_id": sample_uuids["run_id"],
        "project_id": sample_uuids["project_id"],
        "start_time": datetime.now(UTC).isoformat(),
        "duration": 2.5,
        "metadata": {"key": "value"},
        "params": {"param": "value"},
    }


@pytest.fixture
def valid_run_data(sample_uuids):
    """Provide valid test run data for ClientRun creation."""
    return {
        "component": "authentication",
        "env": "staging",
        "source": "pytest-ibutsu",
        "id": sample_uuids["run_id"],
        "project_id": sample_uuids["project_id"],
        "start_time": datetime.now(UTC).isoformat(),
        "duration": 120.5,
        "metadata": {"build": "123", "branch": "main"},
    }


@pytest.fixture
def boundary_test_values():
    """Provide boundary values for numeric field testing."""
    return [
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
    ]


@pytest.fixture
def comprehensive_result_metadata():
    """Provide comprehensive metadata structure for testing."""
    return {
        "exception_name": "AssertionError",
        "short_tb": "AssertionError: Values don't match",
        "markers": [{"name": "database", "args": [], "kwargs": {}}],
        "durations": {"setup": 0.1, "call": 10.0, "teardown": 0.4},
        "statuses": {"setup": ("passed", False), "call": ("failed", False)},
        "classification": "test_failure",
        "user_properties": [("custom_prop", "custom_value")],
        "fspath": "tests/test_example.py",
        "node_id": "tests/test_example.py::test_function[param1]",
    }


@pytest.fixture
def comprehensive_run_metadata():
    """Provide comprehensive run metadata structure for testing."""
    return {
        "jenkins": {
            "job_name": "nightly-tests",
            "build_number": "456",
            "build_url": "http://jenkins.example.com/job/nightly-tests/456",
        },
        "git": {
            "commit": "def456abc",
            "branch": "release/v2.0",
            "author": "developer@example.com",
        },
        "environment": {
            "database_version": "13.4",
            "api_version": "2.1.0",
            "frontend_version": "1.5.2",
        },
        "test_counts": {"total": 45, "passed": 42, "failed": 2, "skipped": 1},
    }
