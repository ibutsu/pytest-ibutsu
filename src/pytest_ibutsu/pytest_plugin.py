from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import re
import tarfile
import uuid
from base64 import urlsafe_b64decode
from datetime import datetime, UTC
from datetime import timezone
from pathlib import Path
from typing import Generator, Any, TYPE_CHECKING
from typing import Iterator

if TYPE_CHECKING:
    from _pytest.terminal import TerminalReporter

import pytest

from .archiver import dump_to_archive
from .modeling import IbutsuTestResult, IbutsuTestRun
from .sender import send_data_to_ibutsu
from .s3_uploader import upload_to_s3

if TYPE_CHECKING:
    import xdist.workermanage

UUID_REGEX = re.compile(
    r"[0-9a-fA-F]{8}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{12}"
)

logger = logging.getLogger(__name__)


class ExpiredTokenError(Exception):
    pass


class UUIDAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: Any,
        value: Any,
        option_string: str | None = None,
    ) -> None:
        if not re.match(UUID_REGEX, value):
            raise ValueError("Not a uuid")
        setattr(namespace, self.dest, value)


def is_xdist_worker(config: pytest.Config) -> bool:
    """Return `True` if this is an xdist worker, `False` otherwise"""
    return hasattr(config, "workerinput")


def is_xdist_controller(config: pytest.Config) -> bool:
    """Return `True` if this is the xdist controller, `False` otherwise"""
    return (
        not is_xdist_worker(config)
        and hasattr(config.option, "dist")
        and config.option.dist != "no"
    )


class IbutsuPlugin:
    def __init__(
        self,
        enabled: bool,
        ibutsu_mode: str,
        ibutsu_token: str | None,
        ibutsu_source: str,
        ibutsu_project: str,
        ibutsu_no_archive: bool,
        extra_data: dict[str, Any],
        run: IbutsuTestRun,
    ) -> None:
        self.ibutsu_mode = ibutsu_mode
        self.ibutsu_token = ibutsu_token
        self.ibutsu_source = ibutsu_source
        self.ibutsu_project = ibutsu_project
        self.ibutsu_no_archive = ibutsu_no_archive
        self.enabled = enabled
        self.extra_data = extra_data
        self.run = run
        self.workers_runs: list[IbutsuTestRun] = []
        self.workers_enabled: list[bool] = []
        self.results: dict[str, IbutsuTestResult] = {}
        # Summary tracking for terminal output
        self.summary_info: dict[str, Any] = {
            "archive_created": False,
            "archive_path": None,
            "s3_uploaded": False,
            "s3_upload_count": 0,
            "s3_upload_errors": 0,
            "s3_bucket": None,
            "server_uploaded": False,
            "server_url": None,
            "frontend_url": None,
            "errors": [],
        }
        if self.ibutsu_token and self.is_token_expired(self.ibutsu_token):
            raise ExpiredTokenError("Your token has expired.")

    @property
    def is_archive_mode(self) -> bool:
        """Returns True if mode is 'archive'."""
        return self.ibutsu_mode == "archive"

    @property
    def is_s3_mode(self) -> bool:
        """Returns True if mode is 's3'."""
        return self.ibutsu_mode == "s3"

    @property
    def is_server_mode(self) -> bool:
        """Returns True if mode is a server URL (not 'archive' or 's3')."""
        return (
            self.ibutsu_mode not in ("archive", "s3") and self.ibutsu_mode is not None
        )

    @property
    def ibutsu_server(self) -> str:
        """Returns the server URL for backward compatibility."""
        return self.ibutsu_mode if self.is_server_mode else ""

    def is_token_expired(self, token: str) -> bool:
        """Validate a JWT token"""
        payload = token.split(".")[1]
        if len(payload) % 4 != 0:
            payload += "=" * (4 - (len(payload) % 4))
        payload_dict = json.loads(urlsafe_b64decode(payload))
        expires = datetime.fromtimestamp(payload_dict["exp"], tz=timezone.utc)
        return datetime.now(tz=timezone.utc) > expires

    @staticmethod
    def _parse_data_option(data_list: list[str]) -> dict[str, Any]:
        data_dict: dict[str, Any] = {}

        for data_str in data_list:
            if not data_str:
                continue
            if "=" not in data_str:
                raise ValueError(
                    f"Invalid --ibutsu-data format: '{data_str}'. "
                    "Expected format: key=value"
                )
            key_str, value = data_str.split("=", 1)
            (*path, key) = key_str.split(".")
            current_item = data_dict
            for path_key in path:
                if path_key not in current_item:
                    current_item[path_key] = {}
                current_item = current_item[path_key]
            current_item[key] = value
        return data_dict

    @classmethod
    def _get_config_value(
        cls,
        config: pytest.Config,
        option_name: str,
        ini_name: str,
        env_name: str,
        default: Any = None,
    ) -> Any:
        """Get configuration value with consistent precedence: CLI > ENV > INI > default"""
        cli_value = config.getoption(option_name)
        if cli_value is not None:
            return cli_value

        env_value = os.getenv(env_name)
        if env_value is not None:
            return env_value

        ini_value = config.getini(ini_name)
        if ini_value is not None and ini_value != "":
            return ini_value

        return default

    @classmethod
    def _parse_env_data_option(cls, env_data: str | None) -> list[str]:
        """Parse environment variable data option into list format"""
        if not env_data:
            return []
        # Support space-separated key=value pairs
        import shlex

        return shlex.split(env_data)

    @classmethod
    def from_config(cls, config: pytest.Config) -> IbutsuPlugin:
        # Get all configuration values with consistent precedence: CLI > ENV > INI > defaults
        ibutsu_mode = cls._get_config_value(
            config, "ibutsu_mode", "ibutsu_server", "IBUTSU_MODE"
        )
        ibutsu_token = cls._get_config_value(
            config, "ibutsu_token", "ibutsu_token", "IBUTSU_TOKEN"
        )
        ibutsu_source = cls._get_config_value(
            config, "ibutsu_source", "ibutsu_source", "IBUTSU_SOURCE", "local"
        )
        ibutsu_project = cls._get_config_value(
            config, "ibutsu_project", "ibutsu_project", "IBUTSU_PROJECT"
        )
        run_id = cls._get_config_value(
            config, "ibutsu_run_id", "ibutsu_run_id", "IBUTSU_RUN_ID"
        )

        # Handle boolean option separately
        ibutsu_no_archive_cli = config.getoption("ibutsu_no_archive")
        ibutsu_no_archive_env = os.getenv("IBUTSU_NO_ARCHIVE")
        ibutsu_no_archive_ini = config.getini("ibutsu_no_archive")

        if ibutsu_no_archive_cli is not None:
            ibutsu_no_archive = bool(ibutsu_no_archive_cli)
        elif ibutsu_no_archive_env is not None:
            ibutsu_no_archive = ibutsu_no_archive_env.lower() in (
                "true",
                "1",
                "yes",
                "on",
            )
        elif ibutsu_no_archive_ini is not None:
            ibutsu_no_archive = bool(ibutsu_no_archive_ini)
        else:
            ibutsu_no_archive = False

        # Handle ibutsu_data with environment variable support
        cli_data = config.getoption("ibutsu_data") or []
        env_data = cls._parse_env_data_option(os.getenv("IBUTSU_DATA"))
        # CLI data takes precedence over environment data
        data_list = cli_data or env_data
        extra_data = cls._parse_data_option(data_list)

        # Validate that project is required for server mode
        if ibutsu_mode and ibutsu_mode not in ("archive", "s3") and not ibutsu_project:
            raise pytest.UsageError(
                "Ibutsu project is required, use --ibutsu-project, "
                "-o ibutsu_project or the IBUTSU_PROJECT environment variable"
            )

        run = IbutsuTestRun(
            id=run_id,
            source=ibutsu_source,
            metadata={"project": ibutsu_project, **extra_data},
        )
        enabled = False if config.option.collectonly else bool(ibutsu_mode)
        return cls(
            enabled,
            ibutsu_mode,
            ibutsu_token,
            ibutsu_source,
            ibutsu_project,
            ibutsu_no_archive,
            extra_data,
            run,
        )

    def _find_run_artifacts(
        self, archive: tarfile.TarFile
    ) -> Iterator[tuple[str, bytes]]:
        for member in archive.getmembers():
            path = Path(member.path)
            if (
                path.match(f"{self.run.id}/*")
                and path.name != "run.json"
                and member.isfile()
            ):
                yield path.name, archive.extractfile(member).read()  # type: ignore

    def _find_result_artifacts(
        self, archive: tarfile.TarFile, result_id: str
    ) -> Iterator[tuple[str, bytes]]:
        for name in archive.getnames():
            path = Path(name)
            if (
                path.match(f"{self.run.id}/{result_id}/*")
                and path.name != "result.json"
            ):
                yield path.name, archive.extractfile(name).read()  # type: ignore

    def _load_archive(self) -> None:
        """Load data from an ibutsu archive."""
        if not Path(f"{self.run.id}.tar.gz").exists():
            return
        with tarfile.open(f"{self.run.id}.tar.gz", "r:gz") as archive:
            run_json = json.loads(archive.extractfile(f"{self.run.id}/run.json").read())  # type: ignore
            prior_run = IbutsuTestRun.from_json(run_json)
            for name, run_artifact in self._find_run_artifacts(archive):
                prior_run.attach_artifact(name, run_artifact)
            for name in archive.getnames():
                if name.endswith("/result.json"):
                    result_json = json.loads(archive.extractfile(name).read())  # type: ignore
                    prior_result = IbutsuTestResult.from_json(result_json)
                    prior_run._results.append(prior_result)
                    # do not overwrite existing results, keep only the latest
                    if prior_result.metadata["node_id"] in self.results:
                        continue
                    self.results[prior_result.metadata["node_id"]] = prior_result
                    self.run._results.append(prior_result)
                    artifacts = self._find_result_artifacts(archive, prior_result.id)
                    for name, result_artifact in artifacts:
                        prior_result.attach_artifact(name, result_artifact)
        self.run = IbutsuTestRun.from_sequential_test_runs([self.run, prior_run])

    def _update_xdist_result_ids(self) -> None:
        for result in self.results.values():
            result.run_id = self.run.id
            result.metadata["run"] = self.run.id

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(
        self, session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        for item in items:
            result = IbutsuTestResult.from_item(item)
            item.stash[ibutsu_result_key] = result

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        if not self.enabled:
            return
        # we disable pytest-ibutsu here to avoid possible AttributeError in other
        # pytest_collection_modifyitems hooks when "ibutsu_result" is called
        if not session.items:
            self.enabled = False
            return
        self.run.start_timer()

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(
        self,
        item: pytest.Item,
    ) -> Generator[Any, None]:
        if self.enabled:
            item.stash[ibutsu_result_key].start_time = datetime.now(UTC).isoformat()
            self.results[item.nodeid] = item.stash[ibutsu_result_key]
            self.run._results.append(item.stash[ibutsu_result_key])
        return (yield)

    def pytest_exception_interact(
        self,
        node: pytest.Item | pytest.Collector,
        call: pytest.CallInfo[None],
        report: pytest.CollectReport | pytest.TestReport,
    ) -> None:
        if not self.enabled:
            return
        test_result = self.results.get(node.nodeid)
        if not test_result:
            # If an exception is thrown in collection, this method is called, even though we
            # don't yet have an entry for the test, so just ignore the exception and let pytest
            # handle it.
            return
        test_result.attach_artifact("traceback.log", bytes(report.longreprtext, "utf8"))
        test_result.set_metadata_short_tb(call, report)
        test_result.set_metadata_exception_name(call)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if not self.enabled or report.nodeid not in self.results:
            return
        test_result = self.results[report.nodeid]
        test_result.set_metadata_statuses(report)
        test_result.set_metadata_durations(report)
        test_result.set_metadata_user_properties(report)
        test_result.set_metadata_reason(report)

    def pytest_runtest_logfinish(self, nodeid: str) -> None:
        if not self.enabled or nodeid not in self.results:
            return
        test_result = self.results[nodeid]
        test_result.set_metadata_classification()
        test_result.set_result()
        test_result.set_duration()
        self.run.summary.increment(test_result)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node: xdist.workermanage.WorkerController) -> None:
        if not self.enabled or not node.workeroutput["ibutsu_enabled"]:
            self.workers_enabled.append(False)
            return
        self.workers_runs.append(pickle.loads(node.workeroutput["run"]))
        self.results.update(pickle.loads(node.workeroutput["results"]))

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if is_xdist_worker(session.config):
            session.config.workeroutput["ibutsu_enabled"] = self.enabled  # type: ignore
        if (
            not self.enabled
            or is_xdist_controller(session.config)
            and not all(self.workers_enabled)
        ):
            return
        self.run.set_duration()
        # TODO backwards compatibility
        if is_xdist_worker(session.config):
            session.config.workeroutput["run"] = pickle.dumps(self.run)  # type: ignore
            session.config.workeroutput["results"] = pickle.dumps(self.results)  # type: ignore
            return
        if is_xdist_controller(session.config) and self.workers_runs:
            self.run = IbutsuTestRun.from_xdist_test_runs(self.workers_runs)
            self._update_xdist_result_ids()
        self._load_archive()
        session.config.hook.pytest_ibutsu_before_shutdown(
            config=session.config, ibutsu=self
        )

        # Handle the three operation modes
        if self.is_archive_mode or (self.is_s3_mode and not self.ibutsu_no_archive):
            # Archive mode or S3 mode: always create archive
            dump_to_archive(self)

        if self.is_s3_mode:
            # S3 mode: upload archive to S3
            upload_to_s3(ibutsu_plugin=self)
        elif self.is_server_mode:
            # Server mode: send directly to Ibutsu API
            # Create archive if not disabled
            if not self.ibutsu_no_archive:
                dump_to_archive(self)
            send_data_to_ibutsu(self)

    def pytest_addhooks(self, pluginmanager: pytest.PytestPluginManager) -> None:
        from . import newhooks

        pluginmanager.add_hookspecs(newhooks)


ibutsu_plugin_key = pytest.StashKey[IbutsuPlugin]()
ibutsu_result_key = pytest.StashKey[IbutsuTestResult]()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini("ibutsu_server", help="The Ibutsu server to connect to")
    parser.addini("ibutsu_token", help="The JWT token to authenticate with the server")
    parser.addini("ibutsu_source", help="The source of the test run")
    parser.addini(
        "ibutsu_metadata", help="Extra metadata to include with the test results"
    )
    parser.addini("ibutsu_project", help="Project ID or name")
    parser.addini("ibutsu_run_id", help="Test run id")
    parser.addini("ibutsu_no_archive", help="Do not create an archive")
    group = parser.getgroup("ibutsu")
    group.addoption(
        "--ibutsu",
        dest="ibutsu_mode",
        action="store",
        metavar="MODE",
        default=None,
        help="Ibutsu mode: 'archive' to create archive, 's3' to create archive and upload to S3, or URL for direct API upload",
    )
    group.addoption(
        "--ibutsu-token",
        dest="ibutsu_token",
        action="store",
        metavar="TOKEN",
        default=None,
        help="The JWT token to authenticate with the server",
    )
    group.addoption(
        "--ibutsu-source",
        dest="ibutsu_source",
        action="store",
        metavar="SOURCE",
        default="local",
        help="set the source for the tests",
    )
    group.addoption(
        "--ibutsu-data",
        dest="ibutsu_data",
        action="append",
        metavar="DATA",
        default=[],
        help="extra metadata for the test result, key=value (can be used multiple times)",
    )
    group.addoption(
        "--ibutsu-project",
        dest="ibutsu_project",
        action="store",
        metavar="PROJECT",
        default=None,
        help="project id or name - Required",
    )
    group.addoption(
        "--ibutsu-run-id",
        dest="ibutsu_run_id",
        action=UUIDAction,
        metavar="RUN_ID",
        default=str(uuid.uuid4()),
        help="test run id",
    )
    group.addoption(
        "--ibutsu-no-archive",
        dest="ibutsu_no_archive",
        action="store_true",
        default=False,
        help="do not create an archive",
    )


def pytest_configure(config: pytest.Config) -> None:
    plugin = IbutsuPlugin.from_config(config)
    config.pluginmanager.register(plugin)
    config.stash[ibutsu_plugin_key] = plugin


def pytest_report_header(config: pytest.Config) -> list[str]:
    """Add pytest-ibutsu status information to the pytest session header."""
    lines: list[str] = []

    # Get the ibutsu mode from configuration
    ibutsu_mode = config.getoption("ibutsu_mode", default=None)
    try:
        ibutsu_plugin = config.stash[ibutsu_plugin_key]
    except KeyError:
        # Plugin not installed or disabled - no header information to show
        return lines
    if ibutsu_mode and (ibutsu_plugin and ibutsu_plugin.enabled):
        # Determine the mode description
        if ibutsu_plugin.is_server_mode:
            mode_desc = f"server mode (API: {ibutsu_plugin.ibutsu_mode})"
            extra_info = []
            if ibutsu_plugin.ibutsu_project:
                extra_info.append(f"project: {ibutsu_plugin.ibutsu_project}")
            if ibutsu_plugin.ibutsu_source != "local":
                extra_info.append(f"source: {ibutsu_plugin.ibutsu_source}")
            if not ibutsu_plugin.ibutsu_no_archive:
                extra_info.append("archiving: enabled")
            else:
                extra_info.append("archiving: disabled")

            if extra_info:
                mode_desc += f" ({', '.join(extra_info)})"

        elif ibutsu_plugin.is_archive_mode:
            mode_desc = "archive mode (local archive creation)"

        elif ibutsu_plugin.is_s3_mode:
            mode_desc = "S3 mode (archive creation + S3 upload)"

            bucket = os.getenv("AWS_BUCKET", "not configured")
            mode_desc += f" (bucket: {bucket})"

        else:
            mode_desc = f"unknown mode: {ibutsu_plugin.ibutsu_mode}"

        lines.append(f"pytest-ibutsu: {mode_desc}")
        lines.append(f"run ID: {ibutsu_plugin.run.id}")

    return lines


def pytest_terminal_summary(
    terminalreporter: TerminalReporter, exitstatus: int, config: pytest.Config
) -> None:
    """Add pytest-ibutsu operation summary to terminal output."""
    try:
        plugin = config.stash[ibutsu_plugin_key]
        if not plugin.enabled:
            return
    except KeyError:
        return

    summary = plugin.summary_info

    # Only show summary if any operations were performed
    if not any(
        [summary["archive_created"], summary["s3_uploaded"], summary["server_uploaded"]]
    ):
        return

    terminalreporter.write_sep("=", "pytest-ibutsu summary", bold=True)

    # Archive summary
    if summary["archive_created"] and summary["archive_path"]:
        terminalreporter.write_line(f"✓ Archive created: {summary['archive_path']}")

    # S3 summary
    if plugin.is_s3_mode:
        if summary["s3_uploaded"] and summary["s3_upload_count"] > 0:
            bucket = summary.get("s3_bucket", "configured bucket")
            terminalreporter.write_line(
                f"✓ S3 upload: {summary['s3_upload_count']} file(s) uploaded to {bucket}"
            )
        elif summary["s3_upload_errors"] > 0:
            bucket = summary.get("s3_bucket", "S3")
            terminalreporter.write_line(
                f"✗ S3 upload failed: {summary['s3_upload_errors']} error(s) uploading to {bucket}"
            )
        elif not summary["s3_uploaded"]:
            terminalreporter.write_line("✗ S3 upload: No files found or upload failed")

    # Server summary
    if plugin.is_server_mode and summary["server_uploaded"]:
        if summary["frontend_url"]:
            terminalreporter.write_line(
                f"✓ Results uploaded to: {summary['frontend_url']}/runs/{plugin.run.id}"
            )
        else:
            terminalreporter.write_line(
                f"✓ Results uploaded to: {summary['server_url']}"
            )

    # Error summary
    if summary["errors"]:
        terminalreporter.write_line("Errors encountered:")
        for error in summary["errors"]:
            terminalreporter.write_line(f"  - {error}")

    terminalreporter.write_line("")
