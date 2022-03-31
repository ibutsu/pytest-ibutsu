import json
import tarfile
import time
from contextlib import AbstractContextManager
from io import BytesIO
from typing import Iterable
from typing import Union

from attr import asdict

from .modeling import TestResult
from .modeling import TestRun


class IbutsuArchiver(AbstractContextManager):
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
    def _get_bytes(value: Union[bytes, str]) -> bytes:
        if isinstance(value, bytes):
            return value
        with open(value, "rb") as f:
            return f.read()

    def add_result(self, run: TestRun, result: TestResult) -> None:
        self.add_dir(f"{run.id}/{result.id}")
        # convert to dictionary only public fields
        unstructured = asdict(result, filter=lambda attr, _: not attr.name.startswith("_"))
        content = bytes(json.dumps(unstructured), "utf-8")
        self.add_file(f"{run.id}/{result.id}/result.json", content)
        for name, value in result._artifacts.items():
            content = self._get_bytes(value)
            self.add_file(f"{run.id}/{result.id}/{name}", content)

    def add_run(self, run: TestRun) -> None:
        self.add_dir(run.id)
        # convert to dictionary only public fields
        unstructured = asdict(run, filter=lambda attr, _: not attr.name.startswith("_"))
        content = bytes(json.dumps(unstructured), "utf-8")
        self.add_file("run.json", content)

    def __enter__(self) -> "IbutsuArchiver":
        self.tar = tarfile.open(f"{self.name}.tar.gz", "w:gz")
        return self

    def __exit__(self, *exc_details) -> None:
        self.tar.close()
        print(f"Saved results archive to {self.name}.tar.gz")


def dump_archive(run: TestRun, results: Iterable[TestResult]) -> None:
    with IbutsuArchiver(run.id) as ibutsu_archiver:
        ibutsu_archiver.add_run(run)
        for result in results:
            ibutsu_archiver.add_result(run, result)