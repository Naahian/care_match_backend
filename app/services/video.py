import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends

from app.repositories.appointment import AppointmentRepository
from app.schemas.models import VideoTokenResponse

TOKEN_TTL_MINUTES = 60


class VideoService:
    """Issues short-lived room access tokens for teleconsultation video."""

    def __init__(
        self, repository: AppointmentRepository = Depends(AppointmentRepository)
    ) -> None:
        self._repo = repository

    def issue_token(
        self, appointment_id: str, user_id: str, role: str
    ) -> VideoTokenResponse:
        appointment = self._repo.get_for_user(appointment_id, user_id, role)
        if not appointment:
            raise PermissionError("Appointment not found for this user")

        room_id = appointment.get("video_room_id") or f"room-{appointment_id}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MINUTES)

        if not appointment.get("video_room_id"):
            self._repo.update(appointment_id, {"video_room_id": room_id})

        return VideoTokenResponse(
            appointment_id=appointment_id,
            room_id=room_id,
            access_token=secrets.token_urlsafe(32),
            expires_at=expires_at,
        )
