from datetime import datetime

import pytest
from jose import jwt
from pytest_ibutsu.pytest_plugin import ExpiredTokenError
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


def test_expired_token(pytester: pytest.Pytester):
    """Test the ExpiredTokenError is raised when expired token is passed"""
    token = jwt.encode(
        {"exp": datetime.utcnow().timestamp() - 1_000_000}, "secret", algorithm="HS256"
    )
    test_config = pytester.parseconfig(
        "--ibutsu", "archive", "--ibutsu-project", "test-project", "--ibutsu-token", token
    )
    with pytest.raises(ExpiredTokenError):
        IbutsuPlugin.from_config(test_config)


def test_valid_token(pytester: pytest.Pytester):
    """Test the ExpiredTokenError is NOT raised when valid token is passed"""
    token = jwt.encode(
        {"exp": datetime.utcnow().timestamp() + 1_000_000}, "secret", algorithm="HS256"
    )
    test_config = pytester.parseconfig(
        "--ibutsu", "archive", "--ibutsu-project", "test-project", "--ibutsu-token", token
    )
    IbutsuPlugin.from_config(test_config)
