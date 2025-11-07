from __future__ import annotations

import json
import re
import tarfile
import uuid
import pytest
from collections import namedtuple
from pathlib import Path
from typing import Iterator, Any
from unittest.mock import Mock, patch

from pytest_ibutsu.archiver import IbutsuArchiver, dump_to_archive
from pytest_ibutsu.modeling import IbutsuTestResult, IbutsuTestRun

from . import expected_results

# This should match uuid.uuid4()
ARCHIVE_REGEX = re.compile(
    r"^([0-9a-fA-F]{8}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{12})\.tar\.gz$"
)

# Constants for artifact types
COLLECTED_ARTIFACTS = {"runtest_teardown", "runtest"}
FAILED_ARTIFACTS = {"legacy_exception", "actual_exception"}
ALL_ARTIFACT_TYPES = [
    "legacy_exception",
    "actual_exception",
    "runtest_teardown",
    "runtest",
]

# Archive and test constants
ARCHIVE_MODE = "archive"
TEST_PROJECT = "test_project"
IBUTSU_ARCHIVE_ARG = "--ibutsu=archive"
PROJECT_ARG = "--ibutsu-project=test_project"
SOME_MARKER = "some_marker"
EXAMPLE_TEST_FILE = "example_test_to_report_to_ibutsu.py"
SERIALIZATION_FAILED_ERROR = "serialization_failed"

CURRENT_DIR = Path(__file__).parent

pytest_plugins = "pytester"


def remove_varying_fields_from_result(result: dict[str, Any]) -> dict[str, Any]:
    del result["id"]
    del result["run_id"]
    del result["start_time"]
    del result["duration"]
    del result["metadata"]["run"]
    del result["metadata"]["durations"]
    return result


@pytest.fixture
def run_id() -> str:
    return str(uuid.uuid4())


def run_pytest(pytester: pytest.Pytester, args: list[str]) -> pytest.RunResult:
    pytester.copy_example(str(CURRENT_DIR / EXAMPLE_TEST_FILE))
    pytester.makeconftest((CURRENT_DIR / "example_conftest.py").read_text())
    return pytester.runpytest(*args)


Param = namedtuple("Param", ["run_twice", "pytest_args"])

NO_XDIST_ARGS = [
    IBUTSU_ARCHIVE_ARG,
    PROJECT_ARG,
    EXAMPLE_TEST_FILE,
]

XDIST_ARGS = [
    IBUTSU_ARCHIVE_ARG,
    PROJECT_ARG,
    "-p",
    "xdist",
    "-n",
    "2",
    EXAMPLE_TEST_FILE,
]

PYTEST_XDIST_ARGS = [
    pytest.param(Param(False, NO_XDIST_ARGS), id="no-xdist-run-once"),
    pytest.param(Param(False, XDIST_ARGS), id="xdist-run-once"),
    pytest.param(Param(True, NO_XDIST_ARGS), id="no-xdist-run-twice"),
    pytest.param(Param(True, XDIST_ARGS), id="xdist-run-twice"),
]


@pytest.fixture(params=PYTEST_XDIST_ARGS)
def test_data(
    run_id: str, pytester: pytest.Pytester, request: pytest.FixtureRequest
) -> tuple[pytest.RunResult, str]:
    args = list(request.param.pytest_args)  # type: ignore
    if request.param.run_twice:  # type: ignore
        run_id = str(uuid.uuid4())
        run_pytest(pytester, args + [f"--ibutsu-run-id={run_id}"])
        return (
            run_pytest(
                pytester, args + ["-m", SOME_MARKER, f"--ibutsu-run-id={run_id}"]
            ),
            run_id,
        )
    result = run_pytest(pytester, args + ["-m", SOME_MARKER])
    for path in pytester.path.glob("*.tar.gz"):
        if match := re.match(ARCHIVE_REGEX, path.name):
            return result, match.group(1)
    pytest.fail("No archives were created")


def test_archive_creation_comprehensive(
    pytester: pytest.Pytester,
    test_data: tuple[pytest.RunResult, str],
):
    """Test that no internal errors occurred during pytest execution."""
    result, run_id = test_data
    result.stdout.no_re_match_line("INTERNALERROR")

    archive_name = f"{run_id}.tar.gz"
    archive = pytester.path.joinpath(archive_name)
    assert archive.is_file()
    assert archive.lstat().st_size > 0

    archives = 0
    for path in pytester.path.glob("*"):
        archives += 1 if re.match(ARCHIVE_REGEX, path.name) else 0
    assert archives == 1, f"Expected exactly one archive file, got {archives}"


@pytest.fixture
def archive(
    test_data: tuple[pytest.RunResult, str], pytester: pytest.Pytester
) -> Iterator[tarfile.TarFile]:
    _, run_id = test_data
    archive_name = f"{run_id}.tar.gz"
    archive_path = pytester.path.joinpath(archive_name)
    with tarfile.open(archive_path, "r:gz") as tar:
        yield tar


def test_archive_content_run(
    request: pytest.FixtureRequest,
    archive: tarfile.TarFile,
    test_data: tuple[pytest.RunResult, str],
):
    _, run_id = test_data
    run_twice = request.node.callspec.params["test_data"].run_twice
    members = archive.getmembers()
    assert members[0].isdir(), "root dir is missing"
    assert members[1].isfile(), "run.json is missing"
    assert members[1].name == f"{run_id}/run.json"
    o = archive.extractfile(members[1])
    loaded = json.load(o)  # type: ignore
    assert loaded["id"] == run_id
    assert "start_time" in loaded
    assert loaded["start_time"]
    assert "duration" in loaded
    assert loaded["duration"]
    # remove fields that vary
    del loaded["id"]
    del loaded["start_time"]
    del loaded["duration"]
    assert loaded == expected_results.RUNS["run_twice" if run_twice else "run_once"]


def test_archive_content_results_count(
    request: pytest.FixtureRequest,
    archive: tarfile.TarFile,
    test_data: tuple[pytest.RunResult, str],
):
    """Test that the correct number of result files exist in the archive."""
    run_twice = request.node.callspec.params["test_data"].run_twice
    members = [
        m for m in archive.getmembers() if m.isfile() and "result.json" in m.name
    ]
    assert len(members) == 7 if run_twice else 3


def test_archive_content_results_validation(
    request: pytest.FixtureRequest,
    archive: tarfile.TarFile,
    test_data: tuple[pytest.RunResult, str],
):
    """Test that all result files in archive have correct content and structure."""
    _, run_id = test_data
    members = [
        m for m in archive.getmembers() if m.isfile() and "result.json" in m.name
    ]

    # Test each result individually, but in a single test function for better performance
    for member in members:
        o = archive.extractfile(member)
        result_json = json.load(o)  # type: ignore
        result = result_json.copy()  # Make a copy to avoid modifying original

        # Basic structure validation
        assert "id" in result, f"Result {member.name} missing 'id' field"
        assert result["id"], f"Result {member.name} has empty 'id' field"
        assert "start_time" in result, (
            f"Result {member.name} missing 'start_time' field"
        )
        assert result["start_time"], (
            f"Result {member.name} has empty 'start_time' field"
        )
        assert "duration" in result, f"Result {member.name} missing 'duration' field"
        assert result["duration"], f"Result {member.name} has empty 'duration' field"
        assert "run_id" in result, f"Result {member.name} missing 'run_id' field"
        assert result["run_id"] == run_id, f"Result {member.name} has incorrect run_id"
        assert result["metadata"]["run"] == run_id, (
            f"Result {member.name} metadata has incorrect run_id"
        )

        # Content validation against expected results
        result = remove_varying_fields_from_result(result)
        expected_result = expected_results.RESULTS[result["test_id"]]
        assert result == expected_result, f"Result {member.name} content mismatch"


@pytest.mark.parametrize("artifact_name", ALL_ARTIFACT_TYPES)
def test_archive_artifacts_comprehensive(
    archive: tarfile.TarFile,
    artifact_name: str,
    test_data: tuple[pytest.RunResult, str],
):
    """Test artifact files count, run artifact, and content comprehensively."""
    _, run_id = test_data
    run_json_tar_info = archive.extractfile(archive.getmembers()[1])
    run_json = json.load(run_json_tar_info)  # type: ignore

    # Find all artifact files for this artifact type
    members = [
        m
        for m in archive.getmembers()
        if m.isfile() and f"{artifact_name}.log" in m.name
    ]

    # Determine expected summary key based on artifact type using sets
    is_collected_artifact = artifact_name in COLLECTED_ARTIFACTS
    summary_key = "collected" if is_collected_artifact else "failures"
    artifact_description = "collected" if is_collected_artifact else "failed"

    # Test 1: Correct number of artifact files exist
    assert len(members) == run_json["summary"][summary_key], (
        f"There should be {artifact_name}.log for each {artifact_description} test"
    )

    # Test 2: Run artifact exists and has correct content
    run_artifact = archive.extractfile(f"{run_id}/some_artifact.log")
    assert run_artifact.read() == bytes("some_artifact", "utf8")  # type: ignore

    # Test 3: Each artifact file has correct content
    for member in members:
        test_uuid = Path(member.name).parent.stem
        log = archive.extractfile(member)
        expected_content = bytes(f"{artifact_name}_{test_uuid}", "utf8")
        actual_content = log.read()
        assert actual_content == expected_content, (
            f"Content mismatch for {member.name}: "
            f"expected {expected_content!r}, got {actual_content!r}"
        )


PYTEST_COLLECT_ARGS = [
    pytest.param(
        [
            IBUTSU_ARCHIVE_ARG,
            PROJECT_ARG,
            "--collect-only",
            EXAMPLE_TEST_FILE,
        ],
        id="no-xdist-collect-only",
    ),
    pytest.param(
        [
            IBUTSU_ARCHIVE_ARG,
            PROJECT_ARG,
            "--collect-only",
            EXAMPLE_TEST_FILE,
            "-n",
            "2",
        ],
        id="xdist-collect-only",
    ),
    pytest.param(
        [
            IBUTSU_ARCHIVE_ARG,
            PROJECT_ARG,
            "-k",
            "test_that_doesnt_exist",
        ],
        id="no-xdist-nothing-collected",
    ),
    pytest.param(
        [
            IBUTSU_ARCHIVE_ARG,
            PROJECT_ARG,
            "-k",
            "test_that_doesnt_exist",
            "-n",
            "2",
        ],
        id="xdist-nothing-collected",
    ),
]


@pytest.fixture(params=PYTEST_COLLECT_ARGS)
def pytest_collect_test(
    pytester: pytest.Pytester, request: pytest.FixtureRequest
) -> pytest.RunResult:
    return run_pytest(pytester, request.param)  # type: ignore


def test_collect(pytester: pytest.Pytester, pytest_collect_test: pytest.RunResult):
    pytest_collect_test.stdout.no_re_match_line("INTERNALERROR")
    archives = 0
    for path in pytester.path.glob("*"):
        archives += 1 if re.match(ARCHIVE_REGEX, path.name) else 0
    assert archives == 0, f"No archives should be created, got {archives}"


@pytest.fixture(
    params=[
        pytest.param(
            (
                "add_result",
                IbutsuTestResult,
                (IbutsuTestRun(id="test-run"), IbutsuTestResult(test_id="test1")),
            ),
            id="add_result",
        ),
        pytest.param(
            ("add_run", IbutsuTestRun, (IbutsuTestRun(id="test-run"),)), id="add_run"
        ),
    ]
)
def archiver_test_data(request):
    """Fixture providing method, primary type, and factory for creating test data objects."""
    method, primary_type, objects = request.param

    # Find the primary data object and construct args accordingly
    primary_obj = next(
        (obj for obj in objects if isinstance(obj, primary_type)), objects[0]
    )
    run_obj = next((obj for obj in objects if isinstance(obj, IbutsuTestRun)), None)

    # For IbutsuTestResult: args=(run, result), data_obj=result
    # For IbutsuTestRun: args=(run,), data_obj=run
    args = (
        (run_obj, primary_obj) if primary_type == IbutsuTestResult else (primary_obj,)
    )
    archive_args = (args, primary_obj)

    return method, primary_type, archive_args


class TestIbutsuArchiverExtended:
    """Consolidated tests for IbutsuArchiver to reduce duplicate coverage."""

    def test_serialization_fallback_error(self, archive_name, archiver_test_data):
        """Test serialization fallback when initial serialization fails."""
        method, _, archive_args = archiver_test_data
        args = archive_args[0]

        archiver = IbutsuArchiver(archive_name)

        with archiver:
            # Mock cattrs converter to raise an exception
            with patch(
                "pytest_ibutsu.archiver.ibutsu_converter.unstructure"
            ) as mock_unstructure:
                mock_unstructure.side_effect = TypeError("Cattrs failed")

                # Call the appropriate method
                getattr(archiver, method)(*args)

                # Verify fallback was used
                assert mock_unstructure.called

    def test_complete_serialization_failure(self, archive_name, archiver_test_data):
        """Test complete serialization failure for both cattrs and to_dict."""
        method, primary_type, _ = archiver_test_data

        # Create failing classes that cause to_dict to fail
        if primary_type == IbutsuTestResult:

            class FailingIbutsuTestResult(IbutsuTestResult):
                def to_dict(self):
                    raise RuntimeError("to_dict failed")

            run = IbutsuTestRun(id="test-run")
            data_obj = FailingIbutsuTestResult(test_id="test1")
            args = (run, data_obj)
            expected_error_field = "result_id"
        else:  # IbutsuTestRun

            class FailingIbutsuTestRun(IbutsuTestRun):
                def to_dict(self):
                    raise RuntimeError("to_dict failed")

            data_obj = FailingIbutsuTestRun(id="test-run")
            args = (data_obj,)
            expected_error_field = "run_id"

        # Test with a completed archive - create and then read
        with IbutsuArchiver(archive_name) as archiver:
            # Mock cattrs converter to raise an exception
            with patch(
                "pytest_ibutsu.archiver.ibutsu_converter.unstructure"
            ) as mock_unstructure:
                mock_unstructure.side_effect = TypeError("Cattrs failed")

                # Call the appropriate method - should not raise but create fallback content
                getattr(archiver, method)(*args)

        # Now read the archive to verify error content was created
        with tarfile.open(f"{archive_name}.tar.gz", "r:gz") as tar:
            members = tar.getmembers()
            json_file = next(m for m in members if ".json" in m.name)
            content = tar.extractfile(json_file).read()
            error_data = json.loads(content)
            assert error_data["error"] == SERIALIZATION_FAILED_ERROR
            assert error_data[expected_error_field] == data_obj.id

    def test_artifact_file_not_found(self, tmp_path, archiver_test_data):
        """Test artifact handling when file is not found."""
        method, _, archive_args = archiver_test_data
        args, data_obj = archive_args

        # Add an artifact that references a non-existent file
        data_obj.attach_artifact("missing_file.log", "/nonexistent/file.log")

        archive_name = str(tmp_path / "test_archive")
        archiver = IbutsuArchiver(archive_name)

        with archiver:
            # Should not raise an exception, should skip the missing file
            getattr(archiver, method)(*args)

            # Verify only the JSON was added, not the missing artifact
            members = archiver.tar.getmembers()
            artifact_files = [m for m in members if "missing_file.log" in m.name]
            assert len(artifact_files) == 0

    def test_artifact_is_directory(self, tmp_path, archiver_test_data):
        """Test artifact handling when artifact points to a directory."""
        method, _, archive_args = archiver_test_data
        args, data_obj = archive_args

        # Add an artifact that references a directory
        artifact_dir = tmp_path / "artifact_directory"
        artifact_dir.mkdir()
        data_obj.attach_artifact("directory.log", str(artifact_dir))

        archive_name = str(tmp_path / "test_archive")
        archiver = IbutsuArchiver(archive_name)

        with archiver:
            # Should not raise an exception, should skip the directory
            getattr(archiver, method)(*args)

            # Verify only the JSON was added, not the directory
            members = archiver.tar.getmembers()
            artifact_files = [m for m in members if "directory.log" in m.name]
            assert len(artifact_files) == 0

    def test_get_bytes_with_string_path(self, shared_test_files):
        """Test _get_bytes with string path to file."""
        test_file = shared_test_files / "test_content.txt"

        result = IbutsuArchiver._get_bytes(str(test_file))
        assert result == b"test content"

    def test_get_bytes_with_bytes_input(self):
        """Test _get_bytes with bytes input."""
        test_bytes = b"test content"
        result = IbutsuArchiver._get_bytes(test_bytes)
        assert result == test_bytes


class TestDumpToArchive:
    """Test the dump_to_archive function."""

    @pytest.mark.parametrize(
        "mock_archive_plugin_config",
        [
            {
                "run_id": "test-run",
                "results": {
                    "result1": IbutsuTestResult(test_id="test1"),
                    "result2": IbutsuTestResult(test_id="test2"),
                },
            }
        ],
        indirect=True,
    )
    def test_dump_to_archive_integration(self, tmp_path, caplog, mock_archive_plugin):
        """Test dump_to_archive function with mock plugin."""
        import logging

        caplog.set_level(logging.INFO)

        mock_plugin = mock_archive_plugin

        original_cwd = Path.cwd()
        try:
            # Change to temporary directory for the test
            import os

            os.chdir(tmp_path)

            dump_to_archive(mock_plugin)

            # Verify the archive was created
            archive_path = tmp_path / f"{mock_plugin.run.id}.tar.gz"
            assert archive_path.exists()

            # Verify summary_info was updated correctly
            assert mock_plugin.summary_info["archive_created"] is True
            assert (
                mock_plugin.summary_info["archive_path"]
                == f"{mock_plugin.run.id}.tar.gz"
            )

        finally:
            os.chdir(original_cwd)


class TestArchiverBasicMethods:
    """Test basic methods of IbutsuArchiver."""

    def test_add_dir(self, archive_name):
        """Test add_dir creates a directory entry."""
        archiver = IbutsuArchiver(archive_name)

        with archiver:
            archiver.add_dir("test_directory")

            # Verify directory was added
            members = archiver.tar.getmembers()
            assert any(m.name == "test_directory" and m.isdir() for m in members)

    def test_add_file(self, archive_name):
        """Test add_file creates a file entry."""
        archiver = IbutsuArchiver(archive_name)
        content = b"test file content"

        with archiver:
            archiver.add_file("test_file.txt", content)

            # Verify file was added
            members = archiver.tar.getmembers()
            assert any(m.name == "test_file.txt" and m.isfile() for m in members)

        # Read back and verify content
        with tarfile.open(f"{archive_name}.tar.gz", "r:gz") as tar:
            file_member = tar.getmember("test_file.txt")
            extracted_content = tar.extractfile(file_member).read()
            assert extracted_content == content

    def test_add_dir_with_timestamp(self, archive_name):
        """Test add_dir sets timestamp correctly."""
        import time

        archiver = IbutsuArchiver(archive_name)
        current_time = int(time.time())

        with archiver:
            archiver.add_dir("timestamped_dir")

            # Verify timestamp is close to current time
            members = archiver.tar.getmembers()
            dir_member = next(m for m in members if m.name == "timestamped_dir")
            assert abs(dir_member.mtime - current_time) < 5  # Within 5 seconds

    def test_add_file_with_mode(self, archive_name):
        """Test add_file sets correct mode."""
        archiver = IbutsuArchiver(archive_name)

        with archiver:
            archiver.add_file("mode_test.txt", b"content")

            # Verify mode is set correctly
            members = archiver.tar.getmembers()
            file_member = next(m for m in members if m.name == "mode_test.txt")
            assert file_member.mode == 33184

    def test_add_result_integration(self, archive_name):
        """Test add_result creates proper structure."""
        run = IbutsuTestRun(id="test-run")
        result = IbutsuTestResult(test_id="test1")
        result.metadata["test_data"] = "test value"

        with IbutsuArchiver(archive_name) as archiver:
            archiver.add_result(run, result)

        # Verify the archive structure
        with tarfile.open(f"{archive_name}.tar.gz", "r:gz") as tar:
            members = tar.getmembers()
            # Should have directory and result.json
            assert any(m.name == f"{run.id}/{result.id}" and m.isdir() for m in members)
            assert any(
                m.name == f"{run.id}/{result.id}/result.json" and m.isfile()
                for m in members
            )

    def test_add_run_integration(self, archive_name):
        """Test add_run creates proper structure."""
        run = IbutsuTestRun(id="test-run")
        run.metadata["run_data"] = "test value"

        with IbutsuArchiver(archive_name) as archiver:
            archiver.add_run(run)

        # Verify the archive structure
        with tarfile.open(f"{archive_name}.tar.gz", "r:gz") as tar:
            members = tar.getmembers()
            # Should have directory and run.json
            assert any(m.name == run.id and m.isdir() for m in members)
            assert any(m.name == f"{run.id}/run.json" and m.isfile() for m in members)

    def test_archiver_context_manager(self, tmp_path):
        """Test IbutsuArchiver as context manager."""
        archive_name = str(tmp_path / "test_archive")

        with IbutsuArchiver(archive_name) as archiver:
            assert archiver.tar is not None
            assert not archiver.tar.closed
            archiver.add_file("test.txt", b"content")

        # After context exit, tar should be closed
        assert archiver.tar.closed

        # Archive file should exist
        archive_path = Path(f"{archive_name}.tar.gz")
        assert archive_path.exists()

    def test_archiver_add_dir_creates_directory(self, tmp_path):
        """Test add_dir creates directory entry."""
        archive_name = str(tmp_path / "test_archive")

        with IbutsuArchiver(archive_name) as archiver:
            archiver.add_dir("test_dir")

            members = archiver.tar.getmembers()
            dir_member = next((m for m in members if m.name == "test_dir"), None)

            assert dir_member is not None
            assert dir_member.isdir()

    def test_archiver_add_file_creates_file(self, tmp_path):
        """Test add_file creates file entry."""
        archive_name = str(tmp_path / "test_archive")
        content = b"test file content"

        with IbutsuArchiver(archive_name) as archiver:
            archiver.add_file("test.txt", content)

            members = archiver.tar.getmembers()
            file_member = next((m for m in members if m.name == "test.txt"), None)

            assert file_member is not None
            assert file_member.isfile()
            assert file_member.size == len(content)

    def test_get_bytes_with_bytes(self, tmp_path):
        """Test _get_bytes with bytes input."""
        content = b"test bytes"
        result = IbutsuArchiver._get_bytes(content)
        assert result == content

    def test_get_bytes_with_path(self, tmp_path):
        """Test _get_bytes with file path."""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"file content")

        result = IbutsuArchiver._get_bytes(str(test_file))
        assert result == b"file content"

    def test_dump_to_archive_creates_archive(self, tmp_path, monkeypatch):
        """Test dump_to_archive creates archive file."""
        # Change to tmp_path for test
        original_cwd = Path.cwd()
        try:
            monkeypatch.chdir(tmp_path)

            # Create mock plugin
            plugin = Mock()
            plugin.run = IbutsuTestRun(id="test-run-123")
            plugin.results = {
                "r1": IbutsuTestResult(test_id="test1"),
                "r2": IbutsuTestResult(test_id="test2"),
            }
            plugin.summary_info = {}

            dump_to_archive(plugin)

            # Check archive was created
            archive_path = tmp_path / f"{plugin.run.id}.tar.gz"
            assert archive_path.exists()

            # Check summary_info was updated
            assert plugin.summary_info["archive_created"] is True
            assert plugin.summary_info["archive_path"] == f"{plugin.run.id}.tar.gz"
        finally:
            import os

            os.chdir(original_cwd)


class TestArchiverEdgeCases:
    """Test edge cases and error conditions in the archiver."""

    def test_context_manager_exception_handling(self, tmp_path):
        """Test that the context manager properly closes even on exceptions."""
        archive_name = str(tmp_path / "test_archive")
        archiver = IbutsuArchiver(archive_name)

        try:
            with archiver:
                # Simulate an exception during archiving
                raise RuntimeError("Test exception")
        except RuntimeError:
            pass

        # Verify the tarfile was closed properly
        assert archiver.tar.closed

    def test_archiver_with_special_characters_in_content(self, tmp_path):
        """Test archiver with special characters and binary content."""
        run = IbutsuTestRun(id="test-run")
        result = IbutsuTestResult(test_id="test1")

        # Add binary content and special characters
        binary_content = b"\x00\x01\x02\xff\xfe\xfd"
        result.attach_artifact("binary.dat", binary_content)

        result.metadata["unicode"] = "Special chars: ñáéíóú 中文 🚀"

        archive_name = str(tmp_path / "test_archive")

        # Create the archive first
        with IbutsuArchiver(archive_name) as archiver:
            archiver.add_result(run, result)

        # Then read it to verify content
        with tarfile.open(f"{archive_name}.tar.gz", "r:gz") as tar:
            members = tar.getmembers()
            binary_file = next(m for m in members if "binary.dat" in m.name)
            extracted_content = tar.extractfile(binary_file).read()
            assert extracted_content == binary_content
