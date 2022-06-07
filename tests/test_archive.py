import json
import re
import tarfile
import uuid
from pathlib import Path
from typing import Iterator
from typing import List

import expected_results
import pytest

ARCHIVE_REGEX = re.compile(
    r"^[0-9a-fA-F]{8}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{12}\.tar\.gz$"
)

CURRENT_DIR = Path(__file__).parent

pytest_plugins = "pytester"


def remove_varying_fields_from_result(result):
    del result["id"]
    del result["run_id"]
    del result["start_time"]
    del result["duration"]
    del result["metadata"]["run"]
    del result["metadata"]["durations"]
    return result


@pytest.fixture
def run_id():
    return str(uuid.uuid4())


def run_pytest(pytester: pytest.Pytester, args: List[str]) -> pytest.RunResult:
    pytester.copy_example(CURRENT_DIR / "example_test_to_report_to_ibutsu.py")
    pytester.makeconftest((CURRENT_DIR / "example_conftest.py").read_text())
    return pytester.runpytest(*args)


PYTEST_XDIST_ARGS = [
    pytest.param([], id="no-xdist"),
    pytest.param(["-p", "xdist", "-n", "2"], id="xdist"),
]


@pytest.fixture(params=PYTEST_XDIST_ARGS)
def result(
    pytester: pytest.Pytester, request: pytest.FixtureRequest, run_id: str
) -> pytest.RunResult:
    args = request.param + [
        "--ibutsu=archive",
        "--ibutsu-project=test_project",
        f"--ibutsu-run-id={run_id}",
        "example_test_to_report_to_ibutsu.py",
    ]
    return run_pytest(pytester, args)


def test_archive_file(pytester: pytest.Pytester, result: pytest.RunResult, run_id: str):
    result.stdout.no_re_match_line("INTERNALERROR")
    result.stdout.re_match_lines([f".*Saved results archive to {run_id}.tar.gz$"])
    archive_name = f"{run_id}.tar.gz"
    archive = pytester.path.joinpath(archive_name)
    assert archive.is_file()
    assert archive.lstat().st_size > 0


@pytest.mark.usefixtures("result")
def test_archives_count(pytester: pytest.Pytester):
    archives = 0
    for path in pytester.path.glob("*"):
        archives += 1 if re.match(ARCHIVE_REGEX, path.name) else 0
    assert archives == 1, f"Expected exactly one archive file, got {archives}"


@pytest.fixture
def archive(result, pytester: pytest.Pytester, run_id: str) -> Iterator[tarfile.TarFile]:
    archive_name = f"{run_id}.tar.gz"
    archive_path = pytester.path.joinpath(archive_name)
    with tarfile.open(archive_path, "r:gz") as tar:
        yield tar


def test_archive_content_run(archive: tarfile.TarFile, run_id: str):
    members = archive.getmembers()
    assert members[0].isdir(), "root dir is missing"
    assert members[1].isfile(), "run.json is missing"
    assert members[1].name == "run.json"
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
    assert loaded == expected_results.RUN


def test_archive_content_results(archive: tarfile.TarFile, subtests, run_id: str):
    members = [m for m in archive.getmembers() if m.isfile() and "result.json" in m.name]
    assert len(members) == 7
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
            result = remove_varying_fields_from_result(result)
            expected_result = expected_results.RESULTS[result["test_id"]]
            assert result == expected_result


@pytest.mark.parametrize(
    "artifact_name", ["legacy_exception", "actual_exception", "runtest_teardown", "runtest"]
)
def test_archive_artifacts(archive: tarfile.TarFile, subtests, artifact_name: str):
    run_json_tar_info = archive.extractfile(archive.getmembers()[1])
    run_json = json.load(run_json_tar_info)  # type: ignore
    members = [m for m in archive.getmembers() if m.isfile() and f"{artifact_name}.log" in m.name]
    collected_or_failed = (
        "collected" if artifact_name in ["runtest_teardown", "runtest"] else "failed"
    )
    collected_or_failures = (
        "collected" if artifact_name in ["runtest_teardown", "runtest"] else "failures"
    )
    assert (
        len(members) == run_json["summary"][collected_or_failures]
    ), f"There should be {artifact_name}.log for each {collected_or_failed} test"
    for member in members:
        test_uuid = Path(member.name).parent.stem
        with subtests.test(name=member.name):
            log = archive.extractfile(member)
            assert log.read() == bytes(f"{artifact_name}_{test_uuid}", "utf8")  # type: ignore


PYTEST_COLLECT_ARGS = [
    pytest.param(
        ["--collect-only", "example_test_to_report_to_ibutsu.py"],
        id="no-xdist-collect-only",
    ),
    pytest.param(
        ["--collect-only", "example_test_to_report_to_ibutsu.py", "-n", "2"],
        id="xdist-collect-only",
    ),
    pytest.param(["-k", "test_that_doesnt_exist"], id="no-xdist-nothing-collected"),
    pytest.param(["-k", "test_that_doesnt_exist", "-n", "2"], id="xdist-nothing-collected"),
]


@pytest.fixture(params=PYTEST_COLLECT_ARGS)
def pytest_collect_test(
    run_id: str, pytester: pytest.Pytester, request: pytest.FixtureRequest
) -> pytest.RunResult:
    args = [
        "--ibutsu=archive",
        "--ibutsu-project=test_project",
        f"--ibutsu-run-id={run_id}",
    ] + request.param
    return run_pytest(pytester, args)


def test_collect(pytester: pytest.Pytester, pytest_collect_test: pytest.RunResult):
    pytest_collect_test.stdout.no_re_match_line("INTERNALERROR")
    archives = 0
    for path in pytester.path.glob("*"):
        archives += 1 if re.match(ARCHIVE_REGEX, path.name) else 0
    assert archives == 0, f"No archives should be created, got {archives}"
