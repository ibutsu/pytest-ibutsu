import pytest

from pytest_ibutsu.pytest_ibutsu import get_server


def test_run_archive(pytester: pytest.Pytester):
    pytester.copy_example("tests/example_test_to_report_to_ibutsu.py")
    pytester.runpytest("--ibutsu-archive=archive.tgz", "example_test_to_report_to_ibutsu.py")
    assert pytester.path.joinpath("archive.tgz").is_file()


def test_run_given_url(pytester: pytest.Pytester, pytestconfig: pytest.Config):
    pytester.copy_example("tests/example_test_to_report_to_ibutsu.py")
    url = pytestconfig.getoption("--test-ibutsu")
    token = pytestconfig.getoption("--test-ibutsu-token")
    project = pytestconfig.getoption("--test-ibutsu-project")
    if not url:
        pytest.skip("url missing")
    result = pytester.runpytest(
        "-ppytester",
        "--include-manual",
        f"--ibutsu={url}",
        f"--ibutsu-token={token}",
        f"--ibutsu-project={project}",
        "example_test_to_report_to_ibutsu.py",
    )
    result.stdout.fnmatch_lines(["*Results can be viewed on: *"])


def test_get_server_given_config(pytestconfig: pytest.Config):
    url = pytestconfig.getoption("--test-ibutsu")
    token = pytestconfig.getoption("--test-ibutsu-token")
    project = pytestconfig.getoption("--test-ibutsu-project")
    if not url:
        pytest.skip("url missing")
    server = get_server(url, token)
    server.frontend

    data = server.add_run(
        {
            "duration": 0,
            "summary": {
                "failures": 0,
                "skips": 0,
                "errors": 0,
                "xfailures": 0,
                "xpasses": 0,
                "tests": 0,
                "collected": 0,
            },
            "metadata": {"project": project},
        }
    )

    run_data = server.refresh_run(data["id"])
    assert data == run_data
    print(data)
