from .pytest_plugin import IbutsuPlugin


def pytest_ibutsu_before_shutdown(config, ibutsu: IbutsuPlugin):
    """Executed before pytest_ibutsu cleanup"""
