from fastapi import HTTPException, Request
from starlette.datastructures import UploadFile

from app.services.medical_reports import MAX_REPORT_BYTES


async def parse_prescription_upload(request: Request) -> bytes:
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "multipart/form-data":
        raise HTTPException(415, "Use multipart/form-data with a file field")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_REPORT_BYTES + 64 * 1024:
            raise HTTPException(413, "Prescription image must be 20 MB or smaller")
        chunks.append(chunk)
    body = b"".join(chunks)

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async with Request(request.scope, receive).form(max_files=1, max_fields=0) as form:
        if len(form.multi_items()) != 1 or set(form) != {"file"} or not isinstance(form["file"], UploadFile):
            raise HTTPException(422, "Provide exactly one uploaded file field")
        upload = form["file"]
        if upload.size is not None and upload.size > MAX_REPORT_BYTES:
            raise HTTPException(413, "Prescription image must be 20 MB or smaller")
        data = await upload.read(MAX_REPORT_BYTES + 1)
        if len(data) > MAX_REPORT_BYTES:
            raise HTTPException(413, "Prescription image must be 20 MB or smaller")
        return data


PRESCRIPTION_REQUEST_BODY = {"requestBody": {"required": True, "content": {
    "multipart/form-data": {"schema": {
        "type": "object", "required": ["file"], "additionalProperties": False,
        "properties": {"file": {"type": "string", "format": "binary",
                                "description": "JPEG, PNG, or WebP image, at most 20 MB; saved as JPEG."}},
    }},
}}}
