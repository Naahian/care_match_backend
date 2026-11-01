from dataclasses import dataclass

from fastapi import HTTPException, Request
from starlette.datastructures import UploadFile

from app.services.medical_reports import MAX_REPORT_BYTES, validate_report_filename


@dataclass
class MedicalReportUpload:
    filename: str
    data: bytes


async def parse_medical_report_upload(request: Request) -> MedicalReportUpload:
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "multipart/form-data":
        raise HTTPException(415, "Use multipart/form-data with filename and file fields")
    # Bound the entire body before Starlette spools multipart uploads to disk.
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_REPORT_BYTES + 64 * 1024:
            raise HTTPException(413, "Report request is too large; files must be 20 MB or smaller")
        chunks.append(chunk)
    body = b"".join(chunks)

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    bounded_request = Request(request.scope, receive)
    async with bounded_request.form(max_files=1, max_fields=1) as form:
        if len(form.multi_items()) != 2 or set(form) != {"filename", "file"}:
            raise HTTPException(422, "Provide exactly one filename field and one file field")
        filename, upload = form["filename"], form["file"]
        if not isinstance(filename, str) or not isinstance(upload, UploadFile):
            raise HTTPException(422, "filename must be text and file must be an uploaded file")
        filename = validate_report_filename(filename)
        if upload.size is not None and upload.size > MAX_REPORT_BYTES:
            raise HTTPException(413, "Report file must be 20 MB or smaller")
        data = await upload.read(MAX_REPORT_BYTES + 1)
        if len(data) > MAX_REPORT_BYTES:
            raise HTTPException(413, "Report file must be 20 MB or smaller")
    return MedicalReportUpload(filename, data)


MEDICAL_REPORT_REQUEST_BODY = {
    "requestBody": {
        "required": True,
        "content": {"multipart/form-data": {"schema": {
            "type": "object",
            "required": ["filename", "file"],
            "additionalProperties": False,
            "properties": {
                "filename": {"type": "string", "maxLength": 200,
                             "description": "Admin's report name; extension is optional. Images are saved as .jpg and PDFs as .pdf. An explicit extension may be .pdf, .jpg, .jpeg, .png, or .webp."},
                "file": {"type": "string", "format": "binary",
                         "description": "PDF, JPEG, PNG, or WebP report; maximum 20 MB. Image reports are converted to JPEG."},
            },
        }}},
    },
}
