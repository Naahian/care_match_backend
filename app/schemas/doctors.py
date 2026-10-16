from datetime import time
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import Weekday
from app.schemas.users import PatientCreate


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
