import json
import re
import tarfile
import uuid
from pathlib import Path

import expected_results
import pytest

ARCHIVE_REGEX = re.compile(
    r"^[0-9a-fA-F]{8}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{12}\.tar\.gz$"
)

PYTEST_ARGS = [
    pytest.param([], id="no-xdist"),
    pytest.param(["-p", "xdist", "-n", "2"], id="xdist"),
]

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


@pytest.fixture(params=PYTEST_ARGS)
def result(pytester, request, run_id):
    pytester.copy_example(CURRENT_DIR / "example_test_to_report_to_ibutsu.py")
    pytester.makeconftest((CURRENT_DIR / "example_conftest.py").read_text())
    args = request.param + [
        "--ibutsu=archive",
        "--ibutsu-project=test_project",
        f"--ibutsu-run-id={run_id}",
        "example_test_to_report_to_ibutsu.py",
    ]
    return pytester.runpytest(*args)


def test_archive_file(pytester, result, run_id):
    result.stdout.re_match_lines([f".*Saved results archive to {run_id}.tar.gz$"])
    archive_name = f"{run_id}.tar.gz"
    archive = pytester.path.joinpath(archive_name)
    assert archive.is_file()
    assert archive.lstat().st_size > 0


@pytest.mark.usefixtures("result")
def test_archives_count(pytester):
    archives = 0
    for path in pytester.path.glob("*"):
        archives += 1 if re.match(ARCHIVE_REGEX, path.name) else 0
    assert archives == 1, f"Expected exactly one archive file, got {archives}"


@pytest.fixture
def archive(result, pytester, run_id):
    archive_name = f"{run_id}.tar.gz"
    archive_path = pytester.path.joinpath(archive_name)
    with tarfile.open(archive_path, "r:gz") as tar:
        yield tar


def test_archive_content_run(archive, run_id):
    members = archive.getmembers()
    assert members[0].isdir(), "root dir is missing"
    assert members[1].isfile(), "run.json is missing"
    assert members[1].name == "run.json"
    o = archive.extractfile(members[1])
    loaded = json.load(o)
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


def test_archive_content_results(archive, subtests, run_id):
    members = [m for m in archive.getmembers() if m.isfile() and "result.json" in m.name]
    assert len(members) == 7
    for member in members:
        with subtests.test(name=member.name):
            o = archive.extractfile(member)
            result = json.load(o)
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
def test_archive_artifacts(archive, subtests, artifact_name):
    run_json_tar_info = archive.extractfile(archive.getmembers()[1])
    run_json = json.load(run_json_tar_info)
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
            log.read() == bytes(f"{artifact_name}_{test_uuid}", "utf8")
    members = [m for m in archive.getmembers() if m.isfile() and f"{artifact_name}.log" in m.name]
