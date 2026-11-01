import logging
import warnings
from io import BytesIO
from urllib.parse import quote, urlsplit

import httpx
from fastapi import Depends, HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import Settings, get_settings

MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_PHOTO_PIXELS = 25_000_000
PROFILE_IMAGES_BUCKET = "profile_images"
logger = logging.getLogger(__name__)


def normalize_profile_photo(data: bytes) -> bytes:
    if not data:
        raise HTTPException(422, "Photo is empty")
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "Photo must be 5 MB or smaller")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"}:
                    raise HTTPException(415, "Choose a JPEG, PNG, or WebP photo")
                if image.width * image.height > MAX_PHOTO_PIXELS:
                    raise HTTPException(413, "Photo dimensions are too large")
                image.load()
                image = ImageOps.exif_transpose(image)
                image.thumbnail((1024, 1024))
                # Re-encode, stripping EXIF/location data and untrusted filenames.
                output = BytesIO()
                image.convert("RGB").save(output, format="JPEG", quality=85)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise HTTPException(422, "Photo must be a valid JPEG, PNG, or WebP image") from exc


class SupabaseMediaStorage:
    def __init__(self, settings: Settings = Depends(get_settings)) -> None:
        self._settings = settings

    def _configuration(self) -> tuple[str, str]:
        base = (self._settings.supabase_url or "").rstrip("/")
        key = (self._settings.supabase_secret_key or "").strip()
        parsed = urlsplit(base)
        if (not key or parsed.scheme != "https" or not parsed.netloc
            or parsed.username or parsed.query or parsed.fragment or parsed.path):
            raise HTTPException(503, "Supabase media storage is not configured")
        if key.startswith("sb_publishable_"):
            raise HTTPException(503, "Supabase media storage requires a secret or service-role key")
        return base, key

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        base, key = self._configuration()
        # New secret keys go in apikey; legacy service-role JWTs also use Bearer.
        headers = {"apikey": key, **kwargs.pop("headers", {})}
        if not key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {key}"
        try:
            with httpx.Client(timeout=self._settings.supabase_timeout_seconds) as client:
                response = client.request(method, f"{base}/storage/v1/{path}", headers=headers, **kwargs)
                response.raise_for_status()
                return response
        except httpx.HTTPError as exc:
            # Provider bodies and request headers may contain credentials.
            raise HTTPException(502, "Supabase media request could not be confirmed") from exc

    def _object(self, path: str) -> str:
        return f"{PROFILE_IMAGES_BUCKET}/{quote(path, safe='')}"

    def upload_profile_photo(self, uid: str, data: bytes) -> str:
        image = normalize_profile_photo(data)
        path = f"{uid}.jpg"
        # A failed overwrite may still have reached storage. Never delete the
        # stable UID object as compensation for a timeout.
        self._request("POST", f"object/{self._object(path)}", content=image,
                      headers={"Content-Type": "image/jpeg", "x-upsert": "true", "Cache-Control": "no-cache"})
        return path

    def photo_url(self, path: str) -> str:
        base, _ = self._configuration()
        if self._settings.supabase_storage_public:
            return f"{base}/storage/v1/object/public/{self._object(path)}"
        response = self._request("POST", f"object/sign/{self._object(path)}",
                                 json={"expiresIn": self._settings.supabase_signed_url_seconds})
        try:
            signed = response.json()["signedURL"]
            if not isinstance(signed, str) or not signed.startswith("/object/sign/"):
                raise ValueError("Invalid signed URL")
            return f"{base}/storage/v1{signed}"
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(502, "Supabase returned an invalid photo link") from exc

    def delete_best_effort(self, path: str | None) -> None:
        if not path:
            return
        try:
            self._request("DELETE", f"object/{PROFILE_IMAGES_BUCKET}",
                          json={"prefixes": [path]})
        except HTTPException:
            logger.warning("Supabase profile photo cleanup failed")
