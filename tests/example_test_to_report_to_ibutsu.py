import pytest

pytest_plugins = "pytester"


def test_help_message(pytester: pytest.Pytester) -> None:
    result = pytester.runpytest("--help")
    # fnmatch_lines does an assertion internally
    result.stdout.fnmatch_lines(["ibutsu:", "*--ibutsu=URL*URL for the Ibutsu server"])


@pytest.mark.skip("Skipped because I'm not a fan of this test.")
def test_mark_skip() -> None:
    pass


@pytest.mark.skipif(True, reason="Skipping due to True equaling True")
def test_mark_skipif() -> None:
    pass


@pytest.mark.some_marker
def test_skip() -> None:
    pytest.skip("I really don't like this test, but I had to think about it first.")


@pytest.mark.some_marker
def test_pass() -> None:
    pass


def test_fail() -> None:
    pytest.fail("I don't like tests that pass")


@pytest.mark.some_marker
def test_exception() -> None:
    raise Exception("Boom!")
