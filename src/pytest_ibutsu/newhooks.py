import pytest

from .pytest_plugin import IbutsuPlugin


def pytest_ibutsu_before_shutdown(config: pytest.Config, ibutsu: IbutsuPlugin) -> None:
    """Executed before pytest_ibutsu cleanup"""
