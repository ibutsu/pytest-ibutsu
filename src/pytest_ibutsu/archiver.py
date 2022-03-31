import json
import tarfile
import time
from contextlib import AbstractContextManager
from io import BytesIO
from typing import Iterable

from attr import asdict

from .modeling import TestResult
from .modeling import TestRun


class IbutsuArchive(AbstractContextManager):
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

    def add_result(self, run: TestRun, result: TestResult) -> None:
        self.add_dir(f"{run.id}/{result.id}")
        content = bytes(json.dumps(asdict(result)), "utf-8")
        self.add_file(f"{run.id}/{result.id}/result.json", content)

    def add_run(self, run: TestRun) -> None:
        self.add_dir(run.id)
        content = bytes(json.dumps(asdict(run)), "utf-8")
        self.add_file("run.json", content)

    def __enter__(self) -> "IbutsuArchive":
        self.tar = tarfile.open(f"{self.name}.tar.gz", "w:gz")
        return self

    def __exit__(self, *exc_details) -> None:
        self.tar.close()
        print(f"Saved results archive to {self.name}.tar.gz")


def dump_archive(run: TestRun, results: Iterable[TestResult]) -> None:
    with IbutsuArchive(run.id) as ibutsu_archive:
        ibutsu_archive.add_run(run)
        for result in results:
            ibutsu_archive.add_result(run, result)
