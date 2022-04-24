import json
import re
import tarfile
import uuid

import expected_results
import pytest

ARCHIVE_REGEX = re.compile(
    r"^[0-9a-fA-F]{8}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{12}\.tar\.gz$"
)

PYTEST_ARGS = [[], ["-p", "xdist", "-n", "2"]]


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


@pytest.fixture(params=PYTEST_ARGS, ids=["no-xdist", "xdist"])
def result(pytester, request, run_id):
    pytester.copy_example("tests/example_test_to_report_to_ibutsu.py")
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
    assert len(members) == 6
    for _, member in enumerate(members):
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
