from datetime import datetime, timezone
from typing import Any

from fastapi import Depends

from app.repositories.appointment import AppointmentRepository
from app.schemas.models import (
    AppointmentCreate,
    AppointmentResponse,
    AppointmentStatus,
)


class BookingService:
    def __init__(
        self, repository: AppointmentRepository = Depends(AppointmentRepository)
    ) -> None:
        self._repo = repository

    def create(
        self, request: AppointmentCreate, requested_by_uid: str
    ) -> AppointmentResponse:
        if request.scheduled_for <= datetime.now(timezone.utc):
            raise ValueError("scheduled_for must be in the future")

        appointment_id = self._repo.create(
            {
                "doctor_id": request.doctor_id,
                "patient_id": request.patient_id,
                "scheduled_for": request.scheduled_for,
                "reason": request.reason,
                "notes": request.notes,
                "status": AppointmentStatus.REQUESTED.value,
                "requested_by": requested_by_uid,
                "created_at": datetime.now(timezone.utc),
            }
        )

        return AppointmentResponse(
            appointment_id=appointment_id,
            doctor_id=request.doctor_id,
            patient_id=request.patient_id,
            scheduled_for=request.scheduled_for,
            status=AppointmentStatus.REQUESTED,
            reason=request.reason,
            notes=request.notes,
        )

    def transition(
        self,
        appointment_id: str,
        new_status: AppointmentStatus,
        actor_uid: str,
        actor_role: str = "doctor",
    ) -> dict[str, Any]:
        record = self._repo.get_for_user(appointment_id, actor_uid, actor_role)
        if not record:
            raise PermissionError("Appointment not found for this user")

        self._repo.update(
            appointment_id,
            {
                "status": new_status.value,
                "updated_at": datetime.now(timezone.utc),
                "updated_by": actor_uid,
            },
        )
        return self._repo.get(appointment_id) or record

    def list_for(self, user_id: str, role: str) -> list[dict[str, Any]]:
        return self._repo.list_for_user(user_id, role)

    def get(self, appointment_id: str) -> dict[str, Any] | None:
        return self._repo.get(appointment_id)
