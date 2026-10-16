from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AppointmentStatus(str, Enum):
    REQUESTED = "requested"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AppointmentCreate(BaseModel):
    doctor_id: str
    patient_id: str
    scheduled_for: datetime
    reason: str = Field(..., min_length=1, max_length=1000)
    notes: Optional[str] = Field(default=None, max_length=2000)


class AppointmentResponse(BaseModel):
    appointment_id: str
    doctor_id: str
    patient_id: str
    scheduled_for: datetime
    status: AppointmentStatus
    reason: str
    notes: Optional[str] = None
    video_room_id: Optional[str] = None
