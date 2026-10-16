from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.responses import SuccessResponse


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
