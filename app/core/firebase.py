from asyncio.log import logger
from functools import lru_cache
from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore, storage
from google.cloud.storage import Bucket

from app.core.config import settings,  BASE_DIR

def _initialize_app() -> None:
    try:
        firebase_admin.get_app()
        return
    except ValueError:
        pass

    if not settings.firebase_credentials_path:
        raise RuntimeError("FIREBASE_CREDENTIALS_PATH is not set (is .env being loaded?)")

    cred_path = Path(settings.firebase_credentials_path)
    if not cred_path.is_absolute():
        cred_path = BASE_DIR / cred_path

    options: dict[str, Any] = {}
    if settings.google_cloud_project:
        options["projectId"] = settings.google_cloud_project
    if settings.firebase_storage_bucket:
        options["storageBucket"] = settings.firebase_storage_bucket

    logger.info("Initializing firebase admin with %s", cred_path)
    firebase_admin.initialize_app(credentials.Certificate(str(cred_path)), options or None)

@lru_cache
def get_firestore_client() -> firestore.Client:
    _initialize_app()
    return firestore.client()


@lru_cache
def get_storage_bucket() -> Bucket:
    _initialize_app()
    bucket_name = settings.firebase_storage_bucket
    if not bucket_name:
        raise RuntimeError("FIREBASE_STORAGE_BUCKET is not configured")
    return storage.bucket(bucket_name)


def get_db() -> firestore.Client:
    return get_firestore_client()


def get_bucket() -> Bucket:
    return get_storage_bucket()
