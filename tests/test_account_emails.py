import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services import account_emails


@pytest.fixture
def firebase_email(monkeypatch):
    monkeypatch.setattr(settings, "firebase_api_key", "test-api-key")
    state = {"status": 200, "body": {"email": "user@example.com"}, "requests": []}

    def handle(request):
        state["requests"].append(request)
        if state.get("network_error"):
            raise httpx.ConnectError("offline", request=request)
        if state.get("malformed"):
            return httpx.Response(state["status"], text="upstream failure")
        return httpx.Response(state["status"], json=state["body"])

    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        account_emails.httpx,
        "AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs),
    )
    return state


def test_password_reset_is_public_and_sends_email(firebase_email):
    response = TestClient(app).post(
        "/api/v1/users/reset-password", json={"email": "user@example.com"}
    )
    assert response.status_code == 200
    assert json.loads(firebase_email["requests"][0].content) == {
        "requestType": "PASSWORD_RESET", "email": "user@example.com"
    }


@pytest.mark.parametrize("error", ["EMAIL_NOT_FOUND", "USER_NOT_FOUND", "USER_DISABLED"])
def test_reset_does_not_reveal_account_existence(firebase_email, error):
    client = TestClient(app)
    body = {"email": "user@example.com"}
    existing = client.post("/api/v1/users/reset-password", json=body)
    firebase_email.update(status=400, body={"error": {"message": error}})
    missing = client.post("/api/v1/users/reset-password", json=body)
    assert missing.status_code == existing.status_code == 200
    assert missing.json() == existing.json()


@pytest.mark.parametrize("body", [{}, {"email": "invalid"}, {"email": "a @b.com"},
                                      {"email": "a@b.com", "uid": "other"}])
def test_reset_validates_input(firebase_email, body):
    response = TestClient(app).post("/api/v1/users/reset-password", json=body)
    assert response.status_code == 422
    assert not firebase_email["requests"]


@pytest.mark.parametrize("error,expected", [
    ("TOO_MANY_ATTEMPTS_TRY_LATER", 429),
    ("QUOTA_EXCEEDED", 429), ("INVALID_EMAIL", 400), ("API_KEY_INVALID", 502),
])
async def test_firebase_errors_are_translated(firebase_email, error, expected):
    firebase_email.update(status=400, body={"error": {"message": error}})
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await account_emails.AccountEmailService().send_password_reset("user@example.com")
    assert exc.value.status_code == expected
    assert error not in exc.value.detail


@pytest.mark.parametrize("failure", ["network_error", "malformed"])
def test_upstream_failures_return_502(firebase_email, failure):
    firebase_email.update(status=503, **{failure: True})
    response = TestClient(app).post(
        "/api/v1/users/reset-password", json={"email": "user@example.com"}
    )
    assert response.status_code == 502


def test_missing_configuration_returns_503(firebase_email, monkeypatch):
    monkeypatch.setattr(settings, "firebase_api_key", None)
    response = TestClient(app).post(
        "/api/v1/users/reset-password", json={"email": "user@example.com"}
    )
    assert response.status_code == 503
    assert not firebase_email["requests"]


def test_standalone_verification_endpoint_is_removed(firebase_email):
    client = TestClient(app)
    assert client.post("/api/v1/users/verify-email").status_code == 404
    assert "/api/v1/users/verify-email" not in client.get("/openapi.json").json()["paths"]
    assert not firebase_email["requests"]
