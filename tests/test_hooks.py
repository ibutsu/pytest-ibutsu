"""Tests for pytest-ibutsu report header and terminal summary hooks."""

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from pytest_ibutsu.pytest_plugin import (
    pytest_report_header,
    pytest_terminal_summary,
    ibutsu_plugin_key,
)

pytest_plugins = "pytester"

CURRENT_DIR = Path(__file__).parent
EXAMPLE_TEST_FILE = "example_test_to_report_to_ibutsu.py"


@pytest.fixture
def isolate_ibutsu_env_vars(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Fixture to isolate all ibutsu environment variables during tests.
    """
    # List of all environment variables that ibutsu uses
    ibutsu_env_vars = [
        "IBUTSU_MODE",
        "IBUTSU_TOKEN",
        "IBUTSU_SOURCE",
        "IBUTSU_PROJECT",
        "IBUTSU_RUN_ID",
        "IBUTSU_NO_ARCHIVE",
        "IBUTSU_DATA",
        "IBUTSU_CA_BUNDLE",
        "IBUTSU_ENV_ID",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
        "AWS_BUCKET",
        "REQUESTS_CA_BUNDLE",
    ]

    # Store original values and clear environment variables
    for var in ibutsu_env_vars:
        if var in os.environ:
            monkeypatch.delenv(var, raising=False)

    # Set clean defaults for variables that should have default values
    monkeypatch.setenv("IBUTSU_PROJECT", "")  # Explicitly empty for most tests

    yield


def run_pytest_capture_output(
    pytester: pytest.Pytester, args: list[str]
) -> pytest.RunResult:
    """Helper to run pytest and capture output."""
    # Create a simple test file that will actually run
    pytester.makepyfile("""
def test_simple():
    assert True
""")
    pytester.makeconftest((CURRENT_DIR / "example_conftest.py").read_text())
    return pytester.runpytest(*args)


class TestReportHeader:
    """Tests for pytest_report_header hook."""

    @pytest.mark.parametrize(
        "mock_stash_config_options",
        [{"plugin": None, "raise_keyerror": True}],
        indirect=True,
    )
    def test_report_header_disabled_plugin(
        self, isolate_ibutsu_env_vars, mock_stash_config
    ):
        """Test that no header is shown when plugin is disabled."""
        config = mock_stash_config

        result = pytest_report_header(config)
        assert result == []

    @pytest.mark.parametrize(
        "mock_stash_config_options",
        [{"plugin": Mock(enabled=False), "raise_keyerror": False}],
        indirect=True,
    )
    def test_report_header_no_mode(self, isolate_ibutsu_env_vars, mock_stash_config):
        """Test that no header is shown when no mode is set."""
        config = mock_stash_config

        result = pytest_report_header(config)
        assert result == []

    @pytest.mark.parametrize(
        "mock_terminal_summary_plugin_config",
        [
            {
                "enabled": True,
                "is_archive_mode": True,
                "is_server_mode": False,
                "is_s3_mode": False,
                "run_id": "test-run-id-123",
            }
        ],
        indirect=True,
    )
    @pytest.mark.parametrize(
        "mock_stash_config_options",
        [{"plugin": None, "raise_keyerror": False}],
        indirect=True,
    )
    def test_report_header_archive_mode(
        self, isolate_ibutsu_env_vars, mock_terminal_summary_plugin, mock_stash_config
    ):
        """Test report header for archive mode."""

        # Update the stash config to use our mock plugin
        mock_stash_config.stash.__getitem__ = (
            lambda self, key: mock_terminal_summary_plugin
            if key == ibutsu_plugin_key
            else None
        )
        mock_stash_config.getoption.return_value = "archive"
        config = mock_stash_config

        result = pytest_report_header(config)

        assert len(result) == 2
        assert "pytest-ibutsu: archive mode (local archive creation)" in result[0]
        assert "run ID: test-run-id-123" in result[1]

    @pytest.mark.parametrize(
        "mock_terminal_summary_plugin_config",
        [
            {
                "enabled": True,
                "is_archive_mode": False,
                "is_server_mode": False,
                "is_s3_mode": True,
                "run_id": "test-run-id-456",
            }
        ],
        indirect=True,
    )
    @pytest.mark.parametrize(
        "mock_stash_config_options",
        [{"plugin": None, "raise_keyerror": False}],
        indirect=True,
    )
    def test_report_header_s3_mode(
        self,
        isolate_ibutsu_env_vars,
        monkeypatch,
        mock_terminal_summary_plugin,
        mock_stash_config,
    ):
        """Test report header for S3 mode."""
        monkeypatch.setenv("AWS_BUCKET", "test-bucket")

        # Update the stash config to use our mock plugin
        mock_stash_config.stash.__getitem__ = (
            lambda self, key: mock_terminal_summary_plugin
            if key == ibutsu_plugin_key
            else None
        )
        mock_stash_config.getoption.return_value = "s3"
        config = mock_stash_config

        result = pytest_report_header(config)

        assert len(result) == 2
        assert (
            "pytest-ibutsu: S3 mode (archive creation + S3 upload) (bucket: test-bucket)"
            in result[0]
        )
        assert "run ID: test-run-id-456" in result[1]

    @pytest.mark.parametrize(
        "mock_terminal_summary_plugin_config",
        [
            {
                "enabled": True,
                "is_archive_mode": False,
                "is_server_mode": True,
                "is_s3_mode": False,
                "run_id": "test-run-id-789",
                "summary_info_overrides": {},
            }
        ],
        indirect=True,
    )
    @pytest.mark.parametrize(
        "mock_stash_config_options",
        [{"plugin": None, "raise_keyerror": False}],
        indirect=True,
    )
    def test_report_header_server_mode(
        self, isolate_ibutsu_env_vars, mock_terminal_summary_plugin, mock_stash_config
    ):
        """Test report header for server mode."""
        # Add additional attributes to the mock plugin
        mock_terminal_summary_plugin.ibutsu_mode = "https://ibutsu.example.com/api"
        mock_terminal_summary_plugin.ibutsu_project = "test-project"
        mock_terminal_summary_plugin.ibutsu_source = "jenkins"
        mock_terminal_summary_plugin.ibutsu_no_archive = False

        # Update the stash config to use our mock plugin
        mock_stash_config.stash.__getitem__ = (
            lambda self, key: mock_terminal_summary_plugin
            if key == ibutsu_plugin_key
            else None
        )
        mock_stash_config.getoption.return_value = "https://ibutsu.example.com/api"
        config = mock_stash_config

        result = pytest_report_header(config)

        assert len(result) == 2
        assert (
            "pytest-ibutsu: server mode (API: https://ibutsu.example.com/api)"
            in result[0]
        )
        assert "project: test-project" in result[0]
        assert "source: jenkins" in result[0]
        assert "archiving: enabled" in result[0]
        assert "run ID: test-run-id-789" in result[1]

    @pytest.mark.parametrize(
        "mock_terminal_summary_plugin_config",
        [
            {
                "enabled": True,
                "is_archive_mode": False,
                "is_server_mode": True,
                "is_s3_mode": False,
                "run_id": "test-run-id-999",
                "summary_info_overrides": {},
            }
        ],
        indirect=True,
    )
    @pytest.mark.parametrize(
        "mock_stash_config_options",
        [{"plugin": None, "raise_keyerror": False}],
        indirect=True,
    )
    def test_report_header_server_mode_no_archive(
        self, isolate_ibutsu_env_vars, mock_terminal_summary_plugin, mock_stash_config
    ):
        """Test report header for server mode with archiving disabled."""
        # Add additional attributes to the mock plugin
        mock_terminal_summary_plugin.ibutsu_mode = "https://ibutsu.example.com/api"
        mock_terminal_summary_plugin.ibutsu_project = "test-project"
        mock_terminal_summary_plugin.ibutsu_source = "local"
        mock_terminal_summary_plugin.ibutsu_no_archive = True

        # Update the stash config to use our mock plugin
        mock_stash_config.stash.__getitem__ = (
            lambda self, key: mock_terminal_summary_plugin
            if key == ibutsu_plugin_key
            else None
        )
        mock_stash_config.getoption.return_value = "https://ibutsu.example.com/api"
        config = mock_stash_config

        result = pytest_report_header(config)

        assert len(result) == 2
        assert (
            "pytest-ibutsu: server mode (API: https://ibutsu.example.com/api)"
            in result[0]
        )
        assert "project: test-project" in result[0]
        assert "archiving: disabled" in result[0]
        # local source should not be included
        assert "source:" not in result[0]
        assert "run ID: test-run-id-999" in result[1]


class TestTerminalSummary:
    """Tests for pytest_terminal_summary hook."""

    def test_terminal_summary_plugin_disabled(self, isolate_ibutsu_env_vars):
        """Test terminal summary when plugin is disabled."""
        config = Mock()

        # Mock stash to raise KeyError for ibutsu_plugin_key
        def mock_stash_getitem(self, key):
            if key == ibutsu_plugin_key:
                raise KeyError()
            return None

        config.stash.__getitem__ = mock_stash_getitem

        terminalreporter = Mock()

        # Should not raise an exception and not write anything
        pytest_terminal_summary(terminalreporter, 0, config)

        terminalreporter.write_sep.assert_not_called()
        terminalreporter.write_line.assert_not_called()

    @pytest.mark.parametrize(
        "mock_ibutsu_plugin_config", [{"enabled": False}], indirect=True
    )
    @pytest.mark.parametrize(
        "mock_stash_config_options",
        [{"plugin": None, "raise_keyerror": False}],
        indirect=True,
    )
    def test_terminal_summary_plugin_not_enabled(
        self, isolate_ibutsu_env_vars, mock_ibutsu_plugin, mock_stash_config
    ):
        """Test terminal summary when plugin exists but is not enabled."""
        # Update the stash config to use our mock plugin
        mock_stash_config.stash.__getitem__ = (
            lambda self, key: mock_ibutsu_plugin if key == ibutsu_plugin_key else None
        )
        config = mock_stash_config

        terminalreporter = Mock()

        pytest_terminal_summary(terminalreporter, 0, config)

        terminalreporter.write_sep.assert_not_called()
        terminalreporter.write_line.assert_not_called()

    @pytest.mark.parametrize(
        "mock_stash_config_options",
        [{"plugin": None, "raise_keyerror": False}],
        indirect=True,
    )
    def test_terminal_summary_no_operations(
        self, isolate_ibutsu_env_vars, mock_ibutsu_plugin, mock_stash_config
    ):
        """Test terminal summary when no operations were performed."""
        # Update the stash config to use our mock plugin
        mock_stash_config.stash.__getitem__ = (
            lambda self, key: mock_ibutsu_plugin if key == ibutsu_plugin_key else None
        )
        config = mock_stash_config

        terminalreporter = Mock()

        pytest_terminal_summary(terminalreporter, 0, config)

        # Should not write any summary when no operations occurred
        terminalreporter.write_sep.assert_not_called()
        terminalreporter.write_line.assert_not_called()

    @pytest.mark.parametrize(
        "mock_terminal_summary_plugin_config",
        [
            {
                "enabled": True,
                "is_s3_mode": False,
                "is_server_mode": False,
                "summary_info_overrides": {
                    "archive_created": True,
                    "archive_path": "test-run-123.tar.gz",
                },
            }
        ],
        indirect=True,
    )
    @pytest.mark.parametrize(
        "mock_stash_config_options",
        [{"plugin": None, "raise_keyerror": False}],
        indirect=True,
    )
    def test_terminal_summary_archive_created(
        self, isolate_ibutsu_env_vars, mock_terminal_summary_plugin, mock_stash_config
    ):
        """Test terminal summary when archive was created."""
        # Update the stash config to use our mock plugin
        mock_stash_config.stash.__getitem__ = (
            lambda self, key: mock_terminal_summary_plugin
            if key == ibutsu_plugin_key
            else None
        )
        config = mock_stash_config

        terminalreporter = Mock()

        pytest_terminal_summary(terminalreporter, 0, config)

        terminalreporter.write_sep.assert_called_once_with(
            "=", "pytest-ibutsu summary", bold=True
        )
        terminalreporter.write_line.assert_any_call(
            "✓ Archive created: test-run-123.tar.gz"
        )

    def test_terminal_summary_s3_success(self, isolate_ibutsu_env_vars):
        """Test terminal summary for successful S3 upload."""
        mock_plugin = Mock()
        mock_plugin.enabled = True
        mock_plugin.is_s3_mode = True
        mock_plugin.is_server_mode = False
        mock_plugin.summary_info = {
            "archive_created": True,
            "archive_path": "test-run-456.tar.gz",
            "s3_uploaded": True,
            "s3_upload_count": 2,
            "s3_upload_errors": 0,
            "s3_bucket": "my-test-bucket",
            "server_uploaded": False,
            "server_url": None,
            "frontend_url": None,
            "errors": [],
        }

        config = Mock()

        def mock_stash_getitem(self, key):
            if key == ibutsu_plugin_key:
                return mock_plugin
            raise KeyError()

        config.stash.__getitem__ = mock_stash_getitem

        terminalreporter = Mock()

        pytest_terminal_summary(terminalreporter, 0, config)

        terminalreporter.write_sep.assert_called_once_with(
            "=", "pytest-ibutsu summary", bold=True
        )
        terminalreporter.write_line.assert_any_call(
            "✓ Archive created: test-run-456.tar.gz"
        )
        terminalreporter.write_line.assert_any_call(
            "✓ S3 upload: 2 file(s) uploaded to my-test-bucket"
        )

    def test_terminal_summary_s3_failure(self, isolate_ibutsu_env_vars):
        """Test terminal summary for failed S3 upload."""
        mock_plugin = Mock()
        mock_plugin.enabled = True
        mock_plugin.is_s3_mode = True
        mock_plugin.is_server_mode = False
        mock_plugin.summary_info = {
            "archive_created": True,
            "archive_path": "test-run-789.tar.gz",
            "s3_uploaded": False,
            "s3_upload_count": 0,
            "s3_upload_errors": 1,
            "s3_bucket": "my-test-bucket",
            "server_uploaded": False,
            "server_url": None,
            "frontend_url": None,
            "errors": [],
        }

        config = Mock()

        def mock_stash_getitem(self, key):
            if key == ibutsu_plugin_key:
                return mock_plugin
            raise KeyError()

        config.stash.__getitem__ = mock_stash_getitem

        terminalreporter = Mock()

        pytest_terminal_summary(terminalreporter, 0, config)

        terminalreporter.write_sep.assert_called_once_with(
            "=", "pytest-ibutsu summary", bold=True
        )
        terminalreporter.write_line.assert_any_call(
            "✓ Archive created: test-run-789.tar.gz"
        )
        terminalreporter.write_line.assert_any_call(
            "✗ S3 upload failed: 1 error(s) uploading to my-test-bucket"
        )

    def test_terminal_summary_server_success(self, isolate_ibutsu_env_vars):
        """Test terminal summary for successful server upload."""
        mock_plugin = Mock()
        mock_plugin.enabled = True
        mock_plugin.is_s3_mode = False
        mock_plugin.is_server_mode = True
        mock_plugin.summary_info = {
            "archive_created": True,
            "archive_path": "test-run-abc.tar.gz",
            "s3_uploaded": False,
            "s3_upload_count": 0,
            "s3_upload_errors": 0,
            "s3_bucket": None,
            "server_uploaded": True,
            "server_url": "https://ibutsu.example.com/api",
            "frontend_url": "https://ibutsu.example.com",
            "errors": [],
        }
        mock_plugin.run = Mock()
        mock_plugin.run.id = "test-run-abc"

        config = Mock()

        def mock_stash_getitem(self, key):
            if key == ibutsu_plugin_key:
                return mock_plugin
            raise KeyError()

        config.stash.__getitem__ = mock_stash_getitem

        terminalreporter = Mock()

        pytest_terminal_summary(terminalreporter, 0, config)

        terminalreporter.write_sep.assert_called_once_with(
            "=", "pytest-ibutsu summary", bold=True
        )
        terminalreporter.write_line.assert_any_call(
            "✓ Archive created: test-run-abc.tar.gz"
        )
        terminalreporter.write_line.assert_any_call(
            "✓ Results uploaded to: https://ibutsu.example.com/runs/test-run-abc"
        )

    def test_terminal_summary_with_errors(self, isolate_ibutsu_env_vars):
        """Test terminal summary when errors occurred."""
        mock_plugin = Mock()
        mock_plugin.enabled = True
        mock_plugin.is_s3_mode = False
        mock_plugin.is_server_mode = False
        mock_plugin.summary_info = {
            "archive_created": True,
            "archive_path": "test-run-error.tar.gz",
            "s3_uploaded": False,
            "s3_upload_count": 0,
            "s3_upload_errors": 0,
            "s3_bucket": None,
            "server_uploaded": False,
            "server_url": None,
            "frontend_url": None,
            "errors": ["Failed to connect to server", "Authentication error"],
        }

        config = Mock()

        def mock_stash_getitem(self, key):
            if key == ibutsu_plugin_key:
                return mock_plugin
            raise KeyError()

        config.stash.__getitem__ = mock_stash_getitem

        terminalreporter = Mock()

        pytest_terminal_summary(terminalreporter, 0, config)

        terminalreporter.write_sep.assert_called_once_with(
            "=", "pytest-ibutsu summary", bold=True
        )
        terminalreporter.write_line.assert_any_call(
            "✓ Archive created: test-run-error.tar.gz"
        )
        terminalreporter.write_line.assert_any_call("Errors encountered:")
        terminalreporter.write_line.assert_any_call("  - Failed to connect to server")
        terminalreporter.write_line.assert_any_call("  - Authentication error")


class TestIntegrationWithPytester:
    """Integration tests using pytester to verify hooks work end-to-end."""

    def test_report_header_archive_mode_integration(
        self, isolate_ibutsu_env_vars, pytester
    ):
        """Test that report header shows correctly in archive mode integration."""
        result = run_pytest_capture_output(
            pytester, ["--ibutsu", "archive", "--ibutsu-project", "test-project", "-v"]
        )

        # Check that the report header appears in the output
        assert (
            "pytest-ibutsu: archive mode (local archive creation)"
            in result.stdout.str()
        )
        assert "run ID:" in result.stdout.str()

    def test_terminal_summary_archive_mode_integration(
        self, isolate_ibutsu_env_vars, pytester
    ):
        """Test that terminal summary shows correctly in archive mode integration."""
        result = run_pytest_capture_output(
            pytester, ["--ibutsu", "archive", "--ibutsu-project", "test-project", "-v"]
        )

        # Check that the terminal summary appears in the output
        assert "pytest-ibutsu summary" in result.stdout.str()
        assert "✓ Archive created:" in result.stdout.str()

    def test_s3_mode_integration_with_env(
        self, isolate_ibutsu_env_vars, pytester, monkeypatch
    ):
        """Test S3 mode integration with environment variables."""
        monkeypatch.setenv("AWS_BUCKET", "test-integration-bucket")

        result = run_pytest_capture_output(
            pytester, ["--ibutsu", "s3", "--ibutsu-project", "test-project", "-v"]
        )

        # Check header
        assert (
            "pytest-ibutsu: S3 mode (archive creation + S3 upload) (bucket: test-integration-bucket)"
            in result.stdout.str()
        )

        # Check summary (even if S3 upload fails, archive should be created)
        assert "pytest-ibutsu summary" in result.stdout.str()
        assert "✓ Archive created:" in result.stdout.str()
