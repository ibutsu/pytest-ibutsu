from typing import Iterator

import pytest
from pytest_ibutsu.pytest_plugin import ibutsu_plugin_key
from pytest_ibutsu.pytest_plugin import ibutsu_result_key


class TestType:
    def __str__(self) -> str:
        return "TestType"


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """This hook is needed only to test legacy behavior.

    It shouldn't blow up the tests if it's called.
    """
    for item in items:
        item.stash[ibutsu_result_key].metadata.update({"node_id": item.nodeid})


def pytest_collection_finish(session: pytest.Session) -> None:
    ibutsu = session.config.stash[ibutsu_plugin_key]
    ibutsu.run.attach_artifact("some_artifact.log", b"some_artifact")


def pytest_exception_interact(node: pytest.Item | pytest.Collector) -> None:
    result = node.stash[ibutsu_result_key]
    result.attach_artifact(
        "legacy_exception.log",
        f"legacy_exception_{result.id}".encode(),
    )

    result.attach_artifact(
        "actual_exception.log", f"actual_exception_{result.id}".encode()
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item) -> Iterator[None]:
    yield
    result = item.stash[ibutsu_result_key]
    result.attach_artifact(
        "runtest.log",
        f"runtest_{result.id}".encode(),
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    item.stash[ibutsu_result_key].metadata.update({"extra_data": "runtest_setup"})
    item.stash[ibutsu_result_key].metadata.update({"test_type": TestType()})


def pytest_runtest_teardown(item: pytest.Item) -> None:
    result = item.stash[ibutsu_result_key]
    result.attach_artifact(
        "runtest_teardown.log",
        f"runtest_teardown_{result.id}".encode("ascii"),
    )


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    session.config.stash[ibutsu_plugin_key].run.metadata["accessibility"] = True
