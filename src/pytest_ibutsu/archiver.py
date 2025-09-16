from __future__ import annotations

import logging
import tarfile
import time
import json
from contextlib import AbstractContextManager
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .pytest_plugin import IbutsuPlugin


from .modeling import IbutsuTestResult, IbutsuTestRun, ibutsu_converter

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

    def _serialize_add_artifacts(
        self,
        obj: IbutsuTestResult | IbutsuTestRun,
        base_path: str,
        json_filename: str,
    ) -> None:
        """
        Serialize an object (run or result) and its artifacts to the archive.

        Args:
            obj: The object to serialize (IbutsuTestResult or IbutsuTestRun)
            base_path: The base path in the archive for this object
            json_filename: The filename for the JSON data (e.g., "result.json", "run.json")
        """
        # Serialize the main object data
        try:
            # Use cattrs to unstructure the object directly
            unstructured_data = ibutsu_converter.unstructure(obj)
            # Use standard JSON since data is already unstructured
            content = json.dumps(unstructured_data).encode("utf-8")
        except Exception as e:
            # Last resort: log the error and use error representation
            obj_id = obj.id
            logger.exception(
                f"Failed to serialize {obj.__class__.__name__} {obj_id}: {e}"
            )

            id_key = "result_id" if isinstance(obj, IbutsuTestResult) else "run_id"
            content = json.dumps(
                {"error": "serialization_failed", id_key: obj_id}
            ).encode("utf-8")

        self.add_file(f"{base_path}/{json_filename}", content)

        # Process and add artifacts
        for name, value in obj._artifacts.items():
            try:
                artifact_content = self._get_bytes(value)
            except (FileNotFoundError, IsADirectoryError):
                continue
            self.add_file(f"{base_path}/{name}", artifact_content)

    def add_result(self, run: IbutsuTestRun, result: IbutsuTestResult) -> None:
        base_path = f"{run.id}/{result.id}"
        self.add_dir(base_path)
        self._serialize_add_artifacts(result, base_path, "result.json")

    def add_run(self, run: IbutsuTestRun) -> None:
        base_path = run.id
        self.add_dir(base_path)
        self._serialize_add_artifacts(run, base_path, "run.json")

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

    # Update summary info for terminal output
    archive_path = f"{ibutsu_archiver.name}.tar.gz"
    ibutsu_plugin.summary_info["archive_created"] = True
    ibutsu_plugin.summary_info["archive_path"] = archive_path
