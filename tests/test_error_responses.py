import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, model_validator

from app.main import create_app


class Payload(BaseModel):
    password: str

    @model_validator(mode="after")
    def reject_password(self):
        raise ValueError("Invalid payload")


@pytest.fixture
def error_client():
    app = create_app()

    @app.get("/test/http/{status_code}")
    def http_error(status_code: int):
        raise HTTPException(
            status_code=status_code,
            detail={"reason": "Test failure"},
            headers={"X-Error": "preserved"},
        )

    @app.post("/test/validation")
    def validation(payload: Payload):
        return payload

    @app.get("/test/unhandled")
    def unhandled():
        raise RuntimeError("Upstream connection failed")

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 409, 422, 502, 503])
def test_http_errors_preserve_status_detail_and_headers(error_client, status_code):
    response = error_client.get(f"/test/http/{status_code}")

    assert response.status_code == status_code
    assert response.headers["X-Error"] == "preserved"
    body = response.json()
    assert set(body) == {"status", "msg", "error"}
    assert body["status"] == status_code
    assert body["msg"]
    assert body["error"] == {"reason": "Test failure"}


@pytest.mark.parametrize(
    "method,path,status_code,msg,error",
    [
        ("get", "/missing", 404, "Not Found", "Not Found"),
        ("post", "/health", 405, "Method Not Allowed", "Method Not Allowed"),
        ("get", "/api/v1/users/me", 401, "Unauthorized", "Missing bearer token"),
    ],
)
def test_framework_and_authentication_errors(
    error_client, method, path, status_code, msg, error
):
    response = getattr(error_client, method)(path)

    assert response.status_code == status_code
    assert response.json() == {"status": status_code, "msg": msg, "error": error}
    if status_code == 401:
        assert response.headers["WWW-Authenticate"] == "Bearer"
    if status_code == 405:
        assert "GET" in response.headers["Allow"]


@pytest.mark.parametrize("payload", [{}, {"password": "secret-value"}])
def test_validation_details_are_serializable_without_echoing_password(error_client, payload):
    response = error_client.post("/test/validation", json=payload)

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"status", "msg", "error"}
    assert body["status"] == 422
    assert body["msg"] == "Request validation failed"
    assert body["error"][0]["loc"][0] == "body"
    assert body["error"][0]["msg"]
    assert "secret-value" not in response.text


def test_malformed_json_uses_error_format(error_client):
    response = error_client.post(
        "/test/validation", content="{", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json()["error"][0]["type"] == "json_invalid"


def test_unexpected_exceptions_return_json(error_client):
    response = error_client.get("/test/unhandled")

    assert response.status_code == 500
    assert response.json() == {
        "status": 500,
        "msg": "Internal Server Error",
        "error": "Upstream connection failed",
    }


def test_openapi_documents_error_format(error_client):
    schema = error_client.get("/openapi.json").json()
    responses = schema["paths"]["/api/v1/users/patient"]["post"]["responses"]

    for status_code in ("default", "422"):
        assert responses[status_code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
