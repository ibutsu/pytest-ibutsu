from __future__ import annotations

import json
import re
import tarfile
import uuid
import tempfile
import pytest
from collections import namedtuple
from pathlib import Path
from typing import Iterator, Any
from unittest.mock import patch

from pytest_subtests import SubTests
from pytest_ibutsu.archiver import IbutsuArchiver, dump_to_archive
from pytest_ibutsu.modeling import TestResult, TestRun

from . import expected_results

# This should match uuid.uuid4()
ARCHIVE_REGEX = re.compile(
    r"^([0-9a-fA-F]{8}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{12})\.tar\.gz$"
)

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
    pytester.copy_example(str(CURRENT_DIR / "example_test_to_report_to_ibutsu.py"))
    pytester.makeconftest((CURRENT_DIR / "example_conftest.py").read_text())
    return pytester.runpytest(*args)


Param = namedtuple("Param", ["run_twice", "pytest_args"])

NO_XDIST_ARGS = [
    "--ibutsu=archive",
    "--ibutsu-project=test_project",
    "example_test_to_report_to_ibutsu.py",
]

XDIST_ARGS = [
    "--ibutsu=archive",
    "--ibutsu-project=test_project",
    "-p",
    "xdist",
    "-n",
    "2",
    "example_test_to_report_to_ibutsu.py",
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
                pytester, args + ["-m", "some_marker", f"--ibutsu-run-id={run_id}"]
            ),
            run_id,
        )
    result = run_pytest(pytester, args + ["-m", "some_marker"])
    for path in pytester.path.glob("*.tar.gz"):
        if match := re.match(ARCHIVE_REGEX, path.name):
            return result, match.group(1)
    pytest.fail("No archives were created")


def test_archive_creation_comprehensive(
    pytester: pytest.Pytester,
    test_data: tuple[pytest.RunResult, str],
    subtests: SubTests,
) -> None:
    """Comprehensive test for archive creation, validation and properties."""
    result, run_id = test_data

    with subtests.test("no_internal_errors"):
        # Test that no internal errors occurred during pytest execution
        result.stdout.no_re_match_line("INTERNALERROR")

    with subtests.test("file_creation_and_properties"):
        # Test archive file was created with correct properties
        archive_name = f"{run_id}.tar.gz"
        archive = pytester.path.joinpath(archive_name)
        assert archive.is_file()
        assert archive.lstat().st_size > 0

    with subtests.test("archive_count_validation"):
        # Test exactly one archive was created
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
) -> None:
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


def test_archive_content_results(
    request: pytest.FixtureRequest,
    archive: tarfile.TarFile,
    subtests: SubTests,
    test_data: tuple[pytest.RunResult, str],
) -> None:
    _, run_id = test_data
    run_twice = request.node.callspec.params["test_data"].run_twice
    members = [
        m for m in archive.getmembers() if m.isfile() and "result.json" in m.name
    ]
    assert len(members) == 7 if run_twice else 3
    for member in members:
        o = archive.extractfile(member)
        result = json.load(o)  # type: ignore
        with subtests.test(name=result["test_id"]):
            assert "id" in result
            assert result["id"]
            assert "start_time" in result
            assert result["start_time"]
            assert "duration" in result
            assert result["duration"]
            assert "run_id" in result
            assert result["run_id"] == run_id
            assert result["metadata"]["run"] == run_id
            result = remove_varying_fields_from_result(result)
            expected_result = expected_results.RESULTS[result["test_id"]]
            assert result == expected_result


@pytest.mark.parametrize(
    "artifact_name",
    ["legacy_exception", "actual_exception", "runtest_teardown", "runtest"],
)
def test_archive_artifacts(
    archive: tarfile.TarFile,
    subtests: SubTests,
    artifact_name: str,
    test_data: tuple[pytest.RunResult, str],
) -> None:
    _, run_id = test_data
    run_json_tar_info = archive.extractfile(archive.getmembers()[1])
    run_json = json.load(run_json_tar_info)  # type: ignore
    members = [
        m
        for m in archive.getmembers()
        if m.isfile() and f"{artifact_name}.log" in m.name
    ]
    collected_or_failed = (
        "collected" if artifact_name in ["runtest_teardown", "runtest"] else "failed"
    )
    collected_or_failures = (
        "collected" if artifact_name in ["runtest_teardown", "runtest"] else "failures"
    )
    assert len(members) == run_json["summary"][collected_or_failures], (
        f"There should be {artifact_name}.log for each {collected_or_failed} test"
    )
    run_artifact = archive.extractfile(f"{run_id}/some_artifact.log")
    assert run_artifact.read() == bytes("some_artifact", "utf8")  # type: ignore
    for member in members:
        test_uuid = Path(member.name).parent.stem
        with subtests.test(name=member.name):
            log = archive.extractfile(member)
            assert log.read() == bytes(f"{artifact_name}_{test_uuid}", "utf8")  # type: ignore


PYTEST_COLLECT_ARGS = [
    pytest.param(
        [
            "--ibutsu=archive",
            "--ibutsu-project=test_project",
            "--collect-only",
            "example_test_to_report_to_ibutsu.py",
        ],
        id="no-xdist-collect-only",
    ),
    pytest.param(
        [
            "--ibutsu=archive",
            "--ibutsu-project=test_project",
            "--collect-only",
            "example_test_to_report_to_ibutsu.py",
            "-n",
            "2",
        ],
        id="xdist-collect-only",
    ),
    pytest.param(
        [
            "--ibutsu=archive",
            "--ibutsu-project=test_project",
            "-k",
            "test_that_doesnt_exist",
        ],
        id="no-xdist-nothing-collected",
    ),
    pytest.param(
        [
            "--ibutsu=archive",
            "--ibutsu-project=test_project",
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


def test_collect(
    pytester: pytest.Pytester, pytest_collect_test: pytest.RunResult
) -> None:
    pytest_collect_test.stdout.no_re_match_line("INTERNALERROR")
    archives = 0
    for path in pytester.path.glob("*"):
        archives += 1 if re.match(ARCHIVE_REGEX, path.name) else 0
    assert archives == 0, f"No archives should be created, got {archives}"


class TestIbutsuArchiverExtended:
    """Consolidated tests for IbutsuArchiver to reduce duplicate coverage."""

    @pytest.mark.parametrize(
        "method,data_type",
        [
            ("add_result", "result"),
            ("add_run", "run"),
        ],
    )
    def test_serialization_fallback_error(self, method, data_type):
        """Test serialization fallback when initial serialization fails."""
        # Create appropriate data object
        if data_type == "result":
            run = TestRun(id="test-run")
            data_obj = TestResult(test_id="test1")
            args = (run, data_obj)
        else:  # run
            data_obj = TestRun(id="test-run")
            args = (data_obj,)

        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            archiver = IbutsuArchiver(tmp.name.replace(".tar.gz", ""))

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

    @pytest.mark.parametrize(
        "method,data_type",
        [
            ("add_result", "result"),
            ("add_run", "run"),
        ],
    )
    def test_complete_serialization_failure(self, method, data_type):
        """Test complete serialization failure for both cattrs and to_dict."""

        # Create failing classes that cause to_dict to fail
        if data_type == "result":

            class FailingTestResult(TestResult):
                def to_dict(self):
                    raise RuntimeError("to_dict failed")

            run = TestRun(id="test-run")
            data_obj = FailingTestResult(test_id="test1")
            args = (run, data_obj)
            expected_error_field = "result_id"
        else:  # run

            class FailingTestRun(TestRun):
                def to_dict(self):
                    raise RuntimeError("to_dict failed")

            data_obj = FailingTestRun(id="test-run")
            args = (data_obj,)
            expected_error_field = "run_id"

        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            archive_name = tmp.name.replace(".tar.gz", "")

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
            assert error_data["error"] == "serialization_failed"
            assert error_data[expected_error_field] == data_obj.id

    @pytest.mark.parametrize(
        "method,data_type",
        [
            ("add_result", "result"),
            ("add_run", "run"),
        ],
    )
    def test_artifact_file_not_found(self, method, data_type):
        """Test artifact handling when file is not found."""

        # Create appropriate data object and attach missing artifact
        if data_type == "result":
            run = TestRun(id="test-run")
            data_obj = TestResult(test_id="test1")
            args = (run, data_obj)
        else:  # run
            data_obj = TestRun(id="test-run")
            args = (data_obj,)

        # Add an artifact that references a non-existent file
        data_obj.attach_artifact("missing_file.log", "/nonexistent/file.log")

        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            archiver = IbutsuArchiver(tmp.name.replace(".tar.gz", ""))

            with archiver:
                # Should not raise an exception, should skip the missing file
                getattr(archiver, method)(*args)

                # Verify only the JSON was added, not the missing artifact
                members = archiver.tar.getmembers()
                artifact_files = [m for m in members if "missing_file.log" in m.name]
                assert len(artifact_files) == 0

    @pytest.mark.parametrize(
        "method,data_type",
        [
            ("add_result", "result"),
            ("add_run", "run"),
        ],
    )
    def test_artifact_is_directory(self, method, data_type):
        """Test artifact handling when artifact points to a directory."""

        # Create appropriate data object
        if data_type == "result":
            run = TestRun(id="test-run")
            data_obj = TestResult(test_id="test1")
            args = (run, data_obj)
        else:  # run
            data_obj = TestRun(id="test-run")
            args = (data_obj,)

        # Add an artifact that references a directory
        with tempfile.TemporaryDirectory() as tmpdir:
            data_obj.attach_artifact("directory.log", tmpdir)

            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                archiver = IbutsuArchiver(tmp.name.replace(".tar.gz", ""))

                with archiver:
                    # Should not raise an exception, should skip the directory
                    getattr(archiver, method)(*args)

                    # Verify only the JSON was added, not the directory
                    members = archiver.tar.getmembers()
                    artifact_files = [m for m in members if "directory.log" in m.name]
                    assert len(artifact_files) == 0

    def test_get_bytes_with_string_path(self):
        """Test _get_bytes with string path to file."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            tmp.write("test content")
            tmp.flush()

            result = IbutsuArchiver._get_bytes(tmp.name)
            assert result == b"test content"

            # Cleanup
            Path(tmp.name).unlink()

    def test_get_bytes_with_bytes_input(self):
        """Test _get_bytes with bytes input."""
        test_bytes = b"test content"
        result = IbutsuArchiver._get_bytes(test_bytes)
        assert result == test_bytes


class TestDumpToArchive:
    """Test the dump_to_archive function."""

    def test_dump_to_archive_integration(self, caplog):
        """Test dump_to_archive function with mock plugin."""
        import logging
        from unittest.mock import Mock

        caplog.set_level(logging.INFO)

        mock_plugin = Mock()
        mock_plugin.run = TestRun(id="test-run")
        mock_plugin.results = {
            "result1": TestResult(test_id="test1"),
            "result2": TestResult(test_id="test2"),
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = Path.cwd()
            try:
                # Change to temporary directory for the test
                import os

                os.chdir(tmpdir)

                dump_to_archive(mock_plugin)

                # Verify the archive was created
                archive_path = Path(f"{mock_plugin.run.id}.tar.gz")
                assert archive_path.exists()

                # Verify log message was captured
                assert "Saved results archive" in caplog.text
                assert mock_plugin.run.id in caplog.text

            finally:
                os.chdir(original_cwd)


class TestArchiverEdgeCases:
    """Test edge cases and error conditions in the archiver."""

    def test_context_manager_exception_handling(self):
        """Test that the context manager properly closes even on exceptions."""
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            archiver = IbutsuArchiver(tmp.name.replace(".tar.gz", ""))

            try:
                with archiver:
                    # Simulate an exception during archiving
                    raise RuntimeError("Test exception")
            except RuntimeError:
                pass

            # Verify the tarfile was closed properly
            assert archiver.tar.closed

    def test_archiver_with_special_characters_in_content(self):
        """Test archiver with special characters and binary content."""
        run = TestRun(id="test-run")
        result = TestResult(test_id="test1")

        # Add binary content and special characters
        binary_content = b"\x00\x01\x02\xff\xfe\xfd"
        result.attach_artifact("binary.dat", binary_content)

        result.metadata["unicode"] = "Special chars: ñáéíóú 中文 🚀"

        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            archive_name = tmp.name.replace(".tar.gz", "")

        # Create the archive first
        with IbutsuArchiver(archive_name) as archiver:
            archiver.add_result(run, result)

        # Then read it to verify content
        with tarfile.open(f"{archive_name}.tar.gz", "r:gz") as tar:
            members = tar.getmembers()
            binary_file = next(m for m in members if "binary.dat" in m.name)
            extracted_content = tar.extractfile(binary_file).read()
            assert extracted_content == binary_content
