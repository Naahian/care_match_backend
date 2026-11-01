import re
import unicodedata

from fastapi import HTTPException
from firebase_admin import auth as firebase_auth
from firebase_admin import exceptions as firebase_exceptions

from app.schemas.prescriptions import PrescriptionPage, PrescriptionResponse
from app.services.media import PRESCRIPTION_BUCKET
from app.services.medical_reports import MedicalReportService, prepare_medical_report, validate_patient_folder


class PrescriptionService(MedicalReportService):
    bucket = PRESCRIPTION_BUCKET

    def create_prescription(self, doctor_uid: str, patient_uid: str, data: bytes) -> PrescriptionResponse:
        validate_patient_folder(patient_uid)
        _, image, content_type = prepare_medical_report("Prescription.jpg", data)
        try:
            doctor = firebase_auth.get_user(doctor_uid)
            patient = firebase_auth.get_user(patient_uid)
        except firebase_auth.UserNotFoundError as exc:
            raise HTTPException(404, "Doctor or patient not found") from exc
        except ValueError as exc:
            raise HTTPException(422, "Invalid user UID") from exc
        except firebase_exceptions.FirebaseError as exc:
            raise HTTPException(503, "Failed to reach Firebase Auth") from exc
        if (doctor.custom_claims or {}).get("role") != "doctor":
            raise HTTPException(403, "Doctor privileges required")
        if (patient.custom_claims or {}).get("role") != "patient":
            raise HTTPException(422, "Prescriptions can only be created for a patient")
        doctor_name = doctor.display_name or "Doctor"
        ascii_name = unicodedata.normalize("NFKD", doctor_name).encode("ascii", "ignore").decode()
        name = re.sub(r"[^A-Za-z0-9 ._()-]", "_", ascii_name).strip(" ._")[:120] or "Doctor"

        # Count all files, with pagination, excluding folder placeholders.
        count, offset = 0, 0
        while True:
            rows = self._media.list_documents(self.bucket, patient_uid, 1000, offset)
            count += sum(row.get("id") is not None for row in rows)
            if len(rows) < 1000:
                break
            offset += len(rows)
        # Storage's create-only upload is the concurrency guard. A conflict
        # (or a gap left by a deletion) advances the suffix without overwriting.
        for index in range(count, count + 10):
            filename = f"{name}_{index}.jpg"
            path = f"{patient_uid}/{filename}"
            try:
                self._media.upload_document(self.bucket, path, image, content_type)
            except HTTPException as exc:
                if exc.status_code == 409:
                    continue
                raise
            return PrescriptionResponse(
                patient_uid=patient_uid, doctor_uid=doctor_uid, doctor_name=doctor_name,
                index=index, filename=filename, bucket=self.bucket, path=path,
                content_type=content_type, size_bytes=len(image),
            )
        raise HTTPException(409, "Prescription filenames are busy; retry the upload")

    def list_prescriptions(self, patient_uid: str, limit: int, offset: int) -> PrescriptionPage:
        page = self.list_for_patient(patient_uid, limit, offset)
        return PrescriptionPage(prescriptions=page.reports, next_offset=page.next_offset)
