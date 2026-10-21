import hashlib
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app
from app.schemas.payments import PaymentCreate
from app.services.payment import PaymentService


class MemoryPayments:
    def __init__(self):
        self.records = {}

    def reserve(self, payment_id, data):
        if payment_id in self.records:
            return False
        self.records[payment_id] = {"id": payment_id, **data}
        return True

    def get(self, payment_id):
        return self.records.get(payment_id)

    def update(self, payment_id, data):
        self.records[payment_id].update(data)

    def transition(self, payment_id, data):
        if self.records[payment_id]["status"] not in {"paid", "review"}:
            self.update(payment_id, data)
        return self.records[payment_id].copy()


@pytest.fixture
def payload():
    return PaymentCreate(
        appointment_id="appointment-1", amount_cents=12345,
        idempotency_key="checkout-key-1", customer_name="Test Customer",
        customer_email="customer@example.com", customer_phone="01711111111",
        customer_address="Test address", customer_city="Dhaka", customer_postcode="1200",
    )


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(settings, "sslcommerz_store_id", "test-store")
    monkeypatch.setattr(settings, "sslcommerz_store_password", "test-password")
    monkeypatch.setattr(settings, "sslcommerz_sandbox", True)
    monkeypatch.setattr(settings, "sslcommerz_callback_base_url", "https://api.example.com")
    state = {"requests": [], "validation": {}}

    def handler(request):
        state["requests"].append(request)
        if request.method == "POST":
            if state.get("timeout"):
                raise httpx.ReadTimeout("secret upstream details", request=request)
            return httpx.Response(200, json=state.get("checkout", {
                "status": "SUCCESS", "GatewayPageURL": "https://sandbox.sslcommerz.com/checkout",
                "sessionkey": "session-1",
            }))
        return httpx.Response(200, json=state["validation"])

    real_client = httpx.Client
    monkeypatch.setattr("app.services.payment.httpx.Client", lambda **kw: real_client(
        transport=httpx.MockTransport(handler), **kw
    ))
    return state


@pytest.fixture
def service():
    return PaymentService(MemoryPayments())


def validate(gateway, payment, **overrides):
    gateway["validation"] = {
        "status": "VALID", "tran_id": payment.payment_id, "val_id": "validation-1",
        "currency_type": payment.currency, "currency_amount": "123.45", "risk_level": "0",
        **overrides,
    }
    return {"tran_id": payment.payment_id, "val_id": "validation-1", "status": "VALID"}


def signed_failure(payment_id, status="FAILED"):
    fields = {"tran_id": payment_id, "status": status}
    values = {**fields, "store_passwd": hashlib.md5(b"test-password").hexdigest()}
    fields["verify_sign"] = hashlib.md5("&".join(
        f"{key}={values[key]}" for key in sorted(values)
    ).encode()).hexdigest()
    fields["verify_key"] = "tran_id,status"
    return fields


def test_checkout_persists_url_and_reuses_session(service, payload, gateway):
    payment = service.create_intent(payload, "patient-1")
    repeated = service.create_intent(payload, "patient-1")
    assert repeated == payment
    assert payment.status == "pending"
    assert len(gateway["requests"]) == 1
    request = gateway["requests"][0]
    assert request.url.host == "sandbox.sslcommerz.com"
    data = parse_qs(request.content.decode())
    assert data["total_amount"] == ["123.45"]
    assert data["currency"] == ["BDT"]
    assert data["tran_id"] == [payment.payment_id]
    assert len(payment.payment_id) <= 30
    assert data["ipn_url"] == ["https://api.example.com/api/v1/payments/sslcommerz/ipn"]
    assert data["cus_email"] == [payload.customer_email]
    assert service.get_payment(payment.payment_id, "patient-1") == payment


def test_idempotency_rejects_changes_and_scopes_to_payer(service, payload, gateway):
    payment = service.create_intent(payload, "patient-1")
    with pytest.raises(HTTPException) as error:
        service.create_intent(payload.model_copy(update={"amount_cents": 10}), "patient-1")
    assert error.value.status_code == 409
    other = service.create_intent(payload, "patient-2")
    assert other.payment_id != payment.payment_id
    with pytest.raises(HTTPException) as error:
        service.get_payment(payment.payment_id, "patient-2")
    assert error.value.status_code == 404


@pytest.mark.parametrize("failure", ["timeout", "checkout"])
def test_failed_initialization_does_not_create_second_session(service, payload, gateway, failure):
    gateway[failure] = True if failure == "timeout" else {"status": "FAILED"}
    with pytest.raises(HTTPException) as error:
        service.create_intent(payload, "patient-1")
    assert error.value.status_code == 502
    assert "secret" not in error.value.detail
    with pytest.raises(HTTPException) as error:
        service.create_intent(payload, "patient-1")
    assert error.value.status_code == 409
    assert len(gateway["requests"]) == 1


def test_missing_credentials(service, payload, gateway, monkeypatch):
    monkeypatch.setattr(settings, "sslcommerz_store_password", "")
    with pytest.raises(HTTPException) as error:
        service.create_intent(payload, "patient-1")
    assert error.value.status_code == 503
    assert not service._repo.records


def test_verified_success_and_repeated_ipn(service, payload, gateway):
    payment = service.create_intent(payload, "patient-1")
    callback = validate(gateway, payment)
    assert service.handle_callback(callback, "success").status == "paid"
    gateway["validation"]["status"] = "VALIDATED"
    assert service.handle_callback(callback, "ipn").status == "paid"
    assert service.handle_callback(signed_failure(payment.payment_id), "fail").status == "paid"


@pytest.mark.parametrize("overrides", [
    {"tran_id": "another-payment"}, {"currency_amount": "0.01"},
    {"currency_type": "USD"}, {"val_id": "another-validation"},
    {"status": "INVALID_TRANSACTION"}, {"currency_amount": "invalid"},
])
def test_validation_mismatch_never_marks_paid(service, payload, gateway, overrides):
    payment = service.create_intent(payload, "patient-1")
    callback = validate(gateway, payment, **overrides)
    with pytest.raises(HTTPException) as error:
        service.handle_callback(callback, "ipn")
    assert error.value.status_code == 400
    assert service.get_payment(payment.payment_id, "patient-1").status == "pending"


def test_risky_payment_is_held(service, payload, gateway):
    payment = service.create_intent(payload, "patient-1")
    callback = validate(gateway, payment, risk_level="1")
    assert service.handle_callback(callback, "ipn").status == "review"


@pytest.mark.parametrize("status,expected", [("FAILED", "failed"), ("CANCELLED", "cancelled")])
def test_failure_requires_valid_signature(service, payload, gateway, status, expected):
    payment = service.create_intent(payload, "patient-1")
    with pytest.raises(HTTPException):
        service.handle_callback({"tran_id": payment.payment_id, "status": status}, "ipn")
    assert service.get_payment(payment.payment_id, "patient-1").status == "pending"
    assert service.handle_callback(signed_failure(payment.payment_id, status), "ipn").status == expected
    callback = validate(gateway, payment)
    assert service.handle_callback(callback, "ipn").status == "paid"


def test_callback_route_accepts_provider_form_without_bearer_token(service, payload, gateway):
    payment = service.create_intent(payload, "patient-1")
    callback = validate(gateway, payment)
    app = create_app()
    app.dependency_overrides[PaymentService] = lambda: service
    client = TestClient(app)
    response = client.post("/api/v1/payments/sslcommerz/ipn", data=callback)
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "paid"
    assert client.post("/api/v1/payments/sslcommerz/ipn", json=callback).status_code == 415
    assert client.post("/api/v1/payments/sslcommerz/ipn", content="tran_id=a&tran_id=b",
                       headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 400


def test_live_endpoint(service, payload, gateway, monkeypatch):
    monkeypatch.setattr(settings, "sslcommerz_sandbox", False)
    service.create_intent(payload, "patient-1")
    assert gateway["requests"][0].url.host == "securepay.sslcommerz.com"
