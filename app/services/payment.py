import hashlib
import hmac
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from fastapi import Depends, HTTPException

from app.core.config import settings
from app.repositories.payment import PaymentRepository
from app.schemas.payments import PaymentCreate, PaymentResponse


class PaymentService:
    """SSLCOMMERZ hosted checkout with server-verified callbacks."""

    def __init__(self, repository: PaymentRepository = Depends(PaymentRepository)) -> None:
        self._repo = repository

    @staticmethod
    def _credentials() -> dict[str, str]:
        if not settings.sslcommerz_store_id or not settings.sslcommerz_store_password:
            raise HTTPException(503, "SSLCOMMERZ store credentials are not configured")
        return {
            "store_id": settings.sslcommerz_store_id,
            "store_passwd": settings.sslcommerz_store_password,
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        host = "sandbox" if settings.sslcommerz_sandbox else "securepay"
        try:
            with httpx.Client(timeout=settings.sslcommerz_timeout_seconds) as client:
                response = client.request(method, f"https://{host}.sslcommerz.com{path}", **kwargs)
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError("Invalid response")
                return result
        except (httpx.HTTPError, ValueError) as exc:
            # Do not expose provider URLs or bodies containing credentials.
            raise HTTPException(502, "SSLCOMMERZ request failed") from exc

    @staticmethod
    def _response(payment: dict[str, Any]) -> PaymentResponse:
        return PaymentResponse(
            payment_id=payment["id"],
            appointment_id=payment["appointment_id"],
            amount_cents=payment["amount_cents"],
            currency=payment["currency"],
            status=payment["status"],
            checkout_url=payment.get("checkout_url"),
            created_at=payment["created_at"],
        )

    def create_intent(self, payload: PaymentCreate, payer_uid: str) -> PaymentResponse:
        credentials = self._credentials()
        # Atomic reservation prevents concurrent requests from creating two sessions.
        identity = f"{len(payer_uid)}:{payer_uid}:{payload.idempotency_key}"
        payment_id = hashlib.sha256(identity.encode()).hexdigest()[:30]
        fingerprint = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
        payment = {
            "appointment_id": payload.appointment_id,
            "amount_cents": payload.amount_cents,
            "currency": payload.currency,
            "idempotency_key": payload.idempotency_key,
            "request_fingerprint": fingerprint,
            "payer_uid": payer_uid,
            "provider": "sslcommerz",
            "status": "initializing",
            "created_at": datetime.now(timezone.utc),
        }
        if not self._repo.reserve(payment_id, payment):
            existing = self._repo.get(payment_id)
            if not existing or existing.get("request_fingerprint") != fingerprint:
                raise HTTPException(409, "Idempotency key was used with different payment details")
            if existing["status"] in {"initializing", "initialization_failed"}:
                raise HTTPException(409, "Checkout initialization is incomplete; reconcile before creating a new payment")
            return self._response(existing)

        callback = f"{settings.sslcommerz_callback_base_url.rstrip('/')}{settings.api_v1_prefix}/payments/sslcommerz"
        data = {
            **credentials,
            "total_amount": format(Decimal(payload.amount_cents) / 100, ".2f"),
            "currency": payload.currency,
            "tran_id": payment_id,
            "success_url": f"{callback}/success",
            "fail_url": f"{callback}/fail",
            "cancel_url": f"{callback}/cancel",
            "ipn_url": f"{callback}/ipn",
            "shipping_method": "NO",
            "product_name": "Medical appointment",
            "product_category": "Healthcare",
            "product_profile": "non-physical-goods",
            "cus_name": payload.customer_name,
            "cus_email": payload.customer_email,
            "cus_phone": payload.customer_phone,
            "cus_add1": payload.customer_address,
            "cus_city": payload.customer_city,
            "cus_postcode": payload.customer_postcode,
            "cus_country": payload.customer_country,
        }
        try:
            result = self._request("POST", "/gwprocess/v4/api.php", data=data)
            checkout_url = result.get("GatewayPageURL")
            if (
                result.get("status") != "SUCCESS"
                or not isinstance(checkout_url, str)
                or not checkout_url.startswith("https://")
            ):
                raise HTTPException(502, "SSLCOMMERZ could not initialize checkout")
        except HTTPException:
            self._repo.transition(payment_id, {"status": "initialization_failed"})
            raise
        self._repo.update(payment_id, {
            "checkout_url": checkout_url,
            "provider_session_key": result.get("sessionkey"),
        })
        stored = self._repo.transition(payment_id, {"status": "pending"})
        return self._response(stored)

    def get_payment(self, payment_id: str, payer_uid: str) -> PaymentResponse:
        payment = self._repo.get(payment_id)
        if not payment or payment.get("payer_uid") != payer_uid:
            raise HTTPException(404, "Payment not found")
        return self._response(payment)

    def _verify_failure_signature(self, payload: dict[str, str]) -> bool:
        credentials = self._credentials()
        keys = payload.get("verify_key", "").split(",")
        if not {"tran_id", "status"}.issubset(keys) or any(key not in payload for key in keys):
            return False
        values = {
            key: payload[key] for key in keys
            if key not in {"verify_sign", "verify_key", "store_passwd"}
        }
        values["store_passwd"] = hashlib.md5(credentials["store_passwd"].encode()).hexdigest()
        encoded = "&".join(f"{key}={values[key]}" for key in sorted(values))
        expected = hashlib.md5(encoded.encode()).hexdigest()
        return hmac.compare_digest(expected, payload.get("verify_sign", ""))

    def handle_callback(self, payload: dict[str, str], event: str) -> PaymentResponse:
        payment_id = payload.get("tran_id", "")
        if not payment_id or "/" in payment_id:
            raise HTTPException(400, "Missing or invalid transaction ID")
        payment = self._repo.get(payment_id)
        if not payment or payment.get("provider") != "sslcommerz":
            raise HTTPException(404, "Payment not found")
        status = payload.get("status", "")
        if status in {"VALID", "VALIDATED"}:
            if not payload.get("val_id"):
                raise HTTPException(400, "Missing validation ID")
            result = self._request("GET", "/validator/api/validationserverAPI.php", params={
                **self._credentials(), "val_id": payload["val_id"], "format": "json",
            })
            currency = result.get("currency_type", result.get("currency"))
            amount = result.get("currency_amount", result.get("amount"))
            try:
                amount_matches = Decimal(str(amount)) == Decimal(payment["amount_cents"]) / 100
            except InvalidOperation:
                amount_matches = False
            if (
                result.get("status") not in {"VALID", "VALIDATED"}
                or result.get("tran_id") != payment_id
                or result.get("val_id") != payload["val_id"]
                or currency != payment["currency"]
                or not amount_matches
            ):
                raise HTTPException(400, "SSLCOMMERZ transaction validation failed")
            update = {
                "status": "paid" if str(result.get("risk_level")) == "0" else "review",
                "provider_validation_id": result["val_id"],
                "provider_bank_transaction_id": result.get("bank_tran_id"),
                "updated_at": datetime.now(timezone.utc),
            }
        elif status in {"FAILED", "CANCELLED"} and event in {"fail", "cancel", "ipn"}:
            if not self._verify_failure_signature(payload):
                raise HTTPException(400, "Invalid SSLCOMMERZ callback signature")
            update = {
                "status": "failed" if status == "FAILED" else "cancelled",
                "updated_at": datetime.now(timezone.utc),
            }
        else:
            raise HTTPException(400, "Unsupported SSLCOMMERZ callback status")
        return self._response(self._repo.transition(payment_id, update))
