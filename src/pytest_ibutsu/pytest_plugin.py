from __future__ import annotations

import argparse
import json
import os
import pickle
import re
import tarfile
import uuid
import warnings
from base64 import urlsafe_b64decode
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Generator
from typing import Iterator

import pytest

from .archiver import dump_to_archive
from .modeling import TestResult
from .modeling import TestRun
from .sender import send_data_to_ibutsu


UUID_REGEX = re.compile(
    r"[0-9a-fA-F]{8}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{12}"
)


class ExpiredTokenError(Exception):
    pass


class UUIDAction(argparse.Action):
    def __call__(self, parser, namespace, value, option_string=None):
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


def merge_dicts(old_dict, new_dict):
    for key, value in old_dict.items():
        if key not in new_dict:
            new_dict[key] = value
        elif isinstance(value, dict):
            merge_dicts(value, new_dict[key])


class IbutsuPlugin:
    def __init__(
        self,
        enabled: bool,
        ibutsu_server: str,
        ibutsu_token: str | None,
        ibutsu_source: str,
        ibutsu_project: str,
        ibutsu_no_archive: bool,
        extra_data: dict,
        run: TestRun,
    ) -> None:
        self.ibutsu_server = ibutsu_server
        self.ibutsu_token = ibutsu_token
        self.ibutsu_source = ibutsu_source
        self.ibutsu_project = ibutsu_project
        self.ibutsu_no_archive = ibutsu_no_archive
        self.enabled = enabled
        self.extra_data = extra_data
        self.run = run
        self.workers_runs: list[TestRun] = []
        self.workers_enabled: list[bool] = []
        self.results: dict[str, TestResult] = {}
        # TODO backwards compatibility
        self._data = {}  # type: ignore
        if self.ibutsu_token and self.is_token_expired(self.ibutsu_token):
            raise ExpiredTokenError("Your token has expired.")

    def is_token_expired(self, token: str) -> bool:
        """Validate a JWT token"""
        payload = token.split(".")[1]
        if len(payload) % 4 != 0:
            payload += "=" * (4 - (len(payload) % 4))
        payload_dict = json.loads(urlsafe_b64decode(payload))
        expires = datetime.fromtimestamp(payload_dict["exp"], tz=timezone.utc)
        return datetime.now(tz=timezone.utc) > expires

    def __getitem__(self, key):
        # TODO backwards compatibility
        warnings.warn(
            f'_ibutsu["{key}"] will be deprecated in pytest-ibutsu 3.0. '
            "Please use a corresponding IbutsuPlugin field.",
            DeprecationWarning,
        )
        return self._data[key]

    def __setitem__(self, key, value):
        # TODO backwards compatibility
        warnings.warn(
            f'_ibutsu["{key}"] will be deprecated in pytest-ibutsu 3.0. '
            "Please use a corresponding IbutsuPlugin field.",
            DeprecationWarning,
        )
        self._data[key] = value

    def upload_artifact_from_file(self, test_uuid, file_name, file_path):
        # TODO backwards compatibility
        warnings.warn(
            "_ibutsu.upload_artifact_from_file will be deprecated in pytest-ibutsu 3.0. "
            "Please use TestResult.attach_artifact",
            DeprecationWarning,
        )
        for test_result in self.results.values():
            if test_result.id == test_uuid:
                test_result.attach_artifact(file_name, file_path)
                break

    # TODO backwards compatibility
    upload_artifact_raw = upload_artifact_from_file

    @staticmethod
    def _parse_data_option(data_list):
        if not data_list:
            return {}
        data_dict = {}
        for data_str in data_list:
            if not data_str:
                continue
            key_str, value = data_str.split("=", 1)
            keys = key_str.split(".")
            current_item = data_dict
            for key in keys[:-1]:
                if key not in current_item:
                    current_item[key] = {}
                current_item = current_item[key]
            key = keys[-1]
            current_item[key] = value
        return data_dict

    @classmethod
    def from_config(cls, config: pytest.Config) -> IbutsuPlugin:
        ibutsu_server = config.getini("ibutsu_server") or config.getoption("ibutsu_server")
        ibutsu_token = config.getini("ibutsu_token") or config.getoption("ibutsu_token")
        ibutsu_source = config.getini("ibutsu_source") or config.getoption("ibutsu_source")
        extra_data = cls._parse_data_option(config.getoption("ibutsu_data"))
        ibutsu_project = (
            os.getenv("IBUTSU_PROJECT")
            or config.getini("ibutsu_project")
            or config.getoption("ibutsu_project")
        )
        run_id = config.getini("ibutsu_run_id") or config.getoption("ibutsu_run_id")
        ibutsu_no_archive = config.getini("ibutsu_no_archive") or config.getoption(
            "ibutsu_no_archive"
        )
        if ibutsu_server and not ibutsu_project:
            raise pytest.UsageError(
                "Ibutsu project is required, use --ibutsu-project, "
                "-o ibutsu_project or the IBUTSU_PROJECT environment variable"
            )
        run = TestRun(
            id=run_id, source=ibutsu_source, metadata={"project": ibutsu_project, **extra_data}
        )
        enabled = False if config.option.collectonly else bool(ibutsu_server)
        return cls(
            enabled,
            ibutsu_server,
            ibutsu_token,
            ibutsu_source,
            ibutsu_project,
            ibutsu_no_archive,
            extra_data,
            run,
        )

    def _find_run_artifacts(self, archive: tarfile.TarFile) -> Iterator[tuple[str, bytes]]:
        for member in archive.getmembers():
            path = Path(member.path)
            if path.match(f"{self.run.id}/*") and path.name != "run.json" and member.isfile():
                yield path.name, archive.extractfile(member).read()  # type: ignore

    def _find_result_artifacts(
        self, archive: tarfile.TarFile, result_id: str
    ) -> Iterator[tuple[str, bytes]]:
        for name in archive.getnames():
            path = Path(name)
            if path.match(f"{self.run.id}/{result_id}/*") and path.name != "result.json":
                yield path.name, archive.extractfile(name).read()  # type: ignore

    def _load_archive(self) -> None:
        """Load data from an ibutsu archive."""
        if not Path(f"{self.run.id}.tar.gz").exists():
            return
        with tarfile.open(f"{self.run.id}.tar.gz", "r:gz") as archive:
            run_json = json.load(archive.extractfile(f"{self.run.id}/run.json"))  # type: ignore
            prior_run = TestRun.from_json(run_json)
            for name, run_artifact in self._find_run_artifacts(archive):
                prior_run.attach_artifact(name, run_artifact)
            for name in archive.getnames():
                if name.endswith("/result.json"):
                    result_json = json.load(archive.extractfile(name))  # type: ignore
                    prior_result = TestResult.from_json(result_json)
                    prior_run._results.append(prior_result)
                    # do not overwrite existing results, keep only the latest
                    if prior_result.metadata["node_id"] in self.results:
                        continue
                    self.results[prior_result.metadata["node_id"]] = prior_result
                    self.run._results.append(prior_result)
                    artifacts = self._find_result_artifacts(archive, prior_result.id)
                    for name, result_artifact in artifacts:
                        prior_result.attach_artifact(name, result_artifact)
        self.run = TestRun.from_sequential_test_runs([self.run, prior_run])

    def _update_xdist_result_ids(self) -> None:
        for result in self.results.values():
            result.run_id = self.run.id
            result.metadata["run"] = self.run.id

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(
        self, session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        for item in items:
            result = TestResult.from_item(item)
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
        self, item: pytest.Item, nextitem: pytest.Item
    ) -> Generator[object, None]:
        if self.enabled:
            item.stash[ibutsu_result_key].start_time = datetime.utcnow().isoformat()
            self.results[item.nodeid] = item.stash[ibutsu_result_key]
            self.run._results.append(item.stash[ibutsu_result_key])
        yield  # type: ignore

    def pytest_exception_interact(
        self,
        node: pytest.Item | pytest.Collector,
        call: pytest.CallInfo,
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

    def pytest_runtest_makereport(self, item, call):
        """Backward compatibility hook to merge metadata from item._ibutsu["data"]["metadata"]"""
        if not self.enabled:
            return

    def pytest_runtest_logfinish(self, nodeid: str) -> None:
        if not self.enabled or nodeid not in self.results:
            return
        test_result = self.results[nodeid]
        test_result.set_metadata_classification()
        test_result.set_result()
        test_result.set_duration()
        self.run.summary.increment(test_result)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node) -> None:
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
        merge_dicts(self.run["metadata"], self.run.metadata)
        if is_xdist_worker(session.config):
            session.config.workeroutput["run"] = pickle.dumps(self.run)  # type: ignore
            session.config.workeroutput["results"] = pickle.dumps(self.results)  # type: ignore
            return
        if is_xdist_controller(session.config):
            self.run = TestRun.from_xdist_test_runs(self.workers_runs)
            self._update_xdist_result_ids()
        self._load_archive()
        session.config.hook.pytest_ibutsu_before_shutdown(config=session.config, ibutsu=self)
        if self.ibutsu_server == "archive" or not self.ibutsu_no_archive:
            dump_to_archive(self)
        if self.ibutsu_server != "archive":
            send_data_to_ibutsu(self)

    def pytest_addhooks(self, pluginmanager: pytest.PytestPluginManager) -> None:
        from . import newhooks

        pluginmanager.add_hookspecs(newhooks)


ibutsu_plugin_key = pytest.StashKey[IbutsuPlugin]()
ibutsu_result_key = pytest.StashKey[TestResult]()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini("ibutsu_server", help="The Ibutsu server to connect to")
    parser.addini("ibutsu_token", help="The JWT token to authenticate with the server")
    parser.addini("ibutsu_source", help="The source of the test run")
    parser.addini("ibutsu_metadata", help="Extra metadata to include with the test results")
    parser.addini("ibutsu_project", help="Project ID or name")
    parser.addini("ibutsu_run_id", help="Test run id")
    parser.addini("ibutsu_no_archive", help="Do not create an archive")
    group = parser.getgroup("ibutsu")
    group.addoption(
        "--ibutsu",
        dest="ibutsu_server",
        action="store",
        metavar="URL",
        default=None,
        help="URL for the Ibutsu server",
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
        action="store",
        metavar="KEY=VALUE",
        nargs="*",
        help="extra metadata for the test result, key=value",
    )
    group.addoption(
        "--ibutsu-project",
        dest="ibutsu_project",
        action="store",
        metavar="PROJECT",
        default=None,
        help="project id or name",
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
