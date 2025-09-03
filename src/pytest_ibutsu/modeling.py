from __future__ import annotations

import json
import os
import time
import uuid
import logging
from datetime import datetime, UTC
from typing import Any
from typing import ClassVar
from typing import Mapping
from typing import TypedDict
from types import FunctionType

from cattrs.preconf.json import make_converter as make_json_converter
import pytest

import attrs
from pytest import ExceptionInfo

log = logging.getLogger(__name__)


def validate_uuid_string(uuid_string: str) -> bool:
    """Validate if a string is a proper UUID format."""
    try:
        uuid.UUID(uuid_string)
        return True
    except ValueError:
        return False


# noinspection PyArgumentList
ibutsu_converter = make_json_converter()
# we need this due to broken structure - replace wit tagged union and/or consistent handling
ibutsu_converter.register_structure_hook(str | bytes, lambda o, _: o)


class ItemMarker(TypedDict):
    name: str
    args: tuple[Any, ...]
    kwargs: Mapping[str, Any]


def _safe_string(obj: object) -> str:
    """This will make string out of ANYTHING without having to worry about the stupid Unicode errors

    This function tries to make str/unicode out of ``obj`` unless it already is one of those and
    then it processes it so in the end there is a harmless ascii string.
    """
    if not isinstance(obj, str):
        obj = str(obj)
    if isinstance(obj, bytes):
        obj = obj.decode("utf-8", "ignore")
    return obj.encode("ascii", "xmlcharrefreplace").decode("ascii")


def _json_serializer(obj: object | FunctionType) -> str:
    if callable(obj) and hasattr(obj, "__code__"):
        return f"function: '{obj.__name__}', args: {str(obj.__code__.co_varnames)}"
    else:
        return str(obj)


# todo: replace this by a more controlled version
def _serializer(inst: type, field: attrs.Attribute[Any], value: Any) -> Any:
    if field and field.name == "metadata":
        return json.loads(json.dumps(value, default=_json_serializer))
    else:
        return value


@attrs.define(auto_attribs=True)
class Summary:
    failures: int = 0
    errors: int = 0
    xfailures: int = 0
    xpasses: int = 0
    skips: int = 0
    tests: int = 0
    collected: int = 0
    not_run: int = 0

    def increment(self, test_result: TestResult) -> None:
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
    def from_results(cls, results: list[TestResult]) -> Summary:
        summary = cls()
        for result in results:
            summary.increment(result)
        summary.collected = len(results)
        return summary


@attrs.define(auto_attribs=True)
class TestRun:
    component: str | None = None
    env: str | None = None
    id: str = attrs.field(factory=lambda: str(uuid.uuid4()))
    metadata: dict[str, Any] = attrs.field(factory=dict)
    source: str | None = None
    start_time: str = ""
    duration: float = 0.0
    _results: list[TestResult] = attrs.field(factory=list)
    _start_unix_time: float = attrs.field(init=False, default=0.0)
    _artifacts: dict[str, bytes | str] = attrs.field(factory=dict)
    summary: Summary = attrs.field(factory=Summary)

    def __attrs_post_init__(self) -> None:
        if os.getenv("JOB_NAME") and os.getenv("BUILD_NUMBER"):
            self.metadata["jenkins"] = {
                "job_name": os.getenv("JOB_NAME"),
                "build_number": os.getenv("BUILD_NUMBER"),
                "build_url": os.getenv("BUILD_URL"),
            }
        if os.getenv("IBUTSU_ENV_ID"):
            self.metadata["env_id"] = os.getenv("IBUTSU_ENV_ID")

    def start_timer(self) -> None:
        self._start_unix_time = time.time()
        self.start_time = datetime.now(UTC).isoformat()

    def set_duration(self) -> None:
        if self._start_unix_time:
            self.duration = time.time() - self._start_unix_time

    def attach_artifact(self, name: str, content: bytes | str) -> None:
        self._artifacts[name] = content

    def to_dict(self) -> dict[str, Any]:
        return attrs.asdict(
            self,
            filter=lambda attr, _: not attr.name.startswith("_"),
            value_serializer=_serializer,  # type: ignore[call-arg]
        )

    @staticmethod
    def get_metadata(runs: list[TestRun]) -> dict[str, Any]:
        metadata = {}
        for run in runs:
            metadata.update(run.metadata)
        return metadata

    @classmethod
    def from_xdist_test_runs(cls, runs: list[TestRun]) -> TestRun:
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
    def from_sequential_test_runs(cls, runs: list[TestRun]) -> TestRun:
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
    def from_json(cls, run_json: dict[str, Any]) -> TestRun:
        return ibutsu_converter.structure(run_json, cls)


@attrs.define(auto_attribs=True)
class TestResult:
    FILTERED_MARKERS: ClassVar[list[str]] = ["parametrize"]
    # Convert the blocker category into an Ibutsu Classification
    BLOCKER_CATEGORY_TO_CLASSIFICATION: ClassVar[dict[str, str]] = {
        "needs-triage": "needs_triage",
        "automation-issue": "test_failure",
        "environment-issue": "environment_failure",
        "product-issue": "product_failure",
        "product-rfe": "product_rfe",
    }

    test_id: str
    component: str | None = None
    env: str | None = None
    result: str = "passed"
    id: str = attrs.field(factory=lambda: str(uuid.uuid4()))
    metadata: dict[str, Any] = attrs.field(factory=dict)
    params: dict[str, Any] = attrs.field(factory=dict)
    run_id: str | None = None
    source: str = "local"
    start_time: str = ""
    duration: float = 0.0
    _artifacts: dict[str, bytes | str] = attrs.field(factory=dict)

    @staticmethod
    def _get_item_params(item: pytest.Item) -> dict[str, Any]:
        def get_name(obj: object) -> str:
            return (
                getattr(obj, "_param_name", None)
                or getattr(obj, "name", None)
                or str(obj)
            )

        try:
            params = item.callspec.params.items()  # type: ignore[attr-defined]
            return {p: get_name(v) for p, v in params}
        except Exception as e:
            log.warning("%s %s", item, e)
            return {}

    @staticmethod
    def _get_item_fspath(item: pytest.Item) -> str:
        fspath = item.location[0] or str(item.path)
        return fspath.split("site-packages/", 1)[-1]

    @staticmethod
    def _get_item_markers(item: pytest.Item) -> list[ItemMarker]:
        return [
            ItemMarker(name=m.name, args=m.args, kwargs=m.kwargs)
            for m in item.iter_markers()
            if m.name not in TestResult.FILTERED_MARKERS
        ]

    @staticmethod
    def _get_test_idents(item: pytest.Item) -> str:
        try:
            return item.location[2]
        except AttributeError:
            try:
                return str(item.path)
            except AttributeError:
                return item.name

    @classmethod
    def from_item(cls, item: pytest.Item) -> TestResult:
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
    def from_json(cls, result_json: dict[str, Any]) -> TestResult:
        return ibutsu_converter.structure(result_json, cls)

    def _get_xfail_reason(self, report: pytest.TestReport) -> str | None:
        xfail_reason = None
        if self.metadata.get("markers"):
            for marker in self.metadata["markers"]:
                if marker.get("name") == "xfail":
                    xfail_reason = marker["kwargs"].get("reason")
        else:
            xfail_reason = report.wasxfail.split("reason: ")[1]
        return xfail_reason

    def _get_skip_reason(self, report: pytest.TestReport) -> str | None:
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
    def _get_classification(reason: str) -> str | None:
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
            if (
                (when == "call" or when == "setup")
                and status[1]
                and status[0] == "skipped"
            ):
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
        call: pytest.CallInfo[None],
        report: pytest.CollectReport | pytest.TestReport,
    ) -> None:
        if not isinstance(call.excinfo, ExceptionInfo):
            return
        val = _safe_string(call.excinfo.value)
        last_lines = "\n".join(report.longreprtext.split("\n")[-4:])
        # todo - determine if we should use normal repr
        short_tb = "{}\n{}\n{!r}".format(
            last_lines,
            call.excinfo.type.__name__,
            val.encode("ascii", "xmlcharrefreplace"),
        )
        self.metadata["short_tb"] = short_tb

    def set_metadata_exception_name(self, call: pytest.CallInfo[None]) -> None:
        if isinstance(call.excinfo, ExceptionInfo):
            self.metadata["exception_name"] = call.excinfo.type.__name__

    def attach_artifact(self, name: str, content: bytes | str) -> None:
        self._artifacts[name] = content

    def to_dict(self) -> dict[str, Any]:
        return attrs.asdict(
            self,
            filter=lambda attr, _: not attr.name.startswith("_"),
            value_serializer=_serializer,  # type: ignore[call-arg]
        )
