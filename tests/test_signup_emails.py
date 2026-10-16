import json
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from firebase_admin import auth as firebase_auth
from firebase_admin import exceptions as firebase_exceptions

from app.core.config import settings
from app.main import app
from app.schemas.users import UserResponse
from app.services import account_emails
from app.services.account_emails import AccountEmailService
from app.services.users import UserService


@pytest.fixture
def delivery(monkeypatch):
    monkeypatch.setattr(settings, "firebase_api_key", "test-api-key")
    generate = Mock(return_value=b"private-custom-token")
    monkeypatch.setattr(firebase_auth, "create_custom_token", generate)
    state = {"requests": [], "token_status": 200, "send_status": 200,
             "token_body": {"idToken": "private-id-token"}, "generate": generate}

    def handle(request):
        state["requests"].append(request)
        if state.get("network_error"):
            raise httpx.ConnectError("offline", request=request)
        if request.url.path.endswith(":signInWithCustomToken"):
            return httpx.Response(state["token_status"], json=state["token_body"])
        return httpx.Response(state["send_status"], json={"email": "new@example.com"})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        account_emails.httpx, "AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs),
    )
    return state


@pytest.mark.parametrize("kind", ["patient", "doctor"])
@pytest.mark.parametrize("failed", [False, True])
def test_signup_sends_firebase_verification_template(delivery, monkeypatch, kind, failed):
    monkeypatch.setattr(UserService, "__init__", lambda self, doctor_repo=None: None)
    record = UserResponse(uid="new-uid", email="new@example.com")
    monkeypatch.setattr(UserService, f"create_{kind}", lambda self, payload: record)
    if failed:
        delivery["send_status"] = 429
    body = {"email": record.email, "password": "secret123"}
    if kind == "doctor":
        body["phone"] = "+15550100"
    response = TestClient(app).post(f"/api/v1/users/{kind}", json=body)
    assert response.status_code == 201
    assert response.json()["verification_email_sent"] is (not failed)
    assert response.json()["data"]["uid"] == record.uid
    delivery["generate"].assert_called_once_with(record.uid)
    exchange, send = delivery["requests"]
    assert exchange.url.path == "/v1/accounts:signInWithCustomToken"
    assert json.loads(exchange.content) == {
        "token": "private-custom-token", "returnSecureToken": True
    }
    assert send.url.path == "/v1/accounts:sendOobCode"
    assert json.loads(send.content) == {
        "requestType": "VERIFY_EMAIL", "idToken": "private-id-token"
    }
    for request in delivery["requests"]:
        assert request.url.params["key"] == "test-api-key"
        assert "password" not in json.loads(request.content)
    for secret in ("private-custom-token", "private-id-token", "secret123"):
        assert secret not in response.text


async def test_missing_api_key_fails_before_generating_token(delivery, monkeypatch):
    monkeypatch.setattr(settings, "firebase_api_key", None)
    with pytest.raises(HTTPException) as exc:
        await AccountEmailService().send_signup_verification("new-uid")
    assert exc.value.status_code == 503
    assert "FIREBASE_API_KEY" in exc.value.detail
    delivery["generate"].assert_not_called()
    assert not delivery["requests"]


async def test_firebase_signing_failure_does_not_request_email(delivery):
    delivery["generate"].side_effect = firebase_exceptions.UnavailableError("unavailable")
    with pytest.raises(HTTPException) as exc:
        await AccountEmailService().send_signup_verification("new-uid")
    assert exc.value.status_code == 502
    assert not delivery["requests"]


@pytest.mark.parametrize("body", [{}, {"idToken": ""}, {"idToken": None}])
async def test_invalid_exchange_response_does_not_request_email(delivery, body):
    delivery["token_body"] = body
    with pytest.raises(HTTPException) as exc:
        await AccountEmailService().send_signup_verification("new-uid")
    assert exc.value.status_code == 502
    assert len(delivery["requests"]) == 1


@pytest.mark.parametrize("failure", ["network", "token_rejected"])
async def test_exchange_failure_is_translated(delivery, failure):
    if failure == "network":
        delivery["network_error"] = True
    else:
        delivery["token_status"] = 400
    with pytest.raises(HTTPException) as exc:
        await AccountEmailService().send_signup_verification("new-uid")
    assert exc.value.status_code == 502
    assert len(delivery["requests"]) == 1
