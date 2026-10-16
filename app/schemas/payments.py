from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


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
