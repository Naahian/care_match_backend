import hashlib
import hmac
from datetime import datetime, timezone

from fastapi import Depends

from app.repositories.payment import PaymentRepository
from app.schemas.models import PaymentCreate, PaymentResponse


class PaymentService:
    """Payment intents with idempotency protection.

    The provider call is a stub; wire it to Stripe (or equivalent) and keep the
    webhook-driven status update separate from this synchronous path.
    """

    def __init__(
        self, repository: PaymentRepository = Depends(PaymentRepository)
    ) -> None:
        self._repo = repository

    def _signature(self, payload: PaymentCreate) -> str:
        return hmac.new(
            key=b"care_match_dev_secret",
            msg=f"{payload.appointment_id}:{payload.amount_cents}".encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()

    def create_intent(
        self, payload: PaymentCreate, payer_uid: str
    ) -> PaymentResponse:
        existing = self._repo.get_by_idempotency_key(payload.idempotency_key)
        if existing:
            return PaymentResponse(
                payment_id=existing["id"],
                appointment_id=existing["appointment_id"],
                amount_cents=existing["amount_cents"],
                currency=existing["currency"],
                status=existing["status"],
                checkout_url=existing.get("checkout_url"),
                created_at=existing["created_at"],
            )

        now = datetime.now(timezone.utc)
        signature = self._signature(payload)
        payment_id = self._repo.create(
            {
                "appointment_id": payload.appointment_id,
                "amount_cents": payload.amount_cents,
                "currency": payload.currency,
                "idempotency_key": payload.idempotency_key,
                "payer_uid": payer_uid,
                "status": "pending",
                "provider_signature": signature,
                "created_at": now,
            }
        )

        return PaymentResponse(
            payment_id=payment_id,
            appointment_id=payload.appointment_id,
            amount_cents=payload.amount_cents,
            currency=payload.currency,
            status="pending",
            checkout_url=f"https://checkout.example.com/pay/{payment_id}",
            created_at=now,
        )
