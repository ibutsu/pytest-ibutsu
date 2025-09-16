from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, TYPE_CHECKING

import boto3
from botocore.exceptions import ClientError

from .modeling import validate_uuid_string

if TYPE_CHECKING:
    from .pytest_plugin import IbutsuPlugin

logger = logging.getLogger(__name__)


class S3Uploader:
    """Handles uploading artifacts to Amazon S3 bucket."""

    def __init__(self, bucket_name: str | None = None, timeout: int = 180) -> None:
        self.bucket_name = bucket_name or os.getenv("AWS_BUCKET")
        if not self.bucket_name:
            raise ValueError(
                "AWS bucket name is required. Set AWS_BUCKET environment variable "
                "or pass bucket_name parameter."
            )

        self.timeout = timeout

        # boto3 will automatically use AWS credentials from:
        # - Environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION)
        # - AWS credentials file
        # - EC2 instance profile
        # - AWS IAM role
        self.s3_client: Any = boto3.client("s3")
        assert self.s3_client is not None

    def find_uuid_tar_gz_files(self, directory: str = ".") -> list[Path]:
        """Find files in the first level of the directory.

        For .tar.gz files, only includes files with UUID.tar.gz pattern.

        Args:
            directory: Directory to search in

        Returns:
            List of Path objects for matching files
        """
        directory_path = Path(directory)

        if not directory_path.exists():
            logger.warning(f"Directory {directory} does not exist")
            return []

        return [
            f
            for f in directory_path.glob("*.tar.gz")
            if f.is_file() and validate_uuid_string(f.name[:-7])
        ]

    def _file_exists_in_s3(self, key: str, local_file_size: int) -> bool:
        """Check if a file with the same name and size already exists in S3.

        Args:
            key: S3 object key to check
            local_file_size: Size of the local file in bytes

        Returns:
            True if file exists with same size, False otherwise
        """
        try:
            response = self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
            s3_file_size = response.get("ContentLength", 0)

            return bool(s3_file_size == local_file_size)

        except Exception as e:
            # If the file doesn't exist, head_object raises a 404 ClientError
            if isinstance(e, ClientError) and e.response["Error"]["Code"] == "404":
                return False
            # For other errors, log and return False to be safe
            logger.warning(f"Error checking S3 file existence for {key}: {e}")
            return False

    def upload_file(self, file_path: Path, key: str | None = None) -> str | None:
        """Upload a single file to S3.

        Args:
            file_path: Local file path to upload
            key: S3 object key (if None, uses file path as key)

        Returns:
            S3 URL of uploaded file

        """
        if not file_path.exists():
            raise FileNotFoundError(f"{file_path.resolve()}")

        s3_key = key or str(file_path)

        # Get local file size
        local_file_size = file_path.stat().st_size
        s3_url = f"s3://{self.bucket_name}/{s3_key}"

        # Check if file already exists in S3 with same name and size
        if self._file_exists_in_s3(s3_key, local_file_size):
            logger.warning(
                f"Pytest-Ibutsu: Skipping {file_path}, exists in S3 with same size: {s3_url}"
            )
            return None

        logger.info(f"Uploading {file_path} to {self.bucket_name}")

        with open(file_path, "rb") as file_obj:
            self.s3_client.upload_fileobj(
                file_obj,
                self.bucket_name,
                s3_key,
                ExtraArgs={"ServerSideEncryption": "AES256"},
            )

        # Construct S3 URL
        logger.info(f"Pytest-Ibutsu: Upload complete: {s3_url}")

        return s3_url

    def upload_archives(self, directory: str = ".") -> list[str]:
        """Upload artifacts from directory to S3.

        Args:
            directory: Directory to search for artifacts (default: current directory)

        Returns:
            List of S3 URLs for uploaded files
        """

        logger.debug(f"Scanning {directory} for files")
        files_to_upload = self.find_uuid_tar_gz_files(directory)

        if not files_to_upload:
            logger.debug(f"No files found in {directory}")
            return []

        uploaded_urls = []
        for file_path in files_to_upload:
            try:
                s3_url = self.upload_file(file_path)
                if s3_url is not None:
                    uploaded_urls.append(s3_url)
            except Exception:
                # Continue uploading other files even if one fails
                logger.exception(f"Failed to upload {file_path}, continuing...")
                continue

        logger.info(
            f"Successfully uploaded {len(uploaded_urls)} out of {len(files_to_upload)} files"
        )
        return uploaded_urls


def upload_to_s3(
    directory: str = ".", ibutsu_plugin: IbutsuPlugin | None = None
) -> None:
    """Upload pytest-ibutsu artifacts to S3.

    Args:
        directory: Directory to search for artifacts
        ibutsu_plugin: IbutsuPlugin instance for summary tracking
    """
    try:
        uploader = S3Uploader()
        uploaded_urls = uploader.upload_archives(directory)

        # Update summary info for terminal output
        if ibutsu_plugin:
            ibutsu_plugin.summary_info["s3_uploaded"] = len(uploaded_urls) > 0
            ibutsu_plugin.summary_info["s3_upload_count"] = len(uploaded_urls)
            ibutsu_plugin.summary_info["s3_bucket"] = uploader.bucket_name

    except Exception as e:
        if ibutsu_plugin:
            ibutsu_plugin.summary_info["s3_upload_errors"] = 1
            ibutsu_plugin.summary_info["errors"].append(f"S3 upload error: {str(e)}")
        # Keep the exception logging for debugging purposes if needed
        logger.exception("Error processing archives for upload:")
