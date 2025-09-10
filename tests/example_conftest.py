from typing import Iterator

import pytest
from pytest_ibutsu.pytest_plugin import ibutsu_plugin_key
from pytest_ibutsu.pytest_plugin import ibutsu_result_key


class ExampleClassMeta:
    def __str__(self) -> str:
        return "ExampleClassMeta"


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items: list[pytest.Item]):
    """This hook is needed only to test legacy behavior.

    It shouldn't blow up the tests if it's called.
    """
    for item in items:
        item.stash[ibutsu_result_key].metadata.update({"node_id": item.nodeid})


def pytest_collection_finish(session: pytest.Session):
    ibutsu = session.config.stash[ibutsu_plugin_key]
    ibutsu.run.attach_artifact("some_artifact.log", b"some_artifact")


def pytest_exception_interact(node: pytest.Item | pytest.Collector):
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


def pytest_runtest_setup(item: pytest.Item):
    item.stash[ibutsu_result_key].metadata.update({"extra_data": "runtest_setup"})
    # use test_type to demonstrate a class will be serialized as a string with the class name
    item.stash[ibutsu_result_key].metadata.update({"test_type": ExampleClassMeta()})


def pytest_runtest_teardown(item: pytest.Item):
    result = item.stash[ibutsu_result_key]
    result.attach_artifact(
        "runtest_teardown.log",
        f"runtest_teardown_{result.id}".encode("ascii"),
    )


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session: pytest.Session):
    session.config.stash[ibutsu_plugin_key].run.metadata["accessibility"] = True
