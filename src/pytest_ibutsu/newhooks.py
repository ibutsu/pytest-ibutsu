import pytest

from .pytest_ibutsu import IbutsuPlugin


def pytest_ibutsu_before_shutdown(config: pytest.Config, ibutsu: IbutsuPlugin):
    """Executed before pytest_ibutsu cleanup"""
