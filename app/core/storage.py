"""
Storage abstraction layer.

Today: STORAGE_BACKEND=local -> saves files to disk under MEDIA_ROOT, served via /media static route.
Later: STORAGE_BACKEND=s3    -> saves to AWS S3, returns a real S3/CDN url.

Feature code (attachments router, any module that uploads photos) only ever calls
`storage.save(file_bytes, key)` and `storage.url_for(key)` - it never knows which backend is active.
Switching to AWS later = change one env var, nothing else in the codebase changes.
"""
import os
import uuid
from abc import ABC, abstractmethod

from app.core.config import settings


class StorageBackend(ABC):
    @abstractmethod
    def save(self, file_bytes: bytes, key: str) -> str:
        """Save bytes under `key`, return the storage key actually used."""

    @abstractmethod
    def url_for(self, key: str) -> str:
        """Return a URL the frontend can use to view/download the file."""


class LocalStorageBackend(StorageBackend):
    def save(self, file_bytes: bytes, key: str) -> str:
        path = os.path.join(settings.MEDIA_ROOT, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(file_bytes)
        return key

    def url_for(self, key: str) -> str:
        return f"{settings.MEDIA_BASE_URL}/{key}"


class S3StorageBackend(StorageBackend):
    """Enable by setting STORAGE_BACKEND=s3 + AWS_* env vars. Needs `boto3` (already in requirements.txt)."""

    def __init__(self):
        import boto3
        self.client = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_REGION,
        )
        self.bucket = settings.AWS_S3_BUCKET

    def save(self, file_bytes: bytes, key: str) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=file_bytes)
        return key

    def url_for(self, key: str) -> str:
        return f"https://{self.bucket}.s3.{settings.AWS_REGION}.amazonaws.com/{key}"


def get_storage() -> StorageBackend:
    if settings.STORAGE_BACKEND == "s3":
        return S3StorageBackend()
    return LocalStorageBackend()


def new_key(folder: str, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
    return f"{folder}/{uuid.uuid4()}.{ext}"


storage = get_storage()
