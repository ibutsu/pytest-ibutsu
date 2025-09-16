from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from pytest_ibutsu.s3_uploader import S3Uploader, upload_to_s3


class TestS3Uploader:
    def test_init_without_bucket_name(self):
        """Test S3Uploader initialization fails when no bucket name is provided."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="AWS bucket name is required"):
                S3Uploader()

    def test_init_with_bucket_name(self):
        """Test S3Uploader initialization succeeds with bucket name."""
        uploader = S3Uploader(bucket_name="test-bucket")
        assert uploader.bucket_name == "test-bucket"
        assert uploader.timeout == 180
        assert uploader.s3_client is not None

    def test_init_with_env_bucket(self):
        """Test S3Uploader initialization uses AWS_BUCKET environment variable."""
        with patch.dict(os.environ, {"AWS_BUCKET": "env-bucket"}):
            uploader = S3Uploader()
            assert uploader.bucket_name == "env-bucket"

    def test_find_uuid_tar_gz_files_first_level_only(self):
        """Test finding UUID.tar.gz files in first level only."""
        uploader = S3Uploader(bucket_name="test-bucket")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create test files - only UUID.tar.gz files should be found
            (
                temp_path / "12345678-1234-1234-1234-123456789abc.tar.gz"
            ).touch()  # Valid UUID
            (temp_path / "test.tar.gz").touch()  # Invalid - not UUID pattern
            (temp_path / "data.ibutsu.xml").touch()  # Different extension - ignored
            (temp_path / "other.txt").touch()  # Different extension - ignored

            # Create subdirectory with nested files - these should not be found
            (temp_path / "subdir").mkdir()
            (
                temp_path / "subdir" / "87654321-4321-4321-4321-fedcba987654.tar.gz"
            ).touch()  # Valid UUID but nested

            files = uploader.find_uuid_tar_gz_files(temp_dir)
            file_names = [f.name for f in files]

            # Should only find UUID.tar.gz file in first level
            assert len(files) == 1
            assert "12345678-1234-1234-1234-123456789abc.tar.gz" in file_names
            assert "test.tar.gz" not in file_names  # Not UUID pattern
            assert (
                "87654321-4321-4321-4321-fedcba987654.tar.gz" not in file_names
            )  # Nested
            assert "data.ibutsu.xml" not in file_names  # Different extension
            assert "other.txt" not in file_names  # Different extension

    def test_find_uuid_tar_gz_files_pattern_validation(self):
        """Test UUID pattern validation for .tar.gz files."""
        uploader = S3Uploader(bucket_name="test-bucket")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create various .tar.gz files
            (
                temp_path / "12345678-1234-1234-1234-123456789abc.tar.gz"
            ).touch()  # Valid UUID
            (
                temp_path / "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE.tar.gz"
            ).touch()  # Valid UUID (uppercase)
            (temp_path / "test.tar.gz").touch()  # Invalid - not UUID
            (
                temp_path / "12345678-1234-1234-123456789abc.tar.gz"
            ).touch()  # Invalid - wrong format
            (
                temp_path / "12345678-1234-1234-1234-123456789abcd.tar.gz"
            ).touch()  # Invalid - too long

            files = uploader.find_uuid_tar_gz_files(temp_dir)
            file_names = [f.name for f in files]

            # Should only find files with valid UUID pattern
            assert len(files) == 2
            assert "12345678-1234-1234-1234-123456789abc.tar.gz" in file_names
            assert "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE.tar.gz" in file_names

    def test_file_exists_in_s3(self, s3_uploader_instance):
        """Test checking if file exists in S3 with same size."""
        uploader = s3_uploader_instance

        # Mock successful head_object response
        with patch.object(uploader.s3_client, "head_object") as mock_head:
            mock_head.return_value = {"ContentLength": 1024}

            # File exists with same size
            assert uploader._file_exists_in_s3("test-key", 1024) is True

            # File exists with different size
            assert uploader._file_exists_in_s3("test-key", 2048) is False

    def test_file_exists_in_s3_not_found(self, s3_uploader_instance):
        """Test checking file existence when file doesn't exist in S3."""
        uploader = s3_uploader_instance

        # Mock 404 ClientError
        error_response = {
            "Error": {"Code": "404", "Message": "Not Found"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }
        client_error = ClientError(error_response, "head_object")

        with patch.object(uploader.s3_client, "head_object", side_effect=client_error):
            assert uploader._file_exists_in_s3("non-existent-key", 1024) is False

    def test_upload_file_skips_existing(self, s3_uploader_instance, shared_test_files):
        """Test that upload_file skips files that already exist with same size."""
        uploader = s3_uploader_instance
        temp_file_path = shared_test_files / "binary_content.bin"

        # Mock file exists in S3 with same size
        with patch.object(uploader, "_file_exists_in_s3", return_value=True):
            with patch.object(uploader.s3_client, "upload_fileobj") as mock_upload:
                assert uploader.upload_file(temp_file_path, "test-key") is None

                # Should not call upload_fileobj since file exists
                mock_upload.assert_not_called()

    def test_find_uuid_tar_gz_files(self):
        """Test finding UUID.tar.gz files specifically."""
        uploader = S3Uploader(bucket_name="test-bucket")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create test files
            uuid1 = str(uuid.uuid4())
            uuid2 = str(uuid.uuid4())
            (temp_path / f"{uuid1}.tar.gz").touch()
            (temp_path / f"{uuid2}.tar.gz").touch()
            (temp_path / "test.tar.gz").touch()  # Invalid - not UUID
            (temp_path / "data.ibutsu.xml").touch()  # Different extension

            files = uploader.find_uuid_tar_gz_files(temp_dir)
            file_names = [f.name for f in files]

            assert len(files) == 2
            assert f"{uuid1}.tar.gz" in file_names
            assert f"{uuid2}.tar.gz" in file_names
            assert "test.tar.gz" not in file_names
            assert "data.ibutsu.xml" not in file_names

    def test_upload_file_success(self, s3_uploader_instance, shared_test_files):
        """Test successful file upload to S3."""
        uploader = s3_uploader_instance
        temp_file_path = shared_test_files / "binary_content.bin"

        # Mock the upload_fileobj method since it's a complex boto3 convenience method
        with patch.object(uploader.s3_client, "upload_fileobj") as mock_upload:
            result = uploader.upload_file(temp_file_path, "test-key")

            # Verify the method was called with correct parameters
            mock_upload.assert_called_once()
            call_args = mock_upload.call_args

            # Check bucket and key parameters
            assert call_args[0][1] == "test-bucket"  # Bucket
            assert call_args[0][2] == "test-key"  # Key

            # Check ExtraArgs contains ServerSideEncryption
            extra_args = call_args[1].get("ExtraArgs", {})
            assert extra_args.get("ServerSideEncryption") == "AES256"

        assert result == "s3://test-bucket/test-key"

    def test_upload_file_not_found(self, s3_uploader_instance):
        """Test upload fails when file doesn't exist."""
        uploader = s3_uploader_instance

        with pytest.raises(FileNotFoundError):
            uploader.upload_file(Path("/non/existent/file"))

    def test_upload_archives(self):
        """Test uploading archive files."""
        uploader = S3Uploader(bucket_name="test-bucket")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create test files with valid UUID pattern for .tar.gz
            test_file = temp_path / "12345678-1234-1234-1234-123456789abc.tar.gz"
            test_file.write_text("test content")
            # Note: .xml files are not uploaded by upload_archives - only UUID.tar.gz files
            xml_file = temp_path / "data.ibutsu.xml"
            xml_file.write_text("xml content")

            # Mock successful uploads
            with patch.object(uploader, "upload_file") as mock_upload:
                mock_upload.side_effect = lambda f, k=None: f"s3://test-bucket/{f.name}"

                results = uploader.upload_archives(temp_dir)

                # Only UUID.tar.gz files are uploaded by upload_archives
                assert len(results) == 1
                assert mock_upload.call_count == 1

    def test_upload_file_client_error(self, tmp_path):
        """Test S3 client error handling."""
        uploader = S3Uploader(bucket_name="test-bucket")

        temp_file_path = tmp_path / "test_content.bin"
        temp_file_path.write_bytes(b"test content")

        # Mock upload_fileobj to raise a ClientError
        error_response = {
            "Error": {
                "Code": "NoSuchBucket",
                "Message": "The specified bucket does not exist.",
            },
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }

        client_error = ClientError(error_response, "upload_fileobj")

        with patch.object(
            uploader.s3_client, "upload_fileobj", side_effect=client_error
        ):
            with pytest.raises(Exception):  # Generic exception for upload failures
                uploader.upload_file(temp_file_path, "test-key")

    def test_s3_bucket_validation_with_stubber(self):
        """Test S3 bucket validation using botocore Stubber for head_bucket operation."""
        uploader = S3Uploader(bucket_name="test-bucket")

        # Use Stubber for a simple S3 operation like head_bucket (checking if bucket exists)
        stubber = Stubber(uploader.s3_client)

        # Mock successful head_bucket response (bucket exists)
        stubber.add_response("head_bucket", {}, {"Bucket": "test-bucket"})

        # This demonstrates proper Stubber usage with actual S3 operations
        with stubber:
            # Call head_bucket operation to verify bucket exists
            response = uploader.s3_client.head_bucket(Bucket="test-bucket")
            assert response is not None

    def test_s3_client_initialization_failure(self):
        """Test S3 client initialization failure."""
        with patch("pytest_ibutsu.s3_uploader.boto3") as mock_boto3:
            # Mock boto3.client to raise an exception
            mock_boto3.client.side_effect = Exception("AWS credentials not found")

            with pytest.raises(Exception, match="AWS credentials not found"):
                S3Uploader(bucket_name="test-bucket")

    def test_find_uuid_tar_gz_files_directory_not_found(self):
        """Test find_uuid_tar_gz_files when directory doesn't exist."""
        uploader = S3Uploader(bucket_name="test-bucket")

        non_existent_dir = "/path/that/does/not/exist"
        files = uploader.find_uuid_tar_gz_files(non_existent_dir)

        assert files == []

    def test_upload_file_s3_client_not_initialized(self, tmp_path):
        """Test upload_file when S3 client is None."""
        uploader = S3Uploader(bucket_name="test-bucket")
        uploader.s3_client = None  # Simulate uninitialized client

        temp_file_path = tmp_path / "test_content.bin"
        temp_file_path.write_bytes(b"test content")

        with pytest.raises(Exception):  # Should raise exception if s3_client is None
            uploader.upload_file(temp_file_path)

    def test_upload_file_generic_exception(self, tmp_path):
        """Test upload_file with generic (non-boto3) exception."""
        uploader = S3Uploader(bucket_name="test-bucket")

        temp_file_path = tmp_path / "test_content.bin"
        temp_file_path.write_bytes(b"test content")

        # Mock upload_fileobj to raise a generic exception
        with patch.object(
            uploader.s3_client,
            "upload_fileobj",
            side_effect=ValueError("Generic error"),
        ):
            with pytest.raises(Exception):  # Generic upload error
                uploader.upload_file(temp_file_path)

    def test_upload_artifacts_no_files_found(self):
        """Test upload_artifacts when no matching files are found."""
        uploader = S3Uploader(bucket_name="test-bucket")

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a file with non-matching extension
            test_file = Path(temp_dir) / "test.txt"
            test_file.write_text("content")

            # upload_archives only looks for UUID.tar.gz files
            results = uploader.upload_archives(temp_dir)

            assert results == []

    def test_upload_artifacts_partial_failure(self):
        """Test upload_artifacts when some files fail to upload."""
        uploader = S3Uploader(bucket_name="test-bucket")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create test files with valid UUID patterns
            file1 = temp_path / "12345678-1234-1234-1234-123456789abc.tar.gz"
            file1.write_text("success content")
            file2 = temp_path / "87654321-4321-4321-4321-fedcba987654.tar.gz"
            file2.write_text("failure content")

            # Mock upload_file to succeed for first file, fail for second
            def mock_upload_side_effect(file_path: Path, key: str | None = None) -> str:
                if "12345678" in file_path.name:
                    return f"s3://test-bucket/{file_path.name}"
                else:
                    raise Exception("Simulated upload failure")

            with patch.object(
                uploader, "upload_file", side_effect=mock_upload_side_effect
            ):
                results = uploader.upload_archives(temp_dir)

                # Should return only the successful upload
                assert len(results) == 1
                assert "12345678-1234-1234-1234-123456789abc.tar.gz" in results[0]

    def test_upload_artifacts_with_none_extensions(self):
        """Test upload_artifacts with None extensions (uses defaults)."""
        uploader = S3Uploader(bucket_name="test-bucket")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create files with default extensions - use valid UUID for .tar.gz
            tar_file = temp_path / "12345678-1234-1234-1234-123456789abc.tar.gz"
            tar_file.write_text("tar content")
            xml_file = temp_path / "data.ibutsu.xml"
            xml_file.write_text("xml content")
            other_file = temp_path / "other.txt"
            other_file.write_text("other content")

            with patch.object(uploader, "upload_file") as mock_upload:
                mock_upload.side_effect = lambda f, k=None: f"s3://test-bucket/{f.name}"

                results = uploader.upload_archives(temp_dir)

                # Should only find and upload UUID.tar.gz files
                assert len(results) == 1
                assert mock_upload.call_count == 1


class TestUploadToS3Function:
    @patch("pytest_ibutsu.s3_uploader.S3Uploader")
    def test_upload_to_s3_success(self, mock_uploader_class: Any):
        """Test upload_to_s3 function with successful upload."""
        mock_uploader = Mock()
        mock_uploader_class.return_value = mock_uploader

        upload_to_s3()

        mock_uploader_class.assert_called_once()
        mock_uploader.upload_archives.assert_called_once_with(".")

    @patch("pytest_ibutsu.s3_uploader.S3Uploader")
    def test_upload_to_s3_with_s3_error(self, mock_uploader_class: Any):
        """Test upload_to_s3 function handles Exception gracefully."""
        mock_uploader_class.side_effect = Exception("Test S3 error")

        # Should not raise exception
        upload_to_s3()

    @patch("pytest_ibutsu.s3_uploader.S3Uploader")
    def test_upload_to_s3_with_generic_error(self, mock_uploader_class: Any):
        """Test upload_to_s3 function handles generic exceptions gracefully."""
        mock_uploader_class.side_effect = ValueError("Generic error")

        # Should not raise exception
        upload_to_s3()
