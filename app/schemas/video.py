from datetime import datetime

from pydantic import BaseModel


class VideoTokenRequest(BaseModel):
    appointment_id: str


class VideoTokenResponse(BaseModel):
    appointment_id: str
    room_id: str
    access_token: str
    expires_at: datetime
