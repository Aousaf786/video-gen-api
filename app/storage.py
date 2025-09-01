import os
from urllib.parse import urljoin
import boto3
from botocore.client import Config

S3_BUCKET = os.getenv("S3_BUCKET")
S3_REGION = os.getenv("S3_REGION", "us-west-2")
S3_PREFIX = os.getenv("S3_PREFIX", "renders/")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")

def upload_if_configured(local_path: str) -> str:
    if S3_BUCKET:
        key = f"{S3_PREFIX}{os.path.basename(local_path)}"
        s3 = boto3.client("s3", region_name=S3_REGION, config=Config(signature_version="s3v4"))
        s3.upload_file(local_path, S3_BUCKET, key, ExtraArgs={"ACL": "public-read", "ContentType": "video/mp4"})
        if PUBLIC_BASE_URL:
            return urljoin(PUBLIC_BASE_URL.rstrip("/") + "/", key)
        return f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{key}"
    return f"file://{os.path.abspath(local_path)}"
