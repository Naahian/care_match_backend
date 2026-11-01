from pydantic import BaseModel

from app.schemas.medical_reports import MedicalReportResponse, PatientMedicalReport


class PrescriptionResponse(MedicalReportResponse):
    doctor_uid: str
    doctor_name: str
    index: int


class PrescriptionPage(BaseModel):
    prescriptions: list[PatientMedicalReport]
    next_offset: int | None = None
