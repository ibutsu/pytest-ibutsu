import pytest
from pytest_ibutsu.pytest_plugin import IbutsuPlugin


def test_from_config_without_project(pytester: pytest.Pytester):
    """Test the from_config classmethod raises a UsageError when no project is specified"""
    test_config = pytester.parseconfig("--ibutsu", "http://localhost:8080/api")

    with pytest.raises(pytest.UsageError):
        IbutsuPlugin.from_config(test_config)


def test_from_config_with_project(pytester: pytest.Pytester):
    """Test the from_config classmethod does not raise a UsageError when a project is specified"""
    test_config = pytester.parseconfig("--ibutsu", "archive", "--ibutsu-project", "test-project")
    IbutsuPlugin.from_config(test_config)
