from enum import Enum

from pydantic import BaseModel, Field


class TriageUrgency(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EMERGENCY = "emergency"


class TriageOutput(BaseModel):
    """Structured schema the triage model must return."""

    urgency: TriageUrgency
    summary: str = Field(..., max_length=500)
    recommended_specialties: list[str] = Field(default_factory=list, max_length=5)
    red_flags: list[str] = Field(default_factory=list, max_length=10)
    needs_human_review: bool = False
