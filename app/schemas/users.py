from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.responses import SuccessResponse


class UserPersonalDetails(BaseModel):
    date_of_birth: Optional[date] = None
    gender: Optional[Literal["Male", "Female", "Other"]] = None
    blood_group: Optional[
        Literal["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
    ] = None

    @field_validator("date_of_birth", "gender", "blood_group", mode="before")
    @classmethod
    def normalize_personal_details(cls, value, info):
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            if info.field_name == "gender":
                return value.capitalize()
            if info.field_name == "blood_group":
                return value.upper()
        if info.field_name == "date_of_birth" and value is not None:
            if isinstance(value, datetime) or not isinstance(value, (str, date)):
                raise ValueError("date_of_birth must be a date in YYYY-MM-DD format")
            if isinstance(value, str) and (
                len(value) != 10 or value[4] != "-" or value[7] != "-"
            ):
                raise ValueError("date_of_birth must be a date in YYYY-MM-DD format")
        return value

    @field_validator("date_of_birth")
    @classmethod
    def validate_date_of_birth(cls, value: Optional[date]) -> Optional[date]:
        if value is not None and value > date.today():
            raise ValueError("Date of birth cannot be in the future")
        return value


class UserResponse(UserPersonalDetails):
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


class UserCreateResponse(SuccessResponse[UserResponse]):
    verification_email_sent: bool


class PatientCreate(BaseModel):
    """Public self-registration. The role is fixed to ``patient`` by the endpoint.

    There is deliberately no ``role`` field: a client cannot choose its own
    role because there is nowhere to put one.
    """

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., max_length=320)
    password: str = Field(..., min_length=6, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=128)


class UserUpdate(UserPersonalDetails):
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
    remove_photo: bool = False
