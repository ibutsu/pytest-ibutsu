import os
import time
import uuid
from dataclasses import dataclass
from dataclasses import field
from http.client import BadStatusLine
from http.client import RemoteDisconnected
from multiprocessing.pool import ApplyResult
from typing import List
from typing import Optional

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

CA_BUNDLE_ENVS = ["REQUESTS_CA_BUNDLE", "IBUTSU_CA_BUNDLE"]
# Maximum number of times an API call is retried
MAX_CALL_RETRIES = 3


class TooManyRetriesError(Exception):
    pass


@dataclass
class IbutsuApiServer:
    server_url: str
    _configuration: Configuration
    api_client: ApiClient
    result_api: ResultApi

    artifact_api: ArtifactApi
    run_api: RunApi
    health_api: HealthApi

    _has_server_error: bool = False
    _server_error_tbs: List[str] = field(default_factory=list)
    _sender_cache: List[ApplyResult] = field(default_factory=list)

    @classmethod
    def for_url(cls, server_url: str, token: Optional[str]):

        config = Configuration(host=server_url, access_token=token)
        # Only set the SSL CA cert if one of the environment variables is set
        for env_var in CA_BUNDLE_ENVS:
            if os.getenv(env_var, None):
                config.ssl_ca_cert = os.getenv(env_var)

        api_client = ApiClient(config)
        return cls(
            server_url=server_url,
            _configuration=config,
            api_client=api_client,
            result_api=ResultApi(api_client),
            artifact_api=ArtifactApi(api_client),
            run_api=RunApi(api_client),
            health_api=HealthApi(api_client),
        )

    @property
    def frontend(self):
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

    def shutdown(self):
        print(f"Ibutsu client finishing up...({len(self._sender_cache)} tasks left)...")
        while self._sender_cache:
            for res in self._sender_cache:
                if res.ready():
                    self._sender_cache.remove(res)
            time.sleep(0.1)
        print("Cleanup complete")

    def add_run(self, run: dict) -> Optional[dict]:
        server_run = self._make_call(self.run_api.add_run, run=run)
        assert server_run
        return server_run.to_dict()

    def refresh_run(self, run_id: str) -> Optional[dict]:
        fresh_run = self._make_call(self.run_api.get_run, run_id)
        if fresh_run:
            return fresh_run.to_dict()
        else:
            return None

    def update_run(self, run):
        self._make_call(self.run_api.update_run, run["id"], run=run)

    def add_result(self, result):
        server_result = self._make_call(self.result_api.add_result, result=result)
        if server_result:
            return server_result.to_dict()

    def update_result(self, id, result):
        self._make_call(self.result_api.update_result, id, result=result, async_req=True)

    def upload_artifact(self, id_, filename, data, is_run=False):

        with open(data, "rb") as file_content:
            try:
                if not file_content.closed:
                    if is_run:
                        # id_ is the run_id, we don't check the return_type because
                        # artifact.to_dict() in the controller contains a None value
                        self._make_call(
                            self.artifact_api.upload_artifact,
                            filename,
                            file_content,
                            run_id=id_,
                            _check_return_type=False,
                        )
                    else:
                        # id_ is the result_id, we don't check the return_type because
                        # artifact.to_dict() in the controller contains a None valued
                        self._make_call(
                            self.artifact_api.upload_artifact,
                            filename,
                            file_content,
                            result_id=id_,
                            _check_return_type=False,
                        )

            except ApiValueError:
                print(f"Uploading artifact '{filename}' failed as the file closed prematurely.")


class NoServer:

    frontend = "gotcha://not-here:1337"
    _has_server_error = True

    def shutdown(self):
        pass

    def add_run(self, run):
        assert "id" not in run
        run["id"] = str(uuid.uuid4())
        return run

    def update_run(self, run):
        return run

    def add_result(self, result):
        result_id = result.get("id")
        if not result_id:
            result_id = str(uuid.uuid4())
            result["id"] = result_id
        return result

    def update_result(self, id, result):
        return result

    def refresh_run(self, run):
        return None

    def upload_artifact(self, id_, filename, data, is_run=False):
        pass


def get_server(server_url: str, token: str | None) -> IbutsuApiServer | None:
    print(f"Ibutsu server: {server_url}")
    if server_url.endswith("/"):
        server_url = server_url[:-1]
    if not server_url.endswith("/api"):
        server_url += "/api"

    server = IbutsuApiServer.for_url(server_url, token=token)
    try:
        server.frontend
        return server
    except MaxRetryError:
        print("Connection failure in health check")
    except ApiException as e:
        if e.status == 401:
            print("authorization failed")

        else:
            print("Error", e.status, "in call to Ibutsu API")
        print(server_url)
        print(e.args)
        print(e.headers)
        print(e.body)
        raise
    server.shutdown()
    return None
