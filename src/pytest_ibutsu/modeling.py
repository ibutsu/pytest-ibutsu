import json
import os
import time
import uuid
import warnings
from datetime import datetime
from typing import Any
from typing import ClassVar
from typing import Dict
from typing import List
from typing import Mapping
from typing import Optional
from typing import Tuple
from typing import TypedDict
from typing import Union

import cattrs
import pytest
from attrs import asdict
from attrs import Attribute
from attrs import define
from attrs import field
from pytest import ExceptionInfo


ItemMarker = TypedDict(
    "ItemMarker", {"name": str, "args": Tuple[Any, ...], "kwargs": Mapping[str, Any]}
)


def _safe_string(obj):
    """This will make string out of ANYTHING without having to worry about the stupid Unicode errors

    This function tries to make str/unicode out of ``obj`` unless it already is one of those and
    then it processes it so in the end there is a harmless ascii string.
    """
    if not isinstance(obj, str):
        obj = str(obj)
    if isinstance(obj, bytes):
        obj = obj.decode("utf-8", "ignore")
    return obj.encode("ascii", "xmlcharrefreplace").decode("ascii")


def _json_serializer(obj):
    if callable(obj) and hasattr(obj, "__code__"):
        return f"function: '{obj.__name__}', args: {str(obj.__code__.co_varnames)}"
    else:
        return str(obj)


def _serializer(inst: type, field: Attribute, value: Any) -> Any:
    if field and field.name == "metadata":
        return json.loads(json.dumps(value, default=_json_serializer))
    else:
        return value


@define(auto_attribs=True)
class Summary:
    failures: int = 0
    errors: int = 0
    xfailures: int = 0
    xpasses: int = 0
    skips: int = 0
    tests: int = 0
    collected: int = 0
    not_run: int = 0

    def increment(self, test_result: "TestResult") -> None:
        attr = {
            "failed": "failures",
            "error": "errors",
            "skipped": "skips",
            "xfailed": "xfailures",
            "xpassed": "xpasses",
        }.get(test_result.result)
        if attr:
            current_count = getattr(self, attr)
            setattr(self, attr, current_count + 1)
        self.tests += 1
        self.collected += 1

    @classmethod
    def from_results(cls, results: List["TestResult"]) -> "Summary":
        summary = cls()
        for result in results:
            summary.increment(result)
        summary.collected = len(results)
        return summary


@define(auto_attribs=True)
class TestRun:
    component: Optional[str] = None
    env: Optional[str] = None
    id: str = field(factory=lambda: str(uuid.uuid4()))
    metadata: Dict = field(factory=dict)
    source: Optional[str] = None
    start_time: str = ""
    duration: float = 0.0
    _results: List["TestResult"] = field(factory=list)
    _start_unix_time: float = field(init=False, default=0.0)
    _artifacts: Dict[str, Union[bytes, str]] = field(factory=dict)
    summary: Summary = field(factory=Summary)
    # TODO backwards compatibility
    _data: Dict = field(factory=dict)

    def __getitem__(self, key):
        # TODO backwards compatibility
        warnings.warn(
            f'_ibutsu["{key}"] will be deprecated in pytest-ibutsu 3.0. '
            "Please use a corresponding TestRun field.",
            DeprecationWarning,
        )
        return self._data[key]

    def __setitem__(self, key, value):
        # TODO backwards compatibility
        warnings.warn(
            f'_ibutsu["{key}"] will be deprecated in 3.0. '
            "Please use a corresponding TestRun field.",
            DeprecationWarning,
        )
        self._data[key] = value

    def __attrs_post_init__(self) -> None:
        if os.getenv("JOB_NAME") and os.getenv("BUILD_NUMBER"):
            self.metadata["jenkins"] = {
                "job_name": os.getenv("JOB_NAME"),
                "build_number": os.getenv("BUILD_NUMBER"),
                "build_url": os.getenv("BUILD_URL"),
            }
        if os.getenv("IBUTSU_ENV_ID"):
            self.metadata["env_id"] = os.getenv("IBUTSU_ENV_ID")
        # TODO backwards compatibility
        self["metadata"] = {}

    def start_timer(self) -> None:
        self._start_unix_time = time.time()
        self.start_time = datetime.utcnow().isoformat()

    def set_duration(self) -> None:
        if self._start_unix_time:
            self.duration = time.time() - self._start_unix_time

    def attach_artifact(self, name: str, content: Union[bytes, str]) -> None:
        self._artifacts[name] = content

    def to_dict(self) -> Dict:
        return asdict(
            self,
            filter=lambda attr, _: not attr.name.startswith("_"),
            value_serializer=_serializer,
        )

    @staticmethod
    def get_metadata(runs: List["TestRun"]) -> Dict:
        metadata = {}
        for run in runs:
            metadata.update(run.metadata)
        return metadata

    @classmethod
    def from_xdist_test_runs(cls, runs: List["TestRun"]) -> "TestRun":
        first_run = runs[0]
        results = []
        for run in runs:
            for result in run._results:
                result.run_id = first_run.id
                result.metadata["run"] = first_run.id
                results.append(result)
        return TestRun(
            component=first_run.component,
            env=first_run.env,
            id=first_run.id,
            metadata=cls.get_metadata(runs),
            source=first_run.source,
            start_time=min(runs, key=lambda run: run.start_time).start_time,
            duration=max(runs, key=lambda run: run.duration).duration,
            summary=Summary.from_results(results),
            artifacts=first_run._artifacts,
            results=results,
        )

    @classmethod
    def from_sequential_test_runs(cls, runs: List["TestRun"]) -> "TestRun":
        latest_run = max(runs, key=lambda run: run.start_time)
        return TestRun(
            component=latest_run.component,
            env=latest_run.env,
            id=latest_run.id,
            metadata=cls.get_metadata(runs),
            source=latest_run.source,
            start_time=min(runs, key=lambda run: run.start_time).start_time,
            duration=sum(run.duration for run in runs),
            summary=Summary.from_results(latest_run._results),
            artifacts=latest_run._artifacts,
            results=latest_run._results,
        )

    @classmethod
    def from_json(cls, run_json: Dict) -> "TestRun":
        return cattrs.structure(run_json, cls)


@define(auto_attribs=True)
class TestResult:
    FILTERED_MARKERS: ClassVar[List[str]] = ["parametrize"]
    # Convert the blocker category into an Ibutsu Classification
    BLOCKER_CATEGORY_TO_CLASSIFICATION: ClassVar[Dict[str, str]] = {
        "needs-triage": "needs_triage",
        "automation-issue": "test_failure",
        "environment-issue": "environment_failure",
        "product-issue": "product_failure",
        "product-rfe": "product_rfe",
    }

    test_id: str
    component: Optional[str] = None
    env: Optional[str] = None
    result: str = "passed"
    id: str = field(factory=lambda: str(uuid.uuid4()))
    metadata: Dict = field(factory=dict)
    params: Dict = field(factory=dict)
    run_id: Optional[str] = None
    source: str = "local"
    start_time: str = ""
    duration: float = 0.0
    _artifacts: Dict[str, Union[bytes, str]] = field(factory=dict)
    # TODO backwards compatibility
    _data: Dict = field(factory=dict)

    def __getitem__(self, key):
        # TODO backwards compatibility
        warnings.warn(
            f'_ibutsu["{key}"] will be deprecated in pytest-ibutsu 3.0. '
            "Please use a corresponding TestResult field.",
            DeprecationWarning,
        )
        return self._data[key]

    def __setitem__(self, key, value):
        # TODO backwards compatibility
        warnings.warn(
            f'_ibutsu["{key}"] will be deprecated in pytest-ibutsu 3.0. '
            "Please use a corresponding TestResult field.",
            DeprecationWarning,
        )
        self._data[key] = value

    def get(self, key: str, default=None):
        # TODO backwards compatibility
        warnings.warn(
            f'_ibutsu.get("{key}") will be deprecated in pytest-ibutsu 3.0. '
            "Please use a corresponding TestResult field.",
            DeprecationWarning,
        )
        return self._data.get(key, default)

    @staticmethod
    def _get_item_params(item: pytest.Item) -> Dict:
        def get_name(obj):
            return getattr(obj, "_param_name", None) or getattr(obj, "name", None) or str(obj)

        try:
            params = item.callspec.params.items()  # type: ignore[attr-defined]
            return {p: get_name(v) for p, v in params}
        except Exception:
            return {}

    @staticmethod
    def _get_item_fspath(item: pytest.Item) -> str:
        fspath = item.location[0] or item.fspath.strpath
        if "site-packages/" in fspath:
            fspath = fspath[fspath.find("site-packages/") + 14 :]
        return fspath

    @staticmethod
    def _get_item_markers(item: pytest.Item) -> List[ItemMarker]:
        return [
            {"name": m.name, "args": m.args, "kwargs": m.kwargs}
            for m in item.iter_markers()
            if m.name not in TestResult.FILTERED_MARKERS
        ]

    @staticmethod
    def _get_test_idents(item: pytest.Item) -> str:
        try:
            return item.location[2]
        except AttributeError:
            try:
                return item.fspath.strpath
            except AttributeError:
                return item.name

    @classmethod
    def from_item(cls, item: pytest.Item) -> "TestResult":
        from .pytest_plugin import ibutsu_plugin_key

        ibutsu_plugin = item.config.stash[ibutsu_plugin_key]
        return cls(
            test_id=cls._get_test_idents(item),
            params=cls._get_item_params(item),
            source=ibutsu_plugin.ibutsu_source,
            run_id=ibutsu_plugin.run.id,
            metadata={
                "statuses": {},
                "run": ibutsu_plugin.run.id,
                "durations": {},
                "fspath": cls._get_item_fspath(item),
                "markers": cls._get_item_markers(item),
                "project": ibutsu_plugin.ibutsu_project,
                "node_id": item.nodeid,
                **ibutsu_plugin.run.metadata,
            },
        )

    @classmethod
    def from_json(cls, result_json: Dict) -> "TestResult":
        return cattrs.structure(result_json, cls)

    def _get_xfail_reason(self, report: pytest.TestReport) -> Optional[str]:
        xfail_reason = None
        if self.metadata.get("markers"):
            for marker in self.metadata["markers"]:
                if marker.get("name") == "xfail":
                    xfail_reason = marker["kwargs"].get("reason")
        else:
            xfail_reason = report.wasxfail.split("reason: ")[1]
        return xfail_reason

    def _get_skip_reason(self, report: pytest.TestReport) -> Optional[str]:
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
                if report.longrepr and isinstance(report.longrepr, tuple):
                    skip_reason = report.longrepr[2].split("Skipped: ")[1]
            except IndexError:
                pass
        return skip_reason

    def set_metadata_reason(self, report: pytest.TestReport) -> None:
        if self.result == "skipped" and not self.metadata.get("skip_reason"):
            reason = self._get_skip_reason(report)
            if reason:
                self.metadata["skip_reason"] = reason
        elif self.result == "xfailed":
            reason = self._get_xfail_reason(report)
            if reason:
                self.metadata["xfail_reason"] = reason

    def set_metadata_statuses(self, report: pytest.TestReport) -> None:
        xfail = hasattr(report, "wasxfail")
        self.metadata["statuses"][report.when] = (report.outcome, xfail)

    def set_metadata_durations(self, report: pytest.TestReport) -> None:
        self.metadata["durations"][report.when] = report.duration

    def set_metadata_user_properties(self, report: pytest.TestReport) -> None:
        self.metadata["user_properties"] = dict(report.user_properties)

    @staticmethod
    def _get_classification(reason: str) -> Optional[str]:
        """Get the skip/xfail classification and category from the reason"""
        try:
            category = reason.split("category:")[1].strip()
            return TestResult.BLOCKER_CATEGORY_TO_CLASSIFICATION.get(category)
        except IndexError:
            return None

    def set_metadata_classification(self) -> None:
        reason = self.metadata.get("skip_reason") or self.metadata.get("xfail_reason")
        if reason:
            classification = self._get_classification(reason)
            if classification:
                self.metadata["classification"] = classification

    def set_result(self) -> None:
        """Handle some logic for when to count certain tests as which state"""
        statuses = self.metadata["statuses"]
        for when, status in statuses.items():
            if (when == "call" or when == "setup") and status[1] and status[0] == "skipped":
                self.result = "xfailed"
                break
            elif when == "call" and status[1] and status[0] == "passed":
                self.result = "xpassed"
                break
            elif (when == "setup" or when == "teardown") and status[0] == "failed":
                self.result = "error"
                break
            elif status[0] == "skipped":
                self.result = "skipped"
                break
            elif when == "call" and status[0] == "failed":
                self.result = "failed"
                break
            elif status[0] == "manual":
                self.result = "manual"
                break
            elif status[0] == "blocked":
                self.result = "blocked"
                break

    def set_duration(self) -> None:
        self.duration = sum(self.metadata.get("durations", {}).values())

    def set_metadata_short_tb(
        self,
        call: pytest.CallInfo,
        report: Union[pytest.CollectReport, pytest.TestReport],
    ) -> None:
        if not isinstance(call.excinfo, ExceptionInfo):
            return
        val = _safe_string(call.excinfo.value)
        last_lines = "\n".join(report.longreprtext.split("\n")[-4:])
        short_tb = "{}\n{}\n{}".format(
            last_lines, call.excinfo.type.__name__, val.encode("ascii", "xmlcharrefreplace")
        )
        self.metadata["short_tb"] = short_tb

    def set_metadata_exception_name(self, call: pytest.CallInfo) -> None:
        if isinstance(call.excinfo, ExceptionInfo):
            self.metadata["exception_name"] = call.excinfo.type.__name__

    def attach_artifact(self, name: str, content: Union[bytes, str]) -> None:
        self._artifacts[name] = content

    def to_dict(self) -> Dict:
        return asdict(
            self,
            filter=lambda attr, _: not attr.name.startswith("_"),
            value_serializer=_serializer,
        )
