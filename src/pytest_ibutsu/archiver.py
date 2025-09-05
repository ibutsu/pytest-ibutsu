from __future__ import annotations

import json
import logging
import tarfile
import time
from contextlib import AbstractContextManager
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .pytest_plugin import IbutsuPlugin


from .modeling import TestResult, TestRun, ibutsu_converter, _json_serializer

logger = logging.getLogger(__name__)


class IbutsuArchiver(AbstractContextManager["IbutsuArchiver"]):
    def __init__(self, name: str) -> None:
        self.name = name

    def add_dir(self, path: str) -> None:
        tar_info = tarfile.TarInfo(path)
        tar_info.mtime = int(time.time())
        tar_info.type = tarfile.DIRTYPE
        tar_info.mode = 33755
        self.tar.addfile(tar_info)

    def add_file(self, path: str, content: bytes) -> None:
        tar_info = tarfile.TarInfo(path)
        tar_info.mtime = int(time.time())
        tar_info.mode = 33184
        tar_info.size = len(content)
        self.tar.addfile(tar_info, fileobj=BytesIO(content))

    @staticmethod
    def _get_bytes(value: bytes | str) -> bytes:
        return value if isinstance(value, bytes) else Path(value).read_bytes()

    def add_result(self, run: TestRun, result: TestResult) -> None:
        self.add_dir(f"{run.id}/{result.id}")
        # Use cattrs converter for robust serialization with fallback protection
        try:
            # First, use cattrs to unstructure the result directly
            unstructured_result = ibutsu_converter.unstructure(result)
            # Filter out private attributes
            filtered_result = {
                k: v for k, v in unstructured_result.items() if not k.startswith("_")
            }
            content = json.dumps(filtered_result).encode("utf-8")
        except (TypeError, ValueError) as e:
            # Fallback: use to_dict() with custom serializer
            try:
                content = json.dumps(result.to_dict(), default=_json_serializer).encode(
                    "utf-8"
                )
            except Exception as fallback_error:
                # Last resort: log the error and use string representation
                logger.exception(
                    f"Failed to serialize TestResult {result.id}: {e}, fallback error: {fallback_error}"
                )
                content = json.dumps(
                    {"error": "serialization_failed", "result_id": result.id}
                ).encode("utf-8")

        self.add_file(f"{run.id}/{result.id}/result.json", content)
        for name, value in result._artifacts.items():
            try:
                content = self._get_bytes(value)
            except (FileNotFoundError, IsADirectoryError):
                continue
            self.add_file(f"{run.id}/{result.id}/{name}", content)

    def add_run(self, run: TestRun) -> None:
        self.add_dir(run.id)
        # Use cattrs converter for robust serialization with fallback protection
        try:
            # First, use cattrs to unstructure the run directly
            unstructured_run = ibutsu_converter.unstructure(run)
            # Filter out private attributes
            filtered_run = {
                k: v for k, v in unstructured_run.items() if not k.startswith("_")
            }
            content = json.dumps(filtered_run).encode("utf-8")
        except (TypeError, ValueError) as e:
            # Fallback: use to_dict() with custom serializer
            try:
                content = json.dumps(run.to_dict(), default=_json_serializer).encode(
                    "utf-8"
                )
            except Exception as fallback_error:
                # Last resort: log the error and use string representation
                logger.exception(
                    f"Failed to serialize TestRun {run.id}: {e}, fallback error: {fallback_error}"
                )
                content = json.dumps(
                    {"error": "serialization_failed", "run_id": run.id}
                ).encode("utf-8")

        self.add_file(f"{run.id}/run.json", content)
        for name, value in run._artifacts.items():
            try:
                content = self._get_bytes(value)
            except (FileNotFoundError, IsADirectoryError):
                continue
            self.add_file(f"{run.id}/{name}", content)

    def __enter__(self) -> IbutsuArchiver:
        self.tar = tarfile.open(f"{self.name}.tar.gz", "w:gz")
        return self

    def __exit__(self, *exc_details: Any) -> None:
        self.tar.close()


def dump_to_archive(ibutsu_plugin: IbutsuPlugin) -> None:
    with IbutsuArchiver(ibutsu_plugin.run.id) as ibutsu_archiver:
        ibutsu_archiver.add_run(ibutsu_plugin.run)
        for result in ibutsu_plugin.results.values():
            ibutsu_archiver.add_result(ibutsu_plugin.run, result)
    message = f"\nPytest-Ibutsu: Saved results archive to {ibutsu_archiver.name}.tar.gz"
    logger.info(message)
    print(message)
