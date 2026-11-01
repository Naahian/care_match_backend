import re
import warnings
from datetime import datetime, timedelta, timezone
from io import BytesIO

from fastapi import Depends, HTTPException
from firebase_admin import auth as firebase_auth
from firebase_admin import exceptions as firebase_exceptions
from PIL import Image, ImageOps, UnidentifiedImageError

from app.schemas.medical_reports import MedicalReportPage, MedicalReportResponse, PatientMedicalReport
from app.services.media import MEDICAL_REPORTS_BUCKET, SupabaseMediaStorage

MAX_REPORT_BYTES = 20 * 1024 * 1024
MAX_REPORT_PIXELS = 25_000_000
REPORT_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def validate_patient_folder(patient_uid: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_(). -]{1,128}", patient_uid) or patient_uid in {".", ".."}:
        raise HTTPException(422, "patient_uid must be a valid storage folder name")


def validate_report_filename(filename: str) -> str:
    filename = filename.strip()
    if (len(filename) > 200 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._()-]*", filename)
            or filename.rsplit(".", 1)[0].endswith((" ", "."))):
        raise HTTPException(422, "filename must start with a letter or number and use only letters, numbers, spaces, dots, underscores, hyphens, or parentheses (maximum 200 characters)")
    if "." in filename and "." + filename.rsplit(".", 1)[1].lower() not in REPORT_TYPES:
        raise HTTPException(422, "Use a filename without an extension, or end it with .pdf, .jpg, .jpeg, .png, or .webp")
    return filename


def prepare_medical_report(filename: str, data: bytes) -> tuple[str, bytes, str]:
    if not data:
        raise HTTPException(422, "Report file is empty")
    if len(data) > MAX_REPORT_BYTES:
        raise HTTPException(413, "Report file must be 20 MB or smaller")
    extension = "." + filename.rsplit(".", 1)[1].lower() if "." in filename else ""
    if extension == ".pdf" or (not extension and data.startswith(b"%PDF-")):
        # Check the PDF envelope without rewriting medical document bytes.
        # This is format screening, not a full PDF parser or malware scan.
        if not re.match(rb"%PDF-(?:1\.[0-7]|2\.0)(?:\r|\n|\s)", data[:10]) or not data.rstrip().endswith(b"%%EOF"):
            raise HTTPException(422, "Report file must have a PDF header and end marker")
        return filename if extension else filename + ".pdf", data, "application/pdf"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"}:
                    raise HTTPException(422, "Report file must be a valid JPEG, PNG, or WebP image")
                if image.width * image.height > MAX_REPORT_PIXELS:
                    raise HTTPException(413, "Report image dimensions are too large")
                image.verify()
            # Decode as well: some truncated JPEGs pass verify().
            with Image.open(BytesIO(data)) as image:
                image.load()
                image = ImageOps.exif_transpose(image).convert("RGBA")
                # JPEG has no transparency; white keeps transparent report
                # backgrounds readable. Preserve resolution and apply EXIF
                # orientation before dropping metadata during re-encoding.
                jpeg = Image.new("RGB", image.size, "white")
                jpeg.paste(image, mask=image.getchannel("A"))
                output = BytesIO()
                jpeg.save(output, format="JPEG", quality=95, subsampling=0)
                data = output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(422, "Report file must be a valid JPEG, PNG, or WebP image") from exc
    if len(data) > MAX_REPORT_BYTES:
        raise HTTPException(413, "Converted report image must be 20 MB or smaller")
    stem = filename.rsplit(".", 1)[0] if extension else filename
    return stem + ".jpg", data, "image/jpeg"


class MedicalReportService:
    def __init__(self, media: SupabaseMediaStorage = Depends(SupabaseMediaStorage)) -> None:
        self._media = media

    def create(self, patient_uid: str, filename: str, data: bytes) -> MedicalReportResponse:
        # A UID is one object path segment; never permit folder traversal.
        validate_patient_folder(patient_uid)
        filename = validate_report_filename(filename)
        filename, data, content_type = prepare_medical_report(filename, data)
        filename = validate_report_filename(filename)
        try:
            patient = firebase_auth.get_user(patient_uid)
        except firebase_auth.UserNotFoundError as exc:
            raise HTTPException(404, "Patient not found") from exc
        except ValueError as exc:
            raise HTTPException(422, "Invalid patient_uid") from exc
        except firebase_exceptions.FirebaseError as exc:
            raise HTTPException(503, "Failed to reach Firebase Auth") from exc
        if (patient.custom_claims or {}).get("role") != "patient":
            raise HTTPException(422, "Reports can only be created for a patient")
        path = f"{patient_uid}/{filename}"
        self._media.upload_medical_report(path, data, content_type)
        return MedicalReportResponse(
            patient_uid=patient_uid, filename=filename, bucket=MEDICAL_REPORTS_BUCKET,
            path=path, content_type=content_type, size_bytes=len(data),
        )

    def list_for_patient(self, patient_uid: str, limit: int, offset: int) -> MedicalReportPage:
        validate_patient_folder(patient_uid)
        rows = self._media.list_medical_reports(patient_uid, limit + 1, offset)
        next_offset = offset + limit if len(rows) > limit else None
        records = []
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._media.report_url_lifetime)
        try:
            for row in rows[:limit]:
                if row.get("id") is None:  # Folder placeholders are not reports.
                    continue
                name = row["name"]
                if not isinstance(name, str) or "/" in name or "\\" in name or name in {".", ".."}:
                    raise ValueError("Invalid report name")
                extension = "." + name.rsplit(".", 1)[-1].lower()
                if extension not in REPORT_TYPES:
                    continue
                size = row["metadata"]["size"]
                if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                    raise ValueError("Invalid report size")
                records.append({
                    "patient_uid": patient_uid, "filename": name, "bucket": MEDICAL_REPORTS_BUCKET,
                    "path": f"{patient_uid}/{name}", "content_type": REPORT_TYPES[extension],
                    "size_bytes": size, "created_at": row.get("created_at"),
                })
            urls = self._media.medical_report_urls([record["path"] for record in records])
            reports = [PatientMedicalReport(**record, download_url=urls[record["path"]],
                                           expires_at=expires_at) for record in records]
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(502, "Supabase returned invalid report data") from exc
        return MedicalReportPage(reports=reports, next_offset=next_offset)
