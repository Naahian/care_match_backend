from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class PaymentCreate(BaseModel):
    appointment_id: str
    amount_cents: int = Field(..., gt=0)
    currency: str = Field(default="BDT", pattern=r"^[A-Za-z]{3}$")
    idempotency_key: str = Field(..., min_length=8, max_length=128)
    customer_name: str = Field(..., min_length=1, max_length=50)
    customer_email: str = Field(..., max_length=50, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    customer_phone: str = Field(..., min_length=6, max_length=20)
    customer_address: str = Field(..., min_length=1, max_length=50)
    customer_city: str = Field(..., min_length=1, max_length=50)
    customer_postcode: str = Field(..., min_length=1, max_length=30)
    customer_country: str = Field(default="Bangladesh", min_length=1, max_length=50)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class PaymentResponse(BaseModel):
    payment_id: str
    appointment_id: str
    amount_cents: int
    currency: str
    status: str
    checkout_url: Optional[str] = None
    created_at: datetime
