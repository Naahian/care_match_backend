"""Shared API response bodies."""

from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, Field

ResponseData = TypeVar("ResponseData")


class SuccessResponse(BaseModel, Generic[ResponseData]):
    status: int = Field(default=200, ge=200, le=299)
    msg: str = "Success"
    data: ResponseData


class ErrorResponse(BaseModel):
    status: int
    msg: str
    error: Any


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    environment: Optional[str] = None
