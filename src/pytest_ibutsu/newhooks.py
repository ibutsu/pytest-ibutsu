import pytest


def pytest_ibutsu_before_shutdown(config, ibutsu):
    """Executed before pytest_ibutsu cleanup"""


def pytest_ibutsu_get_result_metadata(item):
    """Executed for each item in pytest_collection_modifyitems"""


def pytest_ibutsu_get_run_metadata(session):
    """Executed for the session in pytest_sessionfinish"""


def pytest_ibutsu_add_artifact(item_or_node, name, path):
    """Add an artifact to Ibutsu"""


@pytest.hookspec(firstresult=True)
def pytest_ibutsu_is_enabled():
    """Is Ibutsu enabled"""
