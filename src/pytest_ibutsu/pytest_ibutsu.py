import os
from datetime import datetime
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

import pytest

from .archiver import dump_archive
from .modeling import TestResult
from .modeling import TestRun

# Place a limit on the file-size we can upload for artifacts
UPLOAD_LIMIT = 5 * 1024 * 1024  # 5 MiB

# Maximum number of times an API call is retried
MAX_CALL_RETRIES = 3


class IbutsuPlugin:
    def __init__(
        self,
        ibutsu_server: str,
        ibutsu_token: str,
        ibutsu_source: str,
        extra_data: Dict[str, str],
        ibutsu_project: str,
        enabled: bool,
    ) -> None:
        self.ibutsu_server = ibutsu_server
        self.ibutsu_token = ibutsu_token
        self.ibutsu_source = ibutsu_source
        self.extra_data = extra_data
        self.enabled = enabled
        self.run = TestRun(project_id=ibutsu_project, source=self.ibutsu_source)
        self.results: Dict[str, TestResult] = {}

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
    def from_config(cls, config) -> "IbutsuPlugin":
        ibutsu_server = config.getini("ibutsu_server") or config.getoption("ibutsu_server")
        ibutsu_token = config.getini("ibutsu_token") or config.getoption("ibutsu_token")
        ibutsu_source = config.getini("ibutsu_source") or config.getoption("ibutsu_source", "local")
        extra_data = cls._parse_data_option(config.getoption("ibutsu_data", []))
        ibutsu_project = (
            os.getenv("IBUTSU_PROJECT")
            or config.getini("ibutsu_project")
            or config.getoption("ibutsu_project", None)
        )
        enabled = bool(ibutsu_server)
        return cls(ibutsu_server, ibutsu_token, ibutsu_source, extra_data, ibutsu_project, enabled)

    def upload_artifact_from_file(self, *args):
        pass

    @pytest.mark.tryfirst
    def pytest_collection_modifyitems(
        self, session: pytest.Session, config, items: List[pytest.Item]
    ) -> None:
        if not self.enabled:
            return
        for item in items:
            item.ibutsu_result = TestResult.from_item(item)

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        if not self.enabled:
            return
        self.run.start_timer()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item: pytest.Item) -> Optional[object]:
        if self.enabled:
            item.ibutsu_result.start_time = datetime.utcnow().isoformat()
            self.results[item.nodeid] = item.ibutsu_result
        yield

    def pytest_exception_interact(
        self,
        node: Union[pytest.Item, pytest.Collector],
        call,
        report,
    ) -> None:
        if not self.enabled:
            return
        self.upload_artifact_raw(id, "traceback.log", bytes(report.longreprtext, "utf8"))
        test_result = self.results[node.nodeid]
        test_result.set_metadata_short_tb(call, report)
        test_result.set_metadata_exception_name(call)

    def pytest_runtest_logreport(self, report) -> None:
        if not self.enabled or report.nodeid not in self.results:
            return
        test_result = self.results[report.nodeid]
        test_result.set_metadata_statuses(report)
        test_result.set_metadata_durations(report)
        test_result.set_metadata_user_properties(report)
        test_result.set_metadata_reason(report)

    def pytest_runtest_logfinish(
        self, nodeid: str, location: Tuple[str, Optional[int], str]
    ) -> None:
        if not self.enabled or nodeid not in self.results:
            return
        test_result = self.results[nodeid]
        test_result.set_metadata_classification()
        test_result.set_result()
        test_result.set_duration()
        self.run.summary.increment(test_result)

    def pytest_sessionfinish(
        self, session: pytest.Session, exitstatus: Union[int, pytest.ExitCode]
    ) -> None:
        if not self.enabled:
            return
        self.run.set_summary_collected(session)
        self.run.set_duration()

    def pytest_unconfigure(self, config) -> None:
        if not self.enabled:
            return
        config.hook.pytest_ibutsu_before_shutdown(config=config, ibutsu=self)
        if self.ibutsu_server == "archive":
            dump_archive(self.run, self.results.values())
        # if self.run.id:
        #     self.output_msg()

    def pytest_addhooks(self, pluginmanager) -> None:
        from . import newhooks

        pluginmanager.add_hookspecs(newhooks)


def pytest_addoption(parser, pluginmanager) -> None:
    parser.addini("ibutsu_server", help="The Ibutsu server to connect to")
    parser.addini("ibutsu_token", help="The JWT token to authenticate with the server")
    parser.addini("ibutsu_source", help="The source of the test run")
    parser.addini("ibutsu_metadata", help="Extra metadata to include with the test results")
    parser.addini("ibutsu_project", help="Project ID or name")
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
        default=None,
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


def pytest_configure(config) -> None:
    plugin = IbutsuPlugin.from_config(config)
    config.pluginmanager.register(plugin)
    config.ibutsu_plugin = plugin
