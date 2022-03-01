import pytest

def test_run(pytester: pytest.Pytester):
    pytester.copy_example("tests/example_test_to_report_to_ibutsu.py")
    pytester.runpytest("--ibutsu=archive", "tests/example_test_to_report_to_ibutsu.py")