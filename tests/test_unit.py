from pytest_ibutsu.modeling import Summary
from pytest_ibutsu.modeling import TestResult
from pytest_ibutsu.modeling import TestRun


def test_summary_increment():
    summary = Summary()
    results = ["passed", "failed", "xfailed", "xpassed", "error"]
    test_results = [TestResult(test_id="test", result=result) for result in results]
    for test_result in test_results:
        summary.increment(test_result)
    assert summary.tests == 5
    assert summary.failures == 1
    assert summary.xfailures == 1
    assert summary.xpasses == 1
    assert summary.errors == 1


def test_run_env_vars(monkeypatch):
    run = TestRun()
    assert "jenkins" not in run.metadata
    assert "env_id" not in run.metadata
    monkeypatch.setenv("JOB_NAME", "test_job")
    monkeypatch.setenv("BUILD_NUMBER", "123")
    monkeypatch.setenv("BUILD_URL", "http://example.org")
    monkeypatch.setenv("IBUTSU_ENV_ID", "some_env_id")
    run = TestRun()
    assert run.metadata["jenkins"] == {
        "job_name": "test_job",
        "build_number": "123",
        "build_url": "http://example.org",
    }
    assert run.metadata["env_id"] == "some_env_id"


def test_run_to_dict(subtests):
    run = TestRun()
    assert hasattr(run, "_start_unix_time")
    dict_run = run.to_dict()
    for key in dict_run:
        with subtests.test(msg="private field", key=key):
            assert not key.startswith("_"), "dictionary must not contain private attributes"
