import os

import boto3


def _get_s3_bucket() -> str:
    bucket = os.environ.get("AWS_S3_BUCKET")
    if not bucket:
        raise RuntimeError("AWS_S3_BUCKET environment variable is required")
    return bucket


def _get_s3_client():
    region = os.environ.get("AWS_REGION")
    if not region:
        raise RuntimeError("AWS_REGION environment variable is required")
    return boto3.client("s3", region_name=region)


def _normalize_key(s3_key: str) -> str:
    bucket = _get_s3_bucket()
    if s3_key.startswith("s3://"):
        prefix = f"s3://{bucket}/"
        if s3_key.startswith(prefix):
            return s3_key[len(prefix):]
    return s3_key


def upload_file_to_s3(local_path: str, s3_key: str):
    client = _get_s3_client()
    bucket = _get_s3_bucket()
    client.upload_file(local_path, bucket, _normalize_key(s3_key))


def download_file_from_s3(s3_key: str, local_path: str):
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    client = _get_s3_client()
    bucket = _get_s3_bucket()
    client.download_file(bucket, _normalize_key(s3_key), local_path)
