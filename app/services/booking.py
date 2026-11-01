from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, HTTPException

from app.repositories.appointment import AppointmentRepository
from app.schemas.appointments import (
    AppointmentCreate,
    AppointmentResponse,
    AppointmentStatus,
)
from app.services.users import UserService


class BookingService:
    def __init__(
        self, repository: AppointmentRepository = Depends(AppointmentRepository),
        users: UserService = Depends(UserService),
    ) -> None:
        self._repo = repository
        self._users = users

    def _response_record(self, record: dict[str, Any]) -> dict[str, Any]:
        try:
            name = self._users.get_self(record["patient_id"], include_profile=False).display_name
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            name = None
        return {
            **record,
            "appointment_id": record.get("appointment_id", record.get("id")),
            "patient_name": name,
        }

    def create(
        self, request: AppointmentCreate, requested_by_uid: str
    ) -> AppointmentResponse:
        if request.scheduled_for <= datetime.now(timezone.utc):
            raise ValueError("scheduled_for must be in the future")

        response = self._response_record(request.model_dump())

        appointment_id = self._repo.create(
            {
                "doctor_id": request.doctor_id,
                "patient_id": request.patient_id,
                "appointment_type": request.appointment_type.value,
                "scheduled_for": request.scheduled_for,
                "reason": request.reason,
                "notes": request.notes,
                "status": AppointmentStatus.REQUESTED.value,
                "requested_by": requested_by_uid,
                "created_at": datetime.now(timezone.utc),
            }
        )

        return AppointmentResponse.model_validate({
            **response, "appointment_id": appointment_id,
            "status": AppointmentStatus.REQUESTED,
        })

    def transition(
        self,
        appointment_id: str,
        new_status: AppointmentStatus,
        actor_uid: str,
        actor_role: str = "doctor",
    ) -> dict[str, Any]:
        record = self._repo.get_for_user(appointment_id, actor_uid, actor_role)
        if (actor_role not in {"doctor", "admin"} or not record
            or (actor_role == "doctor" and record.get("doctor_id") != actor_uid)):
            raise PermissionError("Appointment not found for this user")

        updated = self._repo.transition(appointment_id, new_status.value, actor_uid, actor_role)
        if not updated:
            raise PermissionError("Appointment not found for this user")
        if updated.get("status") == "completed":
            self._users.sync_doctor_earnings(updated["doctor_id"])
        return self._response_record(updated)

    def list_for(self, user_id: str, role: str) -> list[dict[str, Any]]:
        return [self._response_record(record) for record in self._repo.list_for_user(user_id, role)]

    def get(self, appointment_id: str) -> dict[str, Any] | None:
        return self._repo.get(appointment_id)
