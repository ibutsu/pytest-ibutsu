from __future__ import annotations

import json
import re
import tarfile
import uuid
from collections import namedtuple
from pathlib import Path
from typing import Iterator, Any

import pytest_subtests

import expected_results
import pytest

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


def test_archive_file(
    pytester: pytest.Pytester, test_data: tuple[pytest.RunResult, str]
) -> None:
    result, run_id = test_data
    result.stdout.no_re_match_line("INTERNALERROR")
    result.stdout.re_match_lines([f".*Saved results archive to {run_id}.tar.gz$"])
    archive_name = f"{run_id}.tar.gz"
    archive = pytester.path.joinpath(archive_name)
    assert archive.is_file()
    assert archive.lstat().st_size > 0


@pytest.mark.usefixtures("test_data")
def test_archives_count(pytester: pytest.Pytester) -> None:
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
    subtests: pytest_subtests.SubTests,
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
    subtests: pytest_subtests.SubTests,
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
