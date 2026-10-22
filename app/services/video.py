from datetime import datetime, timedelta, timezone

from agora_token_builder import RtcTokenBuilder
from agora_token_builder.RtcTokenBuilder import Role_Publisher
from fastapi import Depends, HTTPException, status

from app.core.config import Settings, get_settings
from app.repositories.appointment import AppointmentRepository
from app.schemas.video import VideoTokenResponse

TOKEN_TTL_MINUTES = 60


class VideoService:
    """Issues short-lived room access tokens for teleconsultation video."""

    def __init__(
        self,
        repository: AppointmentRepository = Depends(AppointmentRepository),
        settings: Settings = Depends(get_settings),
    ) -> None:
        self._repo = repository
        self._settings = settings

    def issue_token(
        self, appointment_id: str, user_id: str, role: str
    ) -> VideoTokenResponse:
        appointment = self._repo.get_for_user(appointment_id, user_id, role)
        if not appointment:
            raise PermissionError("Appointment not found for this user")

        if not self._settings.agora_app_id or not self._settings.agora_app_certificate:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Agora video credentials are not configured",
            )

        expires_at = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MINUTES)
        # UID 0 allows the RTC client to join with an SDK-assigned numeric UID.
        token = RtcTokenBuilder.buildTokenWithUid(
            self._settings.agora_app_id,
            self._settings.agora_app_certificate,
            appointment_id,
            0,
            Role_Publisher,
            int(expires_at.timestamp()),
        )
        return VideoTokenResponse(
            token=token,
            channelName=appointment_id,
        )
