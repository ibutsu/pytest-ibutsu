from __future__ import annotations

import os
import time
import uuid
import logging
from datetime import datetime, UTC
from typing import Any
from typing import ClassVar
from typing import Mapping
from typing import TypedDict
import types

from cattrs.preconf.json import make_converter as make_json_converter
from cattrs.gen import make_dict_unstructure_fn, override
from attrs import has, fields
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


class ItemMarker(TypedDict):
    name: str
    args: tuple[Any, ...]
    kwargs: Mapping[str, Any]


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

    def increment(self, test_result: IbutsuTestResult) -> None:
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
    def from_results(cls, results: list[IbutsuTestResult]) -> Summary:
        summary = cls()
        for result in results:
            summary.increment(result)
        summary.collected = len(results)
        return summary


@attrs.define(auto_attribs=True)
class IbutsuTestRun:
    component: str | None = None
    env: str | None = None
    id: str = attrs.field(factory=lambda: str(uuid.uuid4()))
    metadata: dict[str, Any] = attrs.field(factory=dict)
    source: str | None = None
    start_time: str = ""
    duration: float = 0.0
    _results: list[IbutsuTestResult] = attrs.field(factory=list)
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
        """Convert IbutsuTestRun to dictionary for JSON serialization.

        Private attributes (starting with '_') are automatically excluded.
        This is a convenience wrapper around cattrs unstructure.

        Returns:
            dict: JSON-serializable dictionary representation
        """
        result = ibutsu_converter.unstructure(self)
        assert isinstance(result, dict)
        return result

    @staticmethod
    def get_metadata(runs: list[IbutsuTestRun]) -> dict[str, Any]:
        metadata = {}
        for run in runs:
            metadata.update(run.metadata)
        return metadata

    @classmethod
    def from_xdist_test_runs(cls, runs: list[IbutsuTestRun]) -> IbutsuTestRun:
        first_run = runs[0]
        results = []
        for run in runs:
            for result in run._results:
                result.run_id = first_run.id
                result.metadata["run"] = first_run.id
                results.append(result)
        return IbutsuTestRun(
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
    def from_sequential_test_runs(cls, runs: list[IbutsuTestRun]) -> IbutsuTestRun:
        latest_run = max(runs, key=lambda run: run.start_time)
        return IbutsuTestRun(
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
    def from_json(cls, run_json: dict[str, Any]) -> IbutsuTestRun:
        """Create IbutsuTestRun from JSON dictionary.

        This is a convenience wrapper around cattrs structure.

        Args:
            run_json: Dictionary representation from JSON

        Returns:
            IbutsuTestRun: Reconstructed instance
        """
        return ibutsu_converter.structure(run_json, cls)


@attrs.define(auto_attribs=True)
class IbutsuTestResult:
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
        except AttributeError:
            return {}
        except Exception as e:
            log.debug("%s %s", item, e)
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
            if m.name not in IbutsuTestResult.FILTERED_MARKERS
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
    def from_item(cls, item: pytest.Item) -> IbutsuTestResult:
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
    def from_json(cls, result_json: dict[str, Any]) -> IbutsuTestResult:
        """Create IbutsuTestResult from JSON dictionary.

        This is a convenience wrapper around cattrs structure.

        Args:
            result_json: Dictionary representation from JSON

        Returns:
            IbutsuTestResult: Reconstructed instance
        """
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
            return IbutsuTestResult.BLOCKER_CATEGORY_TO_CLASSIFICATION.get(category)
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
        val = _simple_unstructure_hook(call.excinfo.value)
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
        """Convert IbutsuTestResult to dictionary for JSON serialization.

        Private attributes (starting with '_') are automatically excluded.
        This is a convenience wrapper around cattrs unstructure.

        Returns:
            dict: JSON-serializable dictionary representation
        """
        result = ibutsu_converter.unstructure(self)
        assert isinstance(result, dict)
        return result


def _simple_unstructure_hook(obj: Any) -> str:
    """Simple unstructure hook that converts any non-serializable object to its string representation.

    This prioritizes using obj.__class__.__name__ first, then considers other dunders
    with __name__ available for non-serializable types.

    Args:
        obj: Any Python object that needs to be unstructured

    Returns:
        String representation of the object
    """
    try:
        return str(obj)

    except Exception:
        # If accessing class name fails, try repr() as fallback
        try:
            return repr(obj)
        except Exception:
            # Absolute last resort - use object id
            return f"<object at {hex(id(obj))}>"


def _is_non_serializable_type(cls: type) -> bool:
    """Predicate to identify non-serializable types that should be converted to strings.

    This replaces the manual list with automatic detection based on type characteristics.
    """
    # Check for descriptor types
    if isinstance(cls, type) and issubclass(cls, (property, classmethod, staticmethod)):
        return True

    # Check for specific non-serializable types using their module and name
    non_serializable_names = {
        "builtin_function_or_method",
        "method",
        "function",
        "member_descriptor",
        "method_descriptor",
        "wrapper_descriptor",
        "getset_descriptor",
        "classmethod_descriptor",
    }

    return hasattr(cls, "__name__") and cls.__name__ in non_serializable_names


def _create_attrs_unstructure_hook_factory(converter: Any) -> Any:
    """Create a factory function for attrs unstructure hooks."""

    def attrs_unstructure_hook_factory(cls: type) -> Any:
        def unstructure_hook(instance: Any) -> dict[str, Any]:
            # Use the standard cattrs unstructure but filter out private fields
            full_dict = make_dict_unstructure_fn(cls, converter)(instance)
            # Filter to only include public attrs fields (not starting with '_')
            attrs_fields = attrs.fields_dict(cls)
            return {
                k: v
                for k, v in full_dict.items()
                if k in attrs_fields and not k.startswith("_")
            }

        return unstructure_hook

    return attrs_unstructure_hook_factory


def _configure_converter(converter: Any) -> None:
    """Configure the converter with comprehensive hook registration.

    This configures both generic hook factories and specific class hooks in one place.
    """
    from cattrs.gen import make_dict_unstructure_fn

    # Register hook factory for attrs classes that excludes private attributes
    converter.register_unstructure_hook_factory(
        has,  # Predicate: any attrs class
        _create_attrs_unstructure_hook_factory(converter),
    )

    # Register hook factory for non-serializable types, non-attrs
    converter.register_unstructure_hook_factory(
        _is_non_serializable_type, lambda cls: _simple_unstructure_hook
    )

    # Register hooks for additional problematic types that commonly appear in metadata
    converter.register_unstructure_hook(BaseException, _simple_unstructure_hook)
    converter.register_unstructure_hook(type, _simple_unstructure_hook)
    converter.register_unstructure_hook(types.ModuleType, _simple_unstructure_hook)

    # Register hook factory for all type subclasses (metaclasses)
    def _is_metaclass(cls: type) -> bool:
        return isinstance(cls, type) and issubclass(cls, type) and cls is not type

    converter.register_unstructure_hook_factory(
        _is_metaclass, lambda cls: _simple_unstructure_hook
    )

    # Register hook factory for custom class instances (catch-all for user-defined classes)
    def _is_custom_class_instance(cls: type) -> bool:
        """Detect custom class instances that should be serialized as strings.

        This catches user-defined classes that don't have specific hooks.
        """
        # Skip built-in types, standard library types, and types already handled
        if cls.__module__ in ("builtins", "__main__"):
            return False
        if cls.__module__.startswith(("collections", "typing", "json", "datetime")):
            return False
        if has(cls):  # Skip attrs classes (handled by factory)
            return False
        # Skip union types and generic types which should be handled by cattrs
        if hasattr(cls, "__origin__") or hasattr(cls, "__args__"):
            return False
        # Skip types that don't have a proper __name__ (like union types)
        if not hasattr(cls, "__name__") or "|" in str(cls):
            return False
        # Catch custom classes that define __str__
        return hasattr(cls, "__str__") and getattr(cls, "__str__") is not object.__str__

    converter.register_unstructure_hook_factory(
        _is_custom_class_instance, lambda cls: _simple_unstructure_hook
    )

    # Register specific hooks for main classes (higher precedence than factory hooks)
    # Following the pattern from https://catt.rs/en/stable/usage.html#using-factory-hooks
    for cls in [IbutsuTestRun, IbutsuTestResult]:
        overrides: dict[str, Any] = {}
        for f in fields(cls):
            # Omit private fields entirely, even if they are attr fields
            if f.name.startswith("_"):
                overrides[f.name] = override(omit=True)
            else:
                overrides[f.name] = override()

        converter.register_unstructure_hook(
            cls,
            make_dict_unstructure_fn(cls, converter, **overrides),
        )


# noinspection PyArgumentList
ibutsu_converter = make_json_converter()
# Configure comprehensive cattrs integration with hook factories and specific class hooks
_configure_converter(ibutsu_converter)
