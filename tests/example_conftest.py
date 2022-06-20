import pytest
from pytest_ibutsu.pytest_plugin import ibutsu_plugin_key
from pytest_ibutsu.pytest_plugin import ibutsu_result_key


class TestType:
    def __str__(self):
        return "TestType"


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, items, config):
    """This hook is needed only to test legacy behavior.

    It shouldn't blow up the tests if it's called.
    """
    for item in items:
        item._ibutsu["data"]["metadata"].update({"node_id": item.nodeid})


def pytest_collection_finish(session):
    ibutsu = session.config.stash[ibutsu_plugin_key]
    ibutsu.run.attach_artifact("some_artifact.log", bytes("some_artifact", "utf8"))


def pytest_exception_interact(node, call, report):
    node.config._ibutsu.upload_artifact_from_file(
        node._ibutsu["id"],
        "legacy_exception.log",
        bytes(f"legacy_exception_{node._ibutsu['id']}", "utf8"),
    )
    test_result = node.config.stash[ibutsu_plugin_key].results[node.nodeid]
    test_result.attach_artifact(
        "actual_exception.log", bytes(f"actual_exception_{test_result.id}", "utf8")
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item):
    yield
    item.config._ibutsu.upload_artifact_from_file(
        item._ibutsu["id"],
        "runtest.log",
        bytes(f"runtest_{item.stash[ibutsu_result_key].id}", "utf8"),
    )


def pytest_runtest_setup(item):
    item._ibutsu["data"]["metadata"].update({"extra_data": "runtest_setup"})
    item.stash[ibutsu_result_key].metadata.update({"test_type": TestType()})


def pytest_runtest_teardown(item):
    item.config._ibutsu.upload_artifact_raw(
        item._ibutsu["id"],
        "runtest_teardown.log",
        bytes(f"runtest_teardown_{item.stash[ibutsu_result_key].id}", "utf-8"),
    )


@pytest.mark.tryfirst
def pytest_sessionfinish(session):
    session.config._ibutsu.run["metadata"]["accessibility"] = True
