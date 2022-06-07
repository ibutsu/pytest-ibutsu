from __future__ import annotations

import argparse
import os
import pickle
import re
import uuid
from datetime import datetime

import pytest

from .archiver import dump_to_archive
from .modeling import TestResult
from .modeling import TestRun
from .sender import send_data_to_ibutsu


UUID_REGEX = re.compile(
    r"^[0-9a-fA-F]{8}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{12}$"
)


class UUIDAction(argparse.Action):
    def __call__(self, parser, namespace, value, option_string=None):
        if not re.match(UUID_REGEX, value):
            raise ValueError("Not a uuid")
        setattr(namespace, self.dest, value)


def is_xdist_worker(config: pytest.Config) -> bool:
    """Return `True` if this is an xdist worker, `False` otherwise"""
    return hasattr(config, "workerinput")


def is_xdist_controller(config: pytest.Config) -> bool:
    """Return `True` if this is the xdist controller, `False` otherwise
    Note: this method also returns `False` when distribution has not been
    activated at all.
    """
    return not is_xdist_worker(config) and config.option.dist != "no"


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
        extra_data: dict,
        run: TestRun,
    ) -> None:
        self.ibutsu_server = ibutsu_server
        self.ibutsu_token = ibutsu_token
        self.ibutsu_source = ibutsu_source
        self.ibutsu_project = ibutsu_project
        self.enabled = enabled
        self.extra_data = extra_data
        self.run = run
        self.workers_runs: list[TestRun] = []
        self.workers_enabled: list[bool] = []
        self.results: dict[str, TestResult] = {}
        # TODO backwards compatibility
        self._data = {}  # type: ignore

    def __getitem__(self, key):
        # TODO backwards compatibility
        return self._data[key]

    def __setitem__(self, key, value):
        # TODO backwards compatibility
        self._data[key] = value

    def upload_artifact_from_file(self, test_uuid, file_name, file_path):
        # TODO backwards compatibility
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
        run = TestRun(
            id=run_id, source=ibutsu_source, metadata={"project": ibutsu_project, **extra_data}
        )  # type: ignore
        enabled = False if config.option.collectonly else bool(ibutsu_server)
        return cls(
            enabled, ibutsu_server, ibutsu_token, ibutsu_source, ibutsu_project, extra_data, run
        )

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(
        self, session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        for item in items:
            item.stash[ibutsu_result_key] = TestResult.from_item(item)
            # TODO backwards compatibility
            item._ibutsu = {
                "id": item.stash[ibutsu_result_key].id,
                "data": {"metadata": {}},
                "artifacts": {},
            }

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        if not self.enabled:
            return
        # we disable pytest-ibutsu here to avoid possible AttributeError in other
        # pytest_collection_modifyitems hooks when "ibutsu_result" is called
        if not session.items:
            self.enabled = False
            return
        self.run.start_timer()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item: pytest.Item) -> object | None:
        if self.enabled:
            item.stash[ibutsu_result_key].start_time = datetime.utcnow().isoformat()
            self.results[item.nodeid] = item.stash[ibutsu_result_key]
        yield

    def pytest_exception_interact(
        self,
        node: pytest.Item | pytest.Collector,
        call: pytest.CallInfo,
        report: pytest.CollectReport | pytest.TestReport,
    ) -> None:
        if not self.enabled:
            return
        test_result = self.results[node.nodeid]
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
        # TODO backwards compatibility
        metadata = getattr(item, "_ibutsu", {}).get("data", {}).get("metadata", {})
        # TODO backwards compatibility
        merge_dicts(metadata, item.stash[ibutsu_result_key].metadata)

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
            session.config.workeroutput["ibutsu_enabled"] = self.enabled
        if (
            not self.enabled
            or is_xdist_controller(session.config)
            and not all(self.workers_enabled)
        ):
            return
        self.run.set_summary_collected(session)
        self.run.set_duration()
        # TODO backwards compatibility
        merge_dicts(self.run["metadata"], self.run.metadata)
        if is_xdist_worker(session.config):
            session.config.workeroutput["run"] = pickle.dumps(self.run)
            session.config.workeroutput["results"] = pickle.dumps(self.results)
            return
        if is_xdist_controller(session.config):
            self.run = TestRun.from_test_runs(self.workers_runs)
        session.config.hook.pytest_ibutsu_before_shutdown(config=session.config, ibutsu=self)
        dump_to_archive(self)
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


def pytest_configure(config: pytest.Config) -> None:
    plugin = IbutsuPlugin.from_config(config)
    config.pluginmanager.register(plugin)
    config.stash[ibutsu_plugin_key] = plugin
    # TODO backwards compatibility
    config._ibutsu = plugin
