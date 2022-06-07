from __future__ import annotations

import os
from http.client import BadStatusLine
from http.client import RemoteDisconnected
from io import BufferedReader
from io import BytesIO
from typing import TYPE_CHECKING

from ibutsu_client import ApiClient
from ibutsu_client import ApiException
from ibutsu_client import Configuration
from ibutsu_client.api.artifact_api import ArtifactApi
from ibutsu_client.api.health_api import HealthApi
from ibutsu_client.api.result_api import ResultApi
from ibutsu_client.api.run_api import RunApi
from ibutsu_client.exceptions import ApiValueError
from urllib3.exceptions import MaxRetryError
from urllib3.exceptions import ProtocolError

from .modeling import TestResult
from .modeling import TestRun


if TYPE_CHECKING:
    from .pytest_plugin import IbutsuPlugin


# Place a limit on the file-size we can upload for artifacts
UPLOAD_LIMIT = 5 * 1024 * 1024  # 5 MiB

# Maximum number of times an API call is retried
MAX_CALL_RETRIES = 3

CA_BUNDLE_ENVS = ["REQUESTS_CA_BUNDLE", "IBUTSU_CA_BUNDLE"]


class TooManyRetriesError(Exception):
    pass


class IbutsuSender:
    def __init__(self, server_url: str, token: str | None = None):
        self._has_server_error = False
        self._server_error_tbs: list[str] = []
        self._sender_cache = []  # type: ignore
        config = Configuration(access_token=token, host=server_url)
        # Only set the SSL CA cert if one of the environment variables is set
        for env_var in CA_BUNDLE_ENVS:
            if os.getenv(env_var, None):
                config.ssl_ca_cert = os.getenv(env_var)
        api_client = ApiClient(config)
        self.result_api = ResultApi(api_client)
        self.artifact_api = ArtifactApi(api_client)
        self.run_api = RunApi(api_client)
        self.health_api = HealthApi(api_client)

    @classmethod
    def from_ibutsu_plugin(cls, ibutsu: IbutsuPlugin) -> IbutsuSender:
        print(f"Ibutsu server: {ibutsu.ibutsu_server}")
        ibutsu_server = ibutsu.ibutsu_server
        if ibutsu.ibutsu_server.endswith("/"):
            ibutsu_server = ibutsu_server[:-1]
        if not ibutsu.ibutsu_server.endswith("/api"):
            ibutsu_server += "/api"
        return cls(server_url=ibutsu_server, token=ibutsu.ibutsu_token)

    @property
    def frontend_url(self) -> str:
        return self.health_api.get_health_info().frontend

    def _make_call(self, api_method, *args, **kwargs):
        for res in self._sender_cache:
            if res.ready():
                self._sender_cache.remove(res)
        try:
            retries = 0
            while retries < MAX_CALL_RETRIES:
                try:
                    out = api_method(*args, **kwargs)
                    if "async_req" in kwargs:
                        self._sender_cache.append(out)
                    return out
                except (RemoteDisconnected, ProtocolError, BadStatusLine):
                    retries += 1
            raise TooManyRetriesError("Too many retries while trying to call API")
        except (MaxRetryError, ApiException, TooManyRetriesError) as e:
            self._has_server_error = self._has_server_error or True
            self._server_error_tbs.append(str(e))
            return None

    @staticmethod
    def _get_buffered_reader(data: bytes | str, filename: str) -> tuple[BufferedReader, int]:
        if isinstance(data, bytes):
            io_bytes = BytesIO(data)
            io_bytes.name = filename
            payload = BufferedReader(io_bytes)  # type: ignore
            return payload, len(data)
        return open(data, "rb"), os.stat(data).st_size

    def add_run(self, run: TestRun):
        self._make_call(self.run_api.add_run, run=run.to_dict())
        # Ibutsu server awkwardness. If we don't update run with the same data it will be
        # incomplete.
        self._make_call(self.run_api.update_run, run.id, run=run.to_dict())

    def add_result(self, result: TestResult):
        self._make_call(self.result_api.add_result, result=result.to_dict())
        for filename, data in result._artifacts.items():
            self.upload_artifact(result.id, filename, data)

    def upload_artifact(self, id_, filename, data, is_run=False):
        kwargs = {"run_id": id_} if is_run else {"result_id": id_}
        buffered_reader, payload_size = self._get_buffered_reader(data, filename)
        if payload_size < UPLOAD_LIMIT:
            try:
                self._make_call(
                    self.artifact_api.upload_artifact,
                    filename,
                    buffered_reader,
                    _check_return_type=False,
                    **kwargs,
                )
            except ApiValueError:
                print(f"Uploading artifact '{filename}' failed as the file closed prematurely.")
        else:
            print("Artifact size is greater than upload limit")
        buffered_reader.close()


def send_data_to_ibutsu(ibutsu_plugin: IbutsuPlugin) -> None:
    sender = IbutsuSender.from_ibutsu_plugin(ibutsu_plugin)
    sender.add_run(ibutsu_plugin.run)
    for result in ibutsu_plugin.results.values():
        sender.add_result(result)
    if not sender._has_server_error:
        print(f"Results can be viewed on: {sender.frontend_url}/runs/{ibutsu_plugin.run.id}")
