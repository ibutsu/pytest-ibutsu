from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .modeling import validate_uuid_string

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    boto3 = None
    BotoCoreError = None
    ClientError = None

if TYPE_CHECKING:
    from .pytest_plugin import IbutsuPlugin


class S3UploadError(Exception):
    pass


class S3Uploader:
    """Handles uploading artifacts to Amazon S3 bucket."""

    def __init__(self, bucket_name: str | None = None, timeout: int = 180) -> None:
        if boto3 is None:
            raise S3UploadError(
                "boto3 is required for S3 upload functionality. "
                "Install it with: pip install pytest-ibutsu[s3]"
            )

        self.bucket_name = bucket_name or os.getenv("AWS_BUCKET")
        if not self.bucket_name:
            raise S3UploadError(
                "AWS bucket name is required. Set AWS_BUCKET environment variable "
                "or pass bucket_name parameter."
            )

        self.timeout = timeout
        self.s3_client: Any = None
        self._init_s3_client()

    def _init_s3_client(self) -> None:
        """Initialize S3 client with AWS credentials from environment."""
        try:
            # boto3 will automatically use AWS credentials from:
            # - Environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION)
            # - AWS credentials file
            # - EC2 instance profile
            # - AWS IAM role
            self.s3_client = boto3.client("s3")
        except Exception as e:
            raise S3UploadError(f"Failed to initialize S3 client: {e}") from e

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
            print(f"Directory {directory} does not exist")
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
            if self.s3_client is None:
                return False

            response = self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
            s3_file_size = response.get("ContentLength", 0)

            return s3_file_size == local_file_size

        except ClientError as e:
            # If the file doesn't exist, head_object raises a 404 ClientError
            if e.response["Error"]["Code"] == "404":
                return False
            # For other errors, log and return False to be safe
            print(f"Error checking S3 file existence for {key}: {e}")
            return False
        except Exception as e:
            print(f"Unexpected error checking S3 file existence for {key}: {e}")
            return False

    def upload_file(self, file_path: Path, key: str | None = None) -> str | None:
        """Upload a single file to S3.

        Args:
            file_path: Local file path to upload
            key: S3 object key (if None, uses file path as key)

        Returns:
            S3 URL of uploaded file

        Raises:
            S3UploadError: If upload fails
        """
        if not file_path.exists():
            raise S3UploadError(f"File {file_path} does not exist")

        s3_key = key or str(file_path)

        # Get local file size
        local_file_size = file_path.stat().st_size
        s3_url = f"s3://{self.bucket_name}/{s3_key}"

        # Check if file already exists in S3 with same name and size
        if self._file_exists_in_s3(s3_key, local_file_size):
            print(
                f"Pytest-Ibutsu: Skipping {file_path}, exists in S3 with same size: {s3_url}"
            )
            return None

        try:
            print(f"Pytest-Ibutsu: Uploading {file_path} to {self.bucket_name}")

            if self.s3_client is None:
                raise S3UploadError("S3 client not initialized")

            with open(file_path, "rb") as file_obj:
                self.s3_client.upload_fileobj(
                    file_obj,
                    self.bucket_name,
                    s3_key,
                    ExtraArgs={"ServerSideEncryption": "AES256"},
                )

            # Construct S3 URL
            print(f"Pytest-Ibutsu: Upload complete: {s3_url}")

            return s3_url

        except (BotoCoreError, ClientError) as e:
            error_msg = f"Failed to upload {file_path} to S3: {e}"
            print(f"Error: {error_msg}")
            raise S3UploadError(error_msg) from e
        except Exception as e:
            error_msg = f"Unexpected error uploading {file_path}: {e}"
            print(f"Error: {error_msg}")
            raise S3UploadError(error_msg) from e

    def upload_archives(self, directory: str = ".") -> list[str]:
        """Upload artifacts from directory to S3.

        Args:
            directory: Directory to search for artifacts (default: current directory)

        Returns:
            List of S3 URLs for uploaded files
        """

        print(f"Scanning {directory} for files")
        files_to_upload = self.find_uuid_tar_gz_files(directory)

        if not files_to_upload:
            print(f"No files found in {directory}")
            return []

        uploaded_urls = []
        for file_path in files_to_upload:
            try:
                s3_url = self.upload_file(file_path)
                if s3_url is not None:
                    uploaded_urls.append(s3_url)
            except S3UploadError:
                # Continue uploading other files even if one fails
                continue

        print(
            f"Successfully uploaded {len(uploaded_urls)} out of {len(files_to_upload)} files"
        )
        return uploaded_urls


def upload_to_s3(
    ibutsu_plugin: IbutsuPlugin,
    directory: str = ".",
) -> None:
    """Upload pytest-ibutsu artifacts to S3.

    Args:
        ibutsu_plugin: The IbutsuPlugin instance
        directory: Directory to search for artifacts
    """
    try:
        uploader = S3Uploader()
        uploader.upload_archives(directory)
    except S3UploadError as e:
        print(f"S3 upload failed: {e}")
    except Exception as e:
        print(f"Unexpected error during S3 upload: {e}")
