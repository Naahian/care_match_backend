from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Urgency(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EMERGENCY = "emergency"


class TriageRequest(BaseModel):
    symptoms: str = Field(..., min_length=1, max_length=4000)
    age: Optional[int] = Field(default=None, ge=0, le=130)
    duration_days: Optional[int] = Field(default=None, ge=0, le=3650)
    medical_history: Optional[str] = Field(default=None, max_length=2000)
    current_medications: list[str] = Field(default_factory=list)
    location: Optional[str] = Field(default=None, max_length=200)


class TriageResponse(BaseModel):
    triage_id: str
    urgency: Urgency
    summary: str
    recommended_specialties: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    created_at: datetime


class TriageDeleteResponse(BaseModel):
    deleted: int = Field(..., ge=0)
