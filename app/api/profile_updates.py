from dataclasses import dataclass
import json

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from app.schemas.users import UserUpdate
from app.services.media import MAX_PHOTO_BYTES


@dataclass
class ProfileUpdateRequest:
    payload: UserUpdate
    photo: bytes | None = None


async def parse_profile_update(request: Request) -> ProfileUpdateRequest:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    limit = MAX_PHOTO_BYTES + 64 * 1024 if content_type == "multipart/form-data" else 64 * 1024
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "Profile request is too large; photos must be 5 MB or smaller")
        chunks.append(chunk)
    body = b"".join(chunks)
    photo = None
    if content_type == "application/json":
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(422, "Invalid profile JSON") from exc
    elif content_type == "multipart/form-data":
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        bounded_request = Request(request.scope, receive)
        async with bounded_request.form(max_files=1, max_fields=10) as form:
            if len(form.multi_items()) != len(form):
                raise HTTPException(422, "Duplicate profile fields are not allowed")
            data = dict(form)
            upload = data.pop("photo", None)
            if upload is not None:
                if not isinstance(upload, UploadFile):
                    raise HTTPException(422, "photo must be an uploaded image file")
                if upload.size is not None and upload.size > MAX_PHOTO_BYTES:
                    raise HTTPException(413, "Photo must be 5 MB or smaller")
                photo = await upload.read(MAX_PHOTO_BYTES + 1)
                if len(photo) > MAX_PHOTO_BYTES:
                    raise HTTPException(413, "Photo must be 5 MB or smaller")
    else:
        raise HTTPException(415, "Use JSON for profile fields or multipart/form-data with a photo file")
    try:
        payload = UserUpdate.model_validate(data)
    except ValidationError as exc:
        raise RequestValidationError([
            {**error, "loc": ("body", *error["loc"])} for error in exc.errors()
        ]) from exc
    if photo is not None and payload.remove_photo:
        raise HTTPException(422, "Upload a photo or remove the photo, not both")
    return ProfileUpdateRequest(payload, photo)


PROFILE_REQUEST_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {"schema": {
                **UserUpdate.model_json_schema(),
                "title": "ProfileUpdateForm",
                "properties": {
                    "photo": {"type": "string", "format": "binary", "description": "Profile image file (JPEG, PNG, or WebP; maximum 5 MB)."},
                    **UserUpdate.model_json_schema()["properties"],
                },
            }},
            "application/json": {"schema": UserUpdate.model_json_schema()},
        },
    },
}
