from __future__ import annotations

import logging
import os
import time
from functools import cached_property
from http.client import BadStatusLine
from http.client import RemoteDisconnected
from pathlib import Path
from typing import TYPE_CHECKING, Callable, cast, Any
from typing import TypeVar, ParamSpec

from ibutsu_client.api_client import ApiClient
from ibutsu_client.exceptions import ApiException, ApiValueError
from ibutsu_client.configuration import Configuration
from ibutsu_client.api.artifact_api import ArtifactApi
from ibutsu_client.api.health_api import HealthApi
from ibutsu_client.api.result_api import ResultApi
from ibutsu_client.api.run_api import RunApi
from urllib3.exceptions import MaxRetryError
from urllib3.exceptions import ProtocolError
from urllib3.exceptions import NewConnectionError
from urllib3.exceptions import ConnectTimeoutError

from .modeling import IbutsuTestResult
from .modeling import IbutsuTestRun


if TYPE_CHECKING:
    from .pytest_plugin import IbutsuPlugin


# Place a limit on the file-size we can upload for artifacts
UPLOAD_LIMIT = 5 * 1024 * 1024  # 5 MiB

# Maximum number of times an API call is retried
MAX_CALL_RETRIES = 3

# Base delay between retries in seconds
RETRY_BASE_DELAY = 1.0

# Backoff factor for exponential delay
RETRY_BACKOFF_FACTOR = 2.0

CA_BUNDLE_ENVS = ["REQUESTS_CA_BUNDLE", "IBUTSU_CA_BUNDLE"]

logger = logging.getLogger(__name__)


class TooManyRetriesError(Exception):
    pass


class ArtifactDataHandler:
    """Simplified handler for artifact data that reduces branching and complexity."""

    def __init__(self, data: Any, upload_limit: int = UPLOAD_LIMIT) -> None:
        self.original_data = data
        self.upload_limit = upload_limit

    @cached_property
    def size(self) -> int:
        """Get the size of the data, cached for performance."""
        try:
            return self._calculate_size()
        except (PermissionError, OSError) as e:
            # Re-raise PermissionError for proper handling upstream
            if isinstance(e, PermissionError):
                raise
            # For other OSError, treat as string content
            text = str(self.original_data) if self.original_data is not None else "None"
            return len(text.encode("utf-8"))

    def _calculate_size(self) -> int:
        """Calculate size using simplified logic."""
        # Handle None first
        if self.original_data is None:
            return 4  # len("None")

        # Handle bytes
        if isinstance(self.original_data, bytes):
            return len(self.original_data)

        # Handle file-like objects (has read method)
        if hasattr(self.original_data, "read"):
            return self._get_file_like_size()

        # Handle strings that might be file paths
        if isinstance(self.original_data, str):
            # Don't treat URLs as file paths
            if self.original_data.startswith(("http://", "https://")):
                return len(self.original_data.encode("utf-8"))

            # Try to treat as file path
            try:
                path = Path(self.original_data)
                if path.is_file():
                    return path.stat().st_size
            except PermissionError:
                # Re-raise PermissionError so it can be handled upstream
                raise
            except (TypeError, OSError):
                # Handle other file system errors
                pass

            # Fall back to string length
            return len(self.original_data.encode("utf-8"))

        # Handle all other types as strings
        text = str(self.original_data)
        return len(text.encode("utf-8"))

    def _get_file_like_size(self) -> int:
        """Get size of file-like object, trying to avoid reading the whole file."""
        if hasattr(self.original_data, "seek") and hasattr(self.original_data, "tell"):
            # Save current position
            current_pos = self.original_data.tell()
            # Seek to end to get size
            self.original_data.seek(0, 2)  # SEEK_END
            size = cast(int, self.original_data.tell())
            # Restore original position
            self.original_data.seek(current_pos)
            return size
        else:
            # If we can't seek, we'll have to read the content
            content = self.original_data.read()
            # Reset position if possible
            if hasattr(self.original_data, "seek"):
                self.original_data.seek(0)
            return (
                len(content) if isinstance(content, (bytes, str)) else len(str(content))
            )

    def is_size_acceptable(self) -> bool:
        """Check if the data size is within upload limits."""
        try:
            return self.size < self.upload_limit
        except PermissionError:
            # Permission errors should be handled by caller
            raise
        except (OSError, TypeError):
            # If we can't determine size, reject to be safe
            return False

    @cached_property
    def prepared_data(self) -> bytes | str:
        """Get the data prepared for API upload, cached for performance."""
        try:
            return self._prepare_content()
        except OSError as e:
            logger.error(f"Failed to read data: {e}")
            # Fall back to string representation
            return str(self.original_data) if self.original_data is not None else "None"

    def _prepare_content(self) -> bytes | str:
        """Prepare content using simplified logic."""
        # Handle None first
        if self.original_data is None:
            return "None"

        # Handle bytes - keep as bytes for binary uploads
        if isinstance(self.original_data, bytes):
            return self.original_data

        # Handle file-like objects
        if hasattr(self.original_data, "read"):
            return self._read_file_like_content()

        # Handle strings that might be file paths
        if isinstance(self.original_data, str):
            # Don't treat URLs as file paths
            if self.original_data.startswith(("http://", "https://")):
                return self.original_data

            # Try to treat as file path and read as binary
            try:
                path = Path(self.original_data)
                if path.is_file():
                    return path.read_bytes()
            except (TypeError, PermissionError, OSError):
                # For PermissionError or other issues, treat as string
                pass

            # Fall back to string content
            return self.original_data

        # Handle all other types as strings
        return str(self.original_data)

    def _read_file_like_content(self) -> bytes:
        """Read content from file-like objects."""
        try:
            content = self.original_data.read()
            if isinstance(content, bytes):
                return content
            elif isinstance(content, str):
                return content.encode("utf-8")
            else:
                return str(content).encode("utf-8")
        except OSError as e:
            logger.error(f"Failed to read from file-like object: {e}")
            return str(self.original_data).encode("utf-8")


R = TypeVar("R")
P = ParamSpec("P")


class IbutsuSender:
    def __init__(self, server_url: str, token: str | None = None):
        self.server_url = server_url
        self._has_server_error = False
        self._server_error_tbs: list[str] = []
        self._sender_cache: list[Any] = []
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
        logger.info(f"Ibutsu server: {ibutsu.ibutsu_server}")
        ibutsu_server = ibutsu.ibutsu_server
        if ibutsu.ibutsu_server.endswith("/"):
            ibutsu_server = ibutsu_server[:-1]
        if not ibutsu.ibutsu_server.endswith("/api"):
            ibutsu_server += "/api"
        return cls(server_url=ibutsu_server, token=ibutsu.ibutsu_token)

    @property
    def frontend_url(self) -> str:
        return cast(str, self.health_api.get_health_info().frontend)

    def _make_call(
        self,
        api_method: Callable[..., R],
        *args: Any,
        hide_exception: bool = False,
        **kwargs: Any,
    ) -> R | None:
        # hide_exception is now a direct parameter

        # Log method name and id once at the beginning
        method_name = getattr(api_method, "__name__", str(api_method))
        method_id = kwargs.get("id") or (
            args[0] if args and isinstance(args[0], str) else None
        )
        logger.debug(
            f"Calling API method: {method_name}"
            + (f" with id: {method_id}" if method_id else "")
        )

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
                except (
                    RemoteDisconnected,
                    ProtocolError,
                    BadStatusLine,
                    NewConnectionError,
                    ConnectTimeoutError,
                ) as e:
                    retries += 1
                    if retries < MAX_CALL_RETRIES:
                        # Calculate delay with exponential backoff
                        delay = RETRY_BASE_DELAY * (
                            RETRY_BACKOFF_FACTOR ** (retries - 1)
                        )
                        logger.debug(
                            f"Network error (attempt {retries}/{MAX_CALL_RETRIES}): {e.__class__.__name__}: {e}. "
                            f"Retrying in {delay:.1f} seconds..."
                        )
                        time.sleep(delay)
                    else:
                        logger.exception(
                            f"Network error (final attempt {retries}/{MAX_CALL_RETRIES}): {e.__class__.__name__}: {e}. "
                        )
                        raise TooManyRetriesError(
                            f"Too many retries ({MAX_CALL_RETRIES}) while trying to call API"
                            f"Network error (final attempt {retries}/{MAX_CALL_RETRIES}): {e.__class__.__name__}: {e}. "
                        )

        except (MaxRetryError, ApiException, TooManyRetriesError) as e:
            if not hide_exception:
                logger.exception("API call failed:")
                self._has_server_error = self._has_server_error or True
                self._server_error_tbs.append(str(e))

        return None

    def add_or_update_run(self, run: IbutsuTestRun) -> None:
        if bool(self._make_call(self.run_api.get_run, hide_exception=True, id=run.id)):
            self._make_call(self.run_api.update_run, id=run.id, run=run.to_dict())
        else:
            self._make_call(self.run_api.add_run, run=run.to_dict())

    def upload_artifacts(self, r: IbutsuTestResult | IbutsuTestRun) -> None:
        for filename, data in r._artifacts.items():
            try:
                self._upload_artifact(
                    r.id, filename, data, isinstance(r, IbutsuTestRun)
                )
            except Exception:
                logger.exception(f"Uploading artifact {filename} failed, continuing...")
                continue

    def add_result(self, result: IbutsuTestResult) -> None:
        logger.debug(f"Adding result {result.id}")
        self._make_call(self.result_api.add_result, result=result.to_dict())

    def _upload_artifact(
        self, id_: str, filename: str, data: bytes | str | None, is_run: bool = False
    ) -> None:
        kwargs = {"run_id" if is_run else "result_id": id_}
        try:
            logger.debug(f"Uploading artifact {filename} for {id_}")

            # Use unified data handler for simplified processing
            handler = ArtifactDataHandler(data)

            # Check size using the unified handler
            if not handler.is_size_acceptable():
                logger.error("Artifact size is greater than upload limit")
                return

            # Pass data directly to the API
            self._make_call(
                self.artifact_api.upload_artifact,
                filename=filename,
                file=handler.prepared_data,
                hide_exception=False,
                **kwargs,
            )
        except (PermissionError, OSError) as exc:
            # data should be a file path string in this context, but handle bytes safely
            data_repr = (
                data.decode("utf-8", errors="replace")
                if isinstance(data, bytes)
                else data
            )
            if isinstance(exc, PermissionError):
                logger.error(
                    f"Permission denied when accessing artifact file '{data_repr}', skipping upload."
                )
            else:
                logger.error(
                    f"Error accessing artifact file '{data_repr}': {exc}, skipping upload."
                )
        except ApiValueError:
            logger.error(
                f"Uploading artifact '{filename}' failed as the file closed prematurely."
            )


def send_data_to_ibutsu(ibutsu_plugin: IbutsuPlugin) -> None:
    sender = IbutsuSender.from_ibutsu_plugin(ibutsu_plugin)

    sender.add_or_update_run(ibutsu_plugin.run)
    sender.upload_artifacts(ibutsu_plugin.run)
    for result in ibutsu_plugin.results.values():
        sender.add_result(result)
        sender.upload_artifacts(result)
    # To start update_run task on Ibutsu server we should update Run
    # https://github.com/ibutsu/pytest-ibutsu/issues/61
    sender.add_or_update_run(ibutsu_plugin.run)

    # Update summary info for terminal output
    ibutsu_plugin.summary_info["server_uploaded"] = not sender._has_server_error
    ibutsu_plugin.summary_info["server_url"] = sender.server_url
    if not sender._has_server_error:
        ibutsu_plugin.summary_info["frontend_url"] = sender.frontend_url
    else:
        ibutsu_plugin.summary_info["errors"].append(
            f"Server upload failed to {sender.server_url}"
        )
