from datetime import datetime

from pydantic import BaseModel


class MedicalReportResponse(BaseModel):
    patient_uid: str
    filename: str
    bucket: str
    path: str
    content_type: str
    size_bytes: int


class PatientMedicalReport(MedicalReportResponse):
    created_at: datetime | None = None
    download_url: str
    expires_at: datetime


class MedicalReportPage(BaseModel):
    reports: list[PatientMedicalReport]
    next_offset: int | None = None
