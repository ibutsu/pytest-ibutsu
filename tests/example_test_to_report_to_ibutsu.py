import pytest

pytest_plugins = "pytester"


def test_help_message(pytester):
    result = pytester.runpytest("--help")
    # fnmatch_lines does an assertion internally
    result.stdout.fnmatch_lines(["ibutsu:", "*--ibutsu=URL*URL for the Ibutsu server"])


@pytest.mark.skip("Skipped because I'm not a fan of this test.")
def test_mark_skip():
    pass


@pytest.mark.skipif(True, reason="Skipping due to True equaling True")
def test_mark_skipif():
    pass


@pytest.mark.some_marker
def test_skip():
    pytest.skip("I really don't like this test, but I had to think about it first.")


@pytest.mark.some_marker
def test_pass():
    pass


def test_fail():
    pytest.fail("I don't like tests that pass")


@pytest.mark.some_marker
def test_exception():
    raise Exception("Boom!")
