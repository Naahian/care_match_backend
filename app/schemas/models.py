from datetime import datetime, time
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Role = Literal["admin", "doctor", "patient"]
ADMIN_ROLES: frozenset[str] = frozenset({"admin"})

Weekday = Literal[
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


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


class PaymentCreate(BaseModel):
    appointment_id: str
    amount_cents: int = Field(..., gt=0)
    currency: str = Field(default="usd", min_length=3, max_length=3)
    idempotency_key: str = Field(..., min_length=8, max_length=128)


class PaymentResponse(BaseModel):
    payment_id: str
    appointment_id: str
    amount_cents: int
    currency: str
    status: str
    checkout_url: Optional[str] = None
    created_at: datetime


class VideoTokenRequest(BaseModel):
    appointment_id: str


class VideoTokenResponse(BaseModel):
    appointment_id: str
    room_id: str
    access_token: str
    expires_at: datetime


class UserResponse(BaseModel):
    """User-facing view of an account. Deliberately excludes credential material."""

    uid: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    phone_number: Optional[str] = None
    photo_url: Optional[str] = None
    email_verified: bool = False
    disabled: bool = False
    role: Optional[str] = None
    providers: list[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None


class AdminUserPage(BaseModel):
    users: list[UserResponse] = Field(default_factory=list)
    next_page_token: Optional[str] = None


class AdminUserUpdate(BaseModel):
    role: Role
    disabled: bool = False


class Clinic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    location: str = Field(..., min_length=1, max_length=300)


class TimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: time
    end: time

    @model_validator(mode="after")
    def _end_after_start(self) -> "TimeRange":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class DoctorProfile(BaseModel):
    """Public-facing clinician profile, stored in the ``doctors`` collection.

    ``verified`` records whether the credentials have been reviewed. It is
    always ``False`` on the registration path and is not part of
    ``DoctorCreate``, so a self-registering doctor cannot claim verification.
    """

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(..., min_length=1, max_length=32)
    clinics: list[Clinic] = Field(default_factory=list, max_length=20)
    specialties: list[str] = Field(default_factory=list, max_length=20)
    degrees: list[str] = Field(default_factory=list, max_length=20)
    time_available: list[TimeRange] = Field(default_factory=list, max_length=20)
    days_available: list[Weekday] = Field(default_factory=list, max_length=7)
    verified: bool = False

    @field_validator("specialties", "degrees")
    @classmethod
    def _strip_and_dedupe(cls, values: list[str]) -> list[str]:
        cleaned = [v.strip() for v in values if v and v.strip()]
        return list(dict.fromkeys(cleaned))

    @field_validator("days_available")
    @classmethod
    def _dedupe_days(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


class DoctorSummary(BaseModel):
    """A ``doctors`` document as returned by the doctor directory endpoint.

    Only matching-relevant, non-contact fields. ``email`` and ``phone`` are
    deliberately absent so that listing doctors does not hand out every
    clinician's contact details to any authenticated caller.
    """

    model_config = ConfigDict(extra="ignore")

    uid: str
    display_name: Optional[str] = None
    specialties: list[str] = Field(default_factory=list)
    degrees: list[str] = Field(default_factory=list)
    clinics: list[Clinic] = Field(default_factory=list)
    days_available: list[Weekday] = Field(default_factory=list)
    time_available: list[TimeRange] = Field(default_factory=list)
    verified: bool = False


class PatientCreate(BaseModel):
    """Public self-registration. The role is fixed to ``patient`` by the endpoint.

    There is deliberately no ``role`` field: a client cannot choose its own
    role because there is nowhere to put one.
    """

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., max_length=320)
    password: str = Field(..., min_length=6, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=128)


class DoctorCreate(PatientCreate):
    """Public clinician registration.

    Carries the doctor profile inline rather than as a nested object, and the
    profile is written to the ``doctors`` collection alongside the account.
    There is deliberately no ``role`` or ``verified`` field.
    """

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(..., min_length=1, max_length=32)
    clinics: list[Clinic] = Field(default_factory=list, max_length=20)
    specialties: list[str] = Field(default_factory=list, max_length=20)
    degrees: list[str] = Field(default_factory=list, max_length=20)
    time_available: list[TimeRange] = Field(default_factory=list, max_length=20)
    days_available: list[Weekday] = Field(default_factory=list, max_length=7)

    @model_validator(mode="after")
    def _apply_profile_rules(self) -> "DoctorCreate":
        """Reuse DoctorProfile's normalisation so both paths share one rule set.

        Validating here keeps bad weekdays and reversed time ranges as 422s at
        the request boundary rather than 500s from the service layer.
        """
        profile = DoctorProfile(
            phone=self.phone,
            clinics=self.clinics,
            specialties=self.specialties,
            degrees=self.degrees,
            time_available=self.time_available,
            days_available=self.days_available,
        )
        self.specialties = profile.specialties
        self.degrees = profile.degrees
        self.days_available = profile.days_available
        return self

    def to_profile(self) -> DoctorProfile:
        return DoctorProfile(
            phone=self.phone,
            clinics=self.clinics,
            specialties=self.specialties,
            degrees=self.degrees,
            time_available=self.time_available,
            days_available=self.days_available,
        )


class UserUpdate(BaseModel):
    """Self-service update: profile fields only.

    Any subset may be supplied. A field omitted from the request is left
    unchanged; a field sent as ``null`` or an empty string clears it.

    ``email``, ``password`` and ``role`` are intentionally not updatable here.
    Credentials change through a re-authenticated reset flow, and roles are
    moved only via the admin endpoint. ``extra="forbid"`` makes a client that
    sends one of them fail loudly instead of silently getting a no-op.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: Optional[str] = Field(default=None, max_length=128)
    phone_number: Optional[str] = Field(default=None, max_length=20)
    photo_url: Optional[str] = Field(default=None, max_length=2048)
