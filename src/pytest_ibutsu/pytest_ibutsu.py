import json
import os
import uuid
from datetime import datetime
import time
import pytest
import attr

# A list of markers that can be filtered out
FILTERED_MARKERS = ["parametrize"]

# Convert the blocker category into an Ibutsu Classification
BLOCKER_CATEGORY_TO_CLASSIFICATION = {
    "needs-triage": "needs_triage",
    "automation-issue": "test_failure",
    "environment-issue": "environment_failure",
    "product-issue": "product_failure",
    "product-rfe": "product_rfe",
}

# Place a limit on the file-size we can upload for artifacts
UPLOAD_LIMIT = 5 * 1024 * 1024  # 5 MiB

# Maximum number of times an API call is retried
MAX_CALL_RETRIES = 3


def merge_dicts(old_dict, new_dict):
    for key, value in old_dict.items():
        if key not in new_dict:
            new_dict[key] = value
        elif isinstance(value, dict):
            merge_dicts(value, new_dict[key])


def parse_data_option(data_list):
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


def get_name(obj):
    return getattr(obj, "_param_name", None) or getattr(obj, "name", None) or str(obj)


def get_test_idents(item):
    try:
        return item.location[2]
    except AttributeError:
        try:
            return item.fspath.strpath
        except AttributeError:
            return None


def overall_test_status(statuses):
    # Handle some logic for when to count certain tests as which state
    for when, status in statuses.items():
        if (when == "call" or when == "setup") and status[1] and status[0] == "skipped":
            return "xfailed"
        elif when == "call" and status[1] and status[0] == "passed":
            return "xpassed"
        elif (when == "setup" or when == "teardown") and status[0] == "failed":
            return "error"
        elif status[0] == "skipped":
            return "skipped"
        elif when == "call" and status[0] == "failed":
            return "failed"
    return "passed"


def get_classification(reason):
    """Get the skip/xfail classification and category from the reason"""
    category = None
    try:
        category = reason.split("category:")[1].strip()
    except IndexError:
        pass
    return BLOCKER_CATEGORY_TO_CLASSIFICATION.get(category)


@attr.s
class Summary(object):
    failures = attr.ib(default=0)
    skips = attr.ib(default=0)
    errors = attr.ib(default=0)
    xfailures = attr.ib(default=0)
    xpasses = attr.ib(default=0)
    tests = attr.ib(default=0)
    collected = attr.ib(default=0)
    not_run = attr.ib(default=0)


@attr.s
class TestRun(object):
    project_id = attr.ib(default=None)
    component = attr.ib(default="")
    duration = attr.ib(default=0.0)
    env = attr.ib(default=0.0)
    id = attr.ib(factory=lambda: str(uuid.uuid4()))
    metadata = attr.ib(factory=dict)
    source = attr.ib(default="local")
    start_time = attr.ib(default=0.0)
    summary = attr.ib(factory=Summary)

    def __attrs_post_init__(self):
        if os.getenv("JOB_NAME") and os.getenv("BUILD_NUMBER"):
            self.metadata["jenkins"] = {
                "job_name": os.getenv("JOB_NAME"),
                "build_number": os.getenv("BUILD_NUMBER"),
                "build_url": os.getenv("BUILD_URL"),
            }
        if os.getenv("IBUTSU_ENV_ID"):
            self.metadata["env_id"] = os.getenv("IBUTSU_ENV_ID")


@attr.s
class TestResult(object):
    test_id = attr.ib()
    component = attr.ib(default=None)
    duration = attr.ib(default=0.0)
    env = attr.ib(default=None)
    id = attr.ib(factory=lambda: str(uuid.uuid4()))
    metadata = attr.ib(factory=dict)
    params = attr.ib(factory=dict)
    project_id = attr.ib(default=None)
    result = attr.ib(default="failed")
    run_id = attr.ib(default=None)
    source = attr.ib(default="local")
    start_time = attr.ib(factory=lambda: datetime.utcnow().isoformat())
    artifacts = attr.ib(factory=list)

    @classmethod
    def from_item(cls, item):
        if hasattr(item, "callspec"):
            try:
                params = {p: get_name(v) for p, v in item.callspec.params.items()}
            except Exception:
                params = {}
        else:
            params = {}
        fspath = item.location[0] or item.fspath.strpath
        if "site-packages/" in fspath:
            fspath = fspath[fspath.find("site-packages/") + 14 :]
        return cls(
            test_id=item.name,
            params=params,
            source=item.config._ibutsu.ibutsu_source,
            metadata={
                "statuses": {},
                "run": item.config._ibutsu.run.id,
                "durations": {},
                "fspath": fspath,
                "markers": [
                    {"name": m.name, "args": m.args, "kwargs": m.kwargs}
                    for m in item.iter_markers()
                    if m.name not in FILTERED_MARKERS
                ],
                **item.config._ibutsu.extra_data,
            },
        )

    def _get_xfail_reason(self, report):
        xfail_reason = None
        if self.metadata.get("markers"):
            for marker in self.metadata["markers"]:
                if marker.get("name") == "xfail":
                    xfail_reason = marker["kwargs"].get("reason")
        else:
            xfail_reason = report.wasxfail.split("reason: ")[1]
        return xfail_reason

    def _get_skip_reason(self, report):
        skip_reason = None
        # first see if the reason is in the marker skip
        if self.metadata.get("markers"):
            for marker in self.metadata["markers"]:
                if marker.get("name") == "skipif":
                    skip_reason = marker["kwargs"].get("reason")
                elif marker.get("name") == "skip":
                    try:
                        skip_reason = marker["args"][0]
                    except IndexError:
                        pass
        # otherwise we must use the report to get the skip information
        else:
            try:
                if report.longrepr:
                    skip_reason = report.longrepr[2].split("Skipped: ")[1]
            except IndexError:
                pass
        return skip_reason

    def set_metadata_reason(self, report):
        if self.result == "skipped" and not self.metadata.get("skip_reason"):
            reason = self._get_skip_reason(report)
            if reason:
                self.metadata["skip_reason"] = reason
        elif self.result == "xfailed":
            reason = self._get_xfail_reason(report)
            if reason:
                self.metadata["xfail_reason"] = reason


class IbutsuPlugin(object):
    def __init__(
        self, ibutsu_server, ibutsu_token, ibutsu_source, extra_data, ibutsu_project, enabled
    ) -> None:
        self.ibutsu_server = ibutsu_server
        self.ibutsu_token = ibutsu_token
        self.ibutsu_source = ibutsu_source
        self.extra_data = extra_data
        self.enabled = enabled
        self.run = TestRun(project_id=ibutsu_project, source=self.ibutsu_source)
        self.results = {}
        self._start_time = None

    @classmethod
    def from_config(cls, config):
        ibutsu_server = config.getini("ibutsu_server") or config.getoption("ibutsu_server")
        ibutsu_token = config.getini("ibutsu_token") or config.getoption("ibutsu_token")
        ibutsu_source = config.getini("ibutsu_source") or config.getoption("ibutsu_source", "local")
        extra_data = parse_data_option(config.getoption("ibutsu_data", []))
        ibutsu_project = (
            os.getenv("IBUTSU_PROJECT")
            or config.getini("ibutsu_project")
            or config.getoption("ibutsu_project", None)
        )
        enabled = bool(ibutsu_server)
        return cls(ibutsu_server, ibutsu_token, ibutsu_source, extra_data, ibutsu_project, enabled)

    @property
    def duration(self):
        if self._start_time:
            return time.time() - self._start_time
        else:
            return 0.0

    def start_timer(self):
        if not self._start_time:
            self._start_time = time.time()

    def pytest_collection_finish(self, session):
        if not self.enabled:
            return

        self.start_timer()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item):
        if self.enabled:
            test_result = TestResult.from_item_and_plugin(item, self)
            for metadata in item.config.hook.pytest_ibutsu_get_result_metadata(item=item):
                test_result.metadata.update(metadata)
            self.results[item.nodeid] = test_result

            def _default(obj):
                if callable(obj) and hasattr(obj, "__code__"):
                    return f"function: '{obj.__name__}', args: {str(obj.__code__.co_varnames)}"
                else:
                    return str(obj)

            # serialize the metadata just in case of any functions present
            test_result.metadata = json.loads(json.dumps(test_result.metadata, default=_default))
        yield

    def pytest_exception_interact(self, node, call, report):
        if not self.enabled:
            return

        val = safe_string(call.excinfo.value)
        last_lines = "\n".join(report.longreprtext.split("\n")[-4:])
        short_tb = "{}\n{}\n{}".format(
            last_lines, call.excinfo.type.__name__, val.encode("ascii", "xmlcharrefreplace")
        )
        id = report._ibutsu["id"]
        data = report._ibutsu["data"]
        self.upload_artifact_raw(id, "traceback.log", bytes(report.longreprtext, "utf8"))
        data["metadata"]["short_tb"] = short_tb
        data["metadata"]["exception_name"] = call.excinfo.type.__name__
        report._ibutsu["data"] = data

    def pytest_runtest_logreport(self, report):
        if not report.nodeid in self.results:
            return

        test_result = self.results[report.nodeid]
        xfail = hasattr(report, "wasxfail")
        test_result.metadata["statuses"][report.when] = (report.outcome, xfail)
        test_result.metadata["durations"][report.when] = report.duration
        test_result.metadata["user_properties"] = dict(report.user_properties)
        test_result.result = overall_test_status(test_result.metadata["statuses"])
        test_result.set_metadata_reason(report)
        reason = test_result.metadata.get("skip_reason") or test_result.metadata.get("xfail_reason")
        if reason:
            classification = get_classification(reason)
            if classification:
                test_result.metadata["classification"] = classification
        test_result.duration = sum(v for v in test_result.metadata["durations"].values())

    def pytest_sessionfinish(self, session):
        if not self.enabled:
            return

        self.run.duration = self.duration
        for metadata in session.config.hook.pytest_ibutsu_get_run_metadata(session=session):
            self.run.metadata.update(metadata)

    def pytest_unconfigure(self, config):
        if not self.enabled:
            return

        config.hook.pytest_ibutsu_before_shutdown(config=config, ibutsu=self)
        # self.shutdown()
        # if self.run.id:
        #     self.output_msg()

    def pytest_ibutsu_add_artifact(self, item_or_node, name, path):
        pass

    def pytest_ibutsu_is_enabled(self):
        return self.enabled

    def pytest_addhooks(self, pluginmanager):
        from . import newhooks

        pluginmanager.add_hookspecs(newhooks)


def pytest_addoption(parser):
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


def pytest_configure(config):
    plugin = IbutsuPlugin.from_config(config)
    config.pluginmanager.register(plugin)
    config._ibutsu = plugin
