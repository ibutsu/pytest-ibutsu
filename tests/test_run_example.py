import pytest


def test_run_archive(pytester: pytest.Pytester):
    pytester.copy_example("tests/example_test_to_report_to_ibutsu.py")
    pytester.runpytest(
        "-ppytester", "--ibutsu-archive=archive.tgz", "example_test_to_report_to_ibutsu.py"
    )
    assert pytester.path.joinpath("archive.tgz").is_file()


def test_run_given_url(pytester, pytestconfig):
    pytester.copy_example("tests/example_test_to_report_to_ibutsu.py")
    url = pytestconfig.getoption("--test-ibutsu")
    token = pytestconfig.getoption("--test-ibutsu-token")
    if not url:
        pytest.skip("url missing")
    result = pytester.runpytest(
        "-ppytester",
        f"--ibutsu={url}",
        f"--ibutsu-token={token}",
        "example_test_to_report_to_ibutsu.py",
    )
    result.fnmatch_lines()
    assert 0
