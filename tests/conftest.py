"""
Session-scoped fixtures for sharing temporary files across tests.

This module provides pytest fixtures that use tmpdir_factory to create
session-scoped temporary directories and files that can be shared across
multiple test functions, following pytest best practices.
"""

import pytest
from pathlib import Path

from pytest_ibutsu.s3_uploader import S3Uploader


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
