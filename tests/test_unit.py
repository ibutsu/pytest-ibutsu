import pytest

from pytest_ibutsu.modeling import Summary
from pytest_ibutsu.modeling import TestResult as TResult
from pytest_ibutsu.modeling import TestRun as TRun


def test_summary_increment():
    summary = Summary()
    results = ["passed", "failed", "xfailed", "xpassed", "error"]
    test_results = [TResult(test_id="test", result=result) for result in results]
    for test_result in test_results:
        summary.increment(test_result)
    assert summary.tests == 5
    assert summary.failures == 1
    assert summary.xfailures == 1
    assert summary.xpasses == 1
    assert summary.errors == 1


def test_run_env_vars(monkeypatch: pytest.MonkeyPatch):
    run = TRun()
    assert "jenkins" not in run.metadata
    assert "env_id" not in run.metadata
    monkeypatch.setenv("JOB_NAME", "test_job")
    monkeypatch.setenv("BUILD_NUMBER", "123")
    monkeypatch.setenv("BUILD_URL", "http://example.org")
    monkeypatch.setenv("IBUTSU_ENV_ID", "some_env_id")
    run = TRun()
    assert run.metadata["jenkins"] == {
        "job_name": "test_job",
        "build_number": "123",
        "build_url": "http://example.org",
    }
    assert run.metadata["env_id"] == "some_env_id"


def test_run_to_dict():
    run = TRun()
    assert hasattr(run, "_start_unix_time")
    dict_run = run.to_dict()

    # Test that no keys start with underscore (private attributes)
    private_keys = [key for key in dict_run if key.startswith("_")]
    assert not private_keys, (
        f"Dictionary must not contain private attributes, but found: {private_keys}"
    )


def test_run_id_in_xdist_results():
    tr_1 = TRun(results=[TResult("test_1"), TResult("test_2"), TResult("test_3")])
    tr_2 = TRun(results=[TResult("test_4"), TResult("test_5"), TResult("test_6")])
    tr_3 = TRun(results=[TResult("test_7"), TResult("test_8"), TResult("test_9")])
    tr = TRun.from_xdist_test_runs([tr_1, tr_2, tr_3])
    for result in tr._results:
        assert result.run_id == tr_1.id
        assert result.metadata["run"] == tr_1.id


def test_parse_data_option():
    from pytest_ibutsu.pytest_plugin import IbutsuPlugin

    res = IbutsuPlugin._parse_data_option(["a.b.c=1"])
    assert res == {"a": {"b": {"c": "1"}}}
