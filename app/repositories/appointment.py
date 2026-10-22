from typing import Any, Optional

from app.repositories.base import BaseRepository


class AppointmentRepository(BaseRepository):
    collection_name = "appointments"

    @staticmethod
    def _with_id(record: dict[str, Any]) -> dict[str, Any]:
        record = dict(record)
        record["appointment_id"] = record.pop("id")
        return record

    def list_for_user(self, user_id, role, limit=50):
        field = "doctor_id" if role == "doctor" else "patient_id"
        return [self._with_id(r) for r in self.list(limit=limit, **{field: user_id})]

    def list_by_doctor(self, doctor_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.list(limit=limit, doctor_id=doctor_id)

    def get_for_user(
        self, appointment_id: str, user_id: str, role: str
    ) -> Optional[dict[str, Any]]:
        record = self.get(appointment_id)
        if not record:
            return None
        if role == "admin":
            return record
        if role not in {"doctor", "patient"}:
            return None
        # Account roles do not determine which side of an appointment a user
        # occupies (a doctor can also book an appointment as a patient).
        return record if user_id in (record.get("doctor_id"), record.get("patient_id")) else None
