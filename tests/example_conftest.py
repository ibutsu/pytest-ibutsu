import pytest


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, items, config):
    """This hook is needed only to test legacy behavior.

    It shouldn't blow up the tests if it's called.
    """
    for item in items:
        item._ibutsu["data"]["metadata"].update({"node_id": item.nodeid})


def pytest_exception_interact(node, call, report):
    node.config._ibutsu.upload_artifact_from_file(
        node._ibutsu["id"], "legacy_exception.log", bytes(f"legacy_exception_{node.nodeid}", "utf8")
    )
    test_result = node.config.ibutsu_plugin.results[node.nodeid]
    test_result.attach_artifact(
        "actual_exception.log", bytes(f"actual_exception_{test_result.id}", "utf8")
    )


@pytest.mark.hookwrapper
def pytest_runtest_protocol(item):
    yield
    item.config._ibutsu.upload_artifact_from_file(
        item._ibutsu["id"],
        "runtest.log",
        bytes(f"runtest_{item.ibutsu_result.id}", "utf8"),
    )


def pytest_runtest_setup(item):
    """This hook is needed only to test legacy behavior.

    It shouldn't blow up the tests if it's called.
    """
    item._ibutsu["data"]["metadata"].update({"extra_data": "runtest_setup"})


def pytest_runtest_teardown(item):
    item.config._ibutsu.upload_artifact_raw(
        item._ibutsu["id"],
        "runtest_teardown.log",
        bytes(f"runtest_teardown_{item.ibutsu_result.id}", "utf-8"),
    )


@pytest.mark.tryfirst
def pytest_sessionfinish(session):
    session.config._ibutsu.run["metadata"]["accessibility"] = True
