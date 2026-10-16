from pydantic import BaseModel, ConfigDict, Field

from app.schemas.responses import SuccessResponse


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(
        ..., max_length=320, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$"
    )


class AccountEmailResponse(SuccessResponse[None]):
    """Acknowledgment that an account action email was requested."""

    data: None = None
