import json
import os
import shutil
import tarfile
import time
import uuid
from tempfile import gettempdir
from tempfile import NamedTemporaryFile

import pytest
from ibutsu_client import ApiClient
from ibutsu_client import ApiException
from ibutsu_client import ArtifactApi
from ibutsu_client import Configuration
from ibutsu_client import HealthApi
from ibutsu_client import ResultApi
from ibutsu_client import RunApi
from urllib3.exceptions import MaxRetryError

# A list of markers that can be filtered out
FILTERED_MARKERS = ["parametrize"]
CA_BUNDLE_ENVS = ["REQUESTS_CA_BUNDLE", "IBUTSU_CA_BUNDLE"]

# Convert the blocker category into an Ibutsu Classification
BLOCKER_CATEGORY_TO_CLASSIFICATION = {
    "automation-issue": "test_failure",
    "environment-issue": "environment_failure",
    "product-issue": "product_failure",
    "product-rfe": "product_rfe",
}

# Place a limit on the file-size we can upload for artifacts
UPLOAD_LIMIT = 256 * 1024  # 256 KiB


def safe_string(o):
    """This will make string out of ANYTHING without having to worry about the stupid Unicode errors

    This function tries to make str/unicode out of ``o`` unless it already is one of those and then
    it processes it so in the end there is a harmless ascii string.

    Args:
        o: Anything.
    """
    if not isinstance(o, str):
        o = str(o)
    if isinstance(o, bytes):
        o = o.decode("utf-8", "ignore")
    o = o.encode("ascii", "xmlcharrefreplace").decode("ascii")
    return o


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


def get_test_idents(item):
    try:
        return item.location[2], item.location[0]
    except AttributeError:
        try:
            return item.fspath.strpath, None
        except AttributeError:
            return (None, None)


def get_name(obj):
    return getattr(obj, "_param_name", None) or getattr(obj, "name", None) or str(obj)


def overall_test_status(statuses):
    # Handle some logic for when to count certain tests as which state
    for when, status in statuses.items():
        if when == "call" and status[1] and status[0] == "skipped":
            return "xfailed"
        elif when == "call" and status[1] and status[0] == "failed":
            return "xpassed"
        elif (when == "setup" or when == "teardown") and status[0] == "failed":
            return "error"
        elif status[0] == "skipped":
            return "skipped"
        elif when == "call" and status[0] == "failed":
            return "failed"
    return "passed"


class IbutsuArchiver(object):
    """
    Save all Ibutsu results to archive
    """

    _start_time = None
    _stop_time = None
    frontend = None

    def __init__(self, source=None, path=None, extra_data=None):
        self._results = {}
        self._run_id = None
        self.run = None
        self.temp_path = path
        self.source = source or "local"
        self.extra_data = extra_data or {}

        # Set an env var ID
        if os.environ.get("IBUTSU_ENV_ID"):
            self.extra_data.update({"env_id": os.environ.get("IBUTSU_ENV_ID")})
        # Auto-detect running in Jenkins and add to the metadata
        if os.environ.get("JOB_NAME") and os.environ.get("BUILD_NUMBER"):
            self.extra_data.update(
                {
                    "jenkins": {
                        "job_name": os.environ.get("JOB_NAME"),
                        "build_number": os.environ.get("BUILD_NUMBER"),
                        "build_url": os.environ.get("BUILD_URL"),
                    }
                }
            )
        # If the project is set via environment variables
        if os.environ.get("IBUTSU_PROJECT"):
            self.extra_data.update({"project", os.environ.get("IBUTSU_PROJECT")})

    def _status_to_summary(self, status):
        return {"failed": "failures", "error": "errors", "skipped": "skips", "tests": "tests"}.get(
            status
        )

    def _save_run(self, run):
        if not self.temp_path:
            self.temp_path = os.path.join(gettempdir(), run["id"])
        os.makedirs(self.temp_path, exist_ok=True)
        if not run.get("metadata"):
            run["metadata"] = {}
        run["metadata"].update(self.extra_data)
        with open(os.path.join(self.temp_path, "run.json"), "w") as f:
            json.dump(run, f)

    @property
    def run_id(self):
        if not self._run_id:
            raise Exception("You need to use set_run_id() to set a run ID")
        return self._run_id

    @property
    def duration(self):
        if self._start_time and self._stop_time:
            return self._stop_time - self._start_time
        elif self._start_time:
            return time.time() - self._start_time
        else:
            return 0

    def start_timer(self):
        if not self._start_time:
            self._start_time = time.time()

    def stop_timer(self):
        if not self._stop_time:
            self._stop_time = time.time()

    def shutdown(self):
        # Gather the summary before building the archive
        summary = {"failures": 0, "skips": 0, "errors": 0, "tests": 0}
        for result in self._results.values():
            key = self._status_to_summary(result["result"])
            if key in summary:
                summary[key] += 1
            summary["tests"] += 1
        self.run["summary"] = summary
        self.update_run()
        # Build the tarball
        self.tar_file = os.path.join(os.path.abspath("."), f"{self.run_id}.tar.gz")
        print("Creating archive {}...".format(os.path.basename(self.tar_file)))
        with tarfile.open(self.tar_file, "w:gz") as tar:
            tar.add(self.temp_path, self.run_id)

    def output_msg(self):
        if hasattr(self, "tar_file"):
            print(f"Saved results archive to {self.tar_file}")

    def get_run_id(self):
        if not self.run:
            run = {
                "duration": 0,
                "summary": {"failures": 0, "skips": 0, "errors": 0, "tests": 0},
                "metadata": self.extra_data,
                "source": getattr(self, "source", "local"),
                "start_time": time.time(),
            }
            self.run = self.add_run(run=run)
        return self.run["id"]

    def set_run_id(self, run_id):
        self._run_id = run_id
        self.refresh_run()

    def add_run(self, run=None):
        if not run.get("id"):
            run["id"] = str(uuid.uuid4())
        if not run.get("source"):
            run["source"] = self.source
        self._save_run(run)
        return run

    def refresh_run(self):
        """This does nothing, there's nothing to do here"""
        pass

    def update_run(self, duration=None):
        if duration:
            self.run["duration"] = duration
        self._save_run(self.run)

    def add_result(self, result):
        result_id = result.get("id")
        if not result_id:
            result_id = str(uuid.uuid4())
            result["id"] = result_id
        art_path = os.path.join(self.temp_path, result_id)
        os.makedirs(art_path, exist_ok=True)
        if not result.get("metadata"):
            result["metadata"] = {}
        result["metadata"].update(self.extra_data)
        with open(os.path.join(art_path, "result.json"), "w") as f:
            json.dump(result, f)
        self._results[result_id] = result
        return result

    def update_result(self, id, result):
        art_path = os.path.join(self.temp_path, id)
        os.makedirs(art_path, exist_ok=True)
        if not result.get("metadata"):
            result["metadata"] = {}
        result["metadata"].update(self.extra_data)
        with open(os.path.join(art_path, "result.json"), "w") as f:
            json.dump(result, f)
        self._results[id] = result

    def upload_artifact(self, id, filename, data):
        file_size = os.stat(data).st_size
        if file_size < UPLOAD_LIMIT:
            art_path = os.path.join(self.temp_path, id)
            os.makedirs(art_path, exist_ok=True)
            shutil.copyfile(data, os.path.join(art_path, filename))
        else:
            print(
                f"File '{filename}' of size '{file_size}' bytes"
                f" exceeds global Ibutsu upload limit of '{UPLOAD_LIMIT}' bytes."
                f" File will not be uploaded to Ibutsu."
            )

    def upload_artifact_raw(self, res_id, filename, data):
        file_object = NamedTemporaryFile(delete=False)
        os_file_name = file_object.name
        file_object.write(data)
        file_object.close()
        self.upload_artifact(res_id, filename, os_file_name)

    def upload_artifact_from_file(self, res_id, logged_filename, filename):
        self.upload_artifact(res_id, logged_filename, filename)

    def get_skip_reason(self, data, report):
        skip_reason = None
        # first see if the reason is in the marker skip
        if data["metadata"].get("markers"):
            for marker in data["metadata"]["markers"]:
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

    def get_skip_classification(self, skip_reason):
        """ Get the skip classification and category from the skip_reason"""
        category = None
        try:
            category = skip_reason.split("category:")[1].strip()
        except IndexError:
            pass
        return BLOCKER_CATEGORY_TO_CLASSIFICATION.get(category)

    @pytest.mark.tryfirst
    def pytest_collection_modifyitems(self, items):
        for item in items:
            data = getattr(item, "_ibutsu", {})
            new_data = {"id": None, "data": {"metadata": {}}, "artifacts": {}}
            merge_dicts(data, new_data)
            item._ibutsu = new_data

    @pytest.mark.hookwrapper
    def pytest_runtest_protocol(self, item):
        if hasattr(item, "callspec"):
            try:
                params = {p: get_name(v) for p, v in item.callspec.params.items()}
            except Exception:
                params = {}
        else:
            params = {}
        start_time = time.time()
        fspath = item.location[0] or item.fspath.strpath
        if "site-packages/" in fspath:
            fspath = fspath[fspath.find("site-packages/") + 14 :]
        data = {
            "result": "failed",
            "source": getattr(self, "source", "local"),
            "params": params,
            "starttime": start_time,
            "start_time": start_time,
            "test_id": get_test_idents(item)[0],
            "metadata": {
                "statuses": {},
                "run": self.run_id,
                "durations": {},
                "fspath": fspath,
                "markers": [
                    {"name": m.name, "args": m.args, "kwargs": m.kwargs}
                    for m in item.iter_markers()
                    if m.name not in FILTERED_MARKERS
                ],
            },
        }

        def _default(obj):
            if callable(obj) and hasattr(obj, "__code__"):
                return f"function: '{obj.__name__}', args: {str(obj.__code__.co_varnames)}"
            else:
                return str(obj)

        # serialize the metadata just in case of any functions present
        data["metadata"] = json.loads(json.dumps(data["metadata"], default=_default))
        result = self.add_result(result=data)
        item._ibutsu["id"] = result["id"]
        # Update result data
        old_data = item._ibutsu["data"]
        merge_dicts(old_data, data)
        item._ibutsu["data"] = data
        yield

    def pytest_exception_interact(self, node, call, report):
        if not hasattr(report, "_ibutsu"):
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
        self.update_result(id, result=data)

    def pytest_runtest_logreport(self, report):
        if not hasattr(report, "_ibutsu"):
            return

        if hasattr(report, "wasxfail"):
            xfail = True
        else:
            xfail = False

        id = report._ibutsu["id"]
        data = report._ibutsu["data"]
        data["metadata"]["user_properties"] = {key: value for key, value in report.user_properties}
        data["metadata"]["statuses"][report.when] = (report.outcome, xfail)
        data["metadata"]["durations"][report.when] = report.duration
        data["result"] = overall_test_status(data["metadata"]["statuses"])
        if data["result"] == "skipped" and not data["metadata"].get("skip_reason"):
            skip_reason = self.get_skip_reason(data, report)
            if skip_reason:
                data["metadata"]["skip_reason"] = skip_reason
                classification = self.get_skip_classification(skip_reason)
                if classification:
                    data["metadata"]["classification"] = classification
        data["duration"] = sum([v for v in data["metadata"]["durations"].values()])
        self.update_result(id, result=data)

    def pytest_sessionfinish(self):
        self.stop_timer()
        self.update_run(duration=self.duration)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        res = outcome.get_result()  # will raise if outcome was exception
        res._ibutsu = item._ibutsu


class IbutsuSender(IbutsuArchiver):
    """
    An enhanced Ibutsu plugin that also sends Ibutsu results to an Ibutsu server
    """

    def __init__(self, server_url, source=None, path=None, extra_data=None):
        self.server_url = server_url
        self._has_server_error = False
        self._server_error_tbs = []
        self._sender_cache = []

        config = Configuration()
        config.host = self.server_url
        # Only set the SSL CA cert if one of the environment variables is set
        for env_var in CA_BUNDLE_ENVS:
            if os.getenv(env_var, None):
                config.ssl_ca_cert = os.getenv(env_var)

        api_client = ApiClient(config)
        self.result_api = ResultApi(api_client)
        self.artifact_api = ArtifactApi(api_client)
        self.run_api = RunApi(api_client)
        self.health_api = HealthApi(api_client)
        super().__init__(source=source, path=path, extra_data=extra_data)

    def _make_call(self, api_method, *args, **kwargs):
        for res in self._sender_cache:
            if res.ready():
                self._sender_cache.remove(res)
        try:
            out = api_method(*args, **kwargs)
            if "async_req" in kwargs:
                self._sender_cache.append(out)
            return out
        except (MaxRetryError, ApiException) as e:
            self._has_server_error = self._has_server_error or True
            self._server_error_tbs.append(str(e))
            return None

    def add_run(self, run=None):
        server_run = self._make_call(self.run_api.add_run, run=run)
        if server_run:
            run = server_run.to_dict()
        return super().add_run(run)

    def refresh_run(self):
        # This can safely completely override the underlying method, because it does nothing
        if not self.run_id:
            return
        server_run = self._make_call(self.run_api.get_run, self.run_id)
        if server_run:
            self.run = server_run.to_dict()

    def update_run(self, duration=None):
        super().update_run(duration)
        self._make_call(self.run_api.update_run, self.run["id"], run=self.run)

    def add_result(self, result):
        if not result.get("metadata"):
            result["metadata"] = {}
        result["metadata"].update(self.extra_data)
        server_result = self._make_call(self.result_api.add_result, result=result)
        if server_result:
            result.update(server_result.to_dict())
        return super().add_result(result)

    def update_result(self, id, result):
        self._make_call(self.result_api.update_result, id, result=result, async_req=True)
        super().update_result(id, result)

    def upload_artifact(self, id, filename, data):
        super().upload_artifact(id, filename, data)
        file_size = os.stat(data).st_size
        if file_size < UPLOAD_LIMIT:
            self._make_call(self.artifact_api.upload_artifact, id, filename, data)

    def output_msg(self):
        with open(".last-ibutsu-run-id", "w") as f:
            f.write(self.run_id)
        url = f"{self.frontend}/runs/{self.run_id}"
        with open(".last-ibutsu-run-url", "w") as f:
            f.write(url)
        if not self._has_server_error:
            print(f"Results can be viewed on: {url}")
        else:
            print(
                "There was an error while uploading results,"
                " and not all results were uploaded to the server."
            )
            print(f"All results were written to archive, partial results can be viewed on: {url}")
        super().output_msg()

    def shutdown(self):
        super().shutdown()
        print(f"Ibutsu client finishing up...({len(self._sender_cache)} tasks left)...")
        while self._sender_cache:
            for res in self._sender_cache:
                if res.ready():
                    self._sender_cache.remove(res)
            time.sleep(0.1)
        print("Cleanup complete")


def pytest_addoption(parser):
    parser.addini("ibutsu_server", help="The Ibutsu server to connect to")
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


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node):
    if not hasattr(node.config, "_ibutsu"):
        # If this plugin is not active
        return
    node.workerinput["run_id"] = node.config._ibutsu.run_id


def pytest_configure(config):
    ibutsu_server = config.getoption("ibutsu_server", None)
    if config.getini("ibutsu_server"):
        ibutsu_server = config.getini("ibutsu_server")
    if not ibutsu_server:
        return
    ibutsu_source = config.getoption("ibutsu_source", None)
    if config.getini("ibutsu_source"):
        ibutsu_source = config.getini("ibutsu_source")
    ibutsu_data = parse_data_option(config.getoption("ibutsu_data", []))
    ibutsu_project = config.getoption("ibutsu_project", None)
    if config.getini("ibutsu_project"):
        ibutsu_project = config.getini("ibutsu_project")
    if ibutsu_project:
        ibutsu_data.update({"project": ibutsu_project})
    if ibutsu_server != "archive":
        try:
            print("Ibutsu server: {}".format(ibutsu_server))
            if ibutsu_server.endswith("/"):
                ibutsu_server = ibutsu_server[:-1]
            if not ibutsu_server.endswith("/api"):
                ibutsu_server += "/api"
            ibutsu = IbutsuSender(ibutsu_server, ibutsu_source, extra_data=ibutsu_data)
            ibutsu.frontend = ibutsu.health_api.get_health_info().frontend
        except MaxRetryError:
            print("Connection failure in health check - switching to archiver")
            ibutsu = IbutsuArchiver(extra_data=ibutsu_data)
        except ApiException:
            print("Error in call to Ibutsu API")
            ibutsu = IbutsuArchiver(extra_data=ibutsu_data)
    else:
        ibutsu = IbutsuArchiver(extra_data=ibutsu_data)
    if config.pluginmanager.has_plugin("xdist"):
        if hasattr(config, "workerinput") and config.workerinput.get("run_id"):
            ibutsu.set_run_id(config.workerinput["run_id"])
        else:
            ibutsu.set_run_id(ibutsu.get_run_id())
    config._ibutsu = ibutsu
    config.pluginmanager.register(config._ibutsu)


def pytest_collection_finish(session):
    if not hasattr(session.config, "_ibutsu"):
        # If this plugin is not active
        return
    ibutsu = session.config._ibutsu
    if not session.config.pluginmanager.has_plugin("xdist"):
        ibutsu.set_run_id(ibutsu.get_run_id())
    ibutsu.start_timer()
    ibutsu.output_msg()


def pytest_unconfigure(config):
    ibutsu_instance = getattr(config, "_ibutsu", None)
    if ibutsu_instance:
        del config._ibutsu
        config.pluginmanager.unregister(ibutsu_instance)
        ibutsu_instance.shutdown()
        if ibutsu_instance.run_id:
            ibutsu_instance.output_msg()
