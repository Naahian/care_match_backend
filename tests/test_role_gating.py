"""Role-gating regression tests.

Using ``require_roles(...)`` as a parameter *default* instead of as the
*annotation* silently disables the check — FastAPI honours the ``Depends`` in
the annotation and ignores the default. These tests assert the 403 behaviour,
not the wiring, so that mistake cannot pass unnoticed again.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from firebase_admin import auth as firebase_auth

from app.core.security import AuthenticatedUser, get_current_user
from app.main import app


def _user(role: str) -> AuthenticatedUser:
    return AuthenticatedUser(uid="u1", email="u@example.com", claims={"role": role})


def _record(**overrides):
    data = {"localId": "u1", "email": "u@example.com", "customAttributes": '{"role":"patient"}'}
    data.update(overrides)
    import firebase_admin._user_mgt as m

    return m.ExportedUserRecord(data)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def authed():
    def _set(role: str) -> None:
        app.dependency_overrides[get_current_user] = lambda: _user(role)

    yield _set
    app.dependency_overrides.clear()


class _StubRepo:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple] = []

    def list_all(self, limit=50, *, verified=None):
        self.calls.append((limit, verified))
        return list(self.rows)


@pytest.fixture
def stub_repo(monkeypatch) -> _StubRepo:
    repo = _StubRepo()
    from app.services.users import UserService

    monkeypatch.setattr(
        UserService, "__init__", lambda self, doctor_repo=None: setattr(self, "_doctors", repo)
    )
    return repo


@pytest.mark.parametrize("role", ["patient", "doctor", "admin"])
def test_list_doctors_allows_any_authenticated_role(
    client: TestClient, authed, stub_repo: _StubRepo, role: str
) -> None:
    authed(role)
    response = client.get("/api/v1/users/doctors")

    assert response.status_code == 200, response.text


def test_list_doctors_denies_unauthenticated(client: TestClient) -> None:
    assert client.get("/api/v1/users/doctors").status_code == 401


def test_list_doctors_denies_unknown_role(client: TestClient, authed) -> None:
    authed("guest")
    assert client.get("/api/v1/users/doctors").status_code == 403


def test_list_doctors_omits_contact_details(client: TestClient, authed, stub_repo) -> None:
    authed("patient")
    stub_repo.rows = [
        {
            "id": "doc-uid",
            "uid": "doc-uid",
            "email": "doc@example.com",
            "phone": "+15550100",
            "specialties": ["Cardiology"],
            "degrees": ["MBBS"],
            "verified": True,
        }
    ]

    body = client.get("/api/v1/users/doctors").json()

    assert len(body) == 1
    assert body[0]["uid"] == "doc-uid"
    assert body[0]["specialties"] == ["Cardiology"]
    assert body[0]["verified"] is True
    assert "email" not in body[0]
    assert "phone" not in body[0]


def test_list_doctors_passes_verified_filter(client: TestClient, authed, stub_repo) -> None:
    authed("admin")
    client.get("/api/v1/users/doctors", params={"verified": "true"})

    assert stub_repo.calls[-1] == (50, True)


def test_list_doctors_validates_limit(client: TestClient, authed, stub_repo) -> None:
    authed("patient")

    assert client.get("/api/v1/users/doctors", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/users/doctors", params={"limit": 9999}).status_code == 422


def test_create_payments_denies_doctor(client: TestClient, authed) -> None:
    authed("doctor")
    response = client.post(
        "/api/v1/payments",
        json={
            "appointment_id": "a1",
            "amount_cents": 100,
            "idempotency_key": "abcdefgh1234",
        },
    )

    assert response.status_code == 403


def test_update_appointment_status_denies_patient(client: TestClient, authed) -> None:
    authed("patient")
    response = client.patch(
        "/api/v1/appointments/a1/status", params={"new_status": "confirmed"}
    )

    assert response.status_code == 403


def _authed_record(role: str):
    import firebase_admin._user_mgt as m

    return m.ExportedUserRecord(
        {"localId": "u1", "customAttributes": f'{{"role": "{role}"}}'}
    )


def test_registration_endpoints_are_public(client: TestClient) -> None:
    """Both registration paths are open; the role comes from the URL."""
    with patch.object(firebase_auth, "create_user", return_value=_record()), patch.object(
        firebase_auth, "update_user", return_value=_record()
    ):
        assert (
            client.post(
                "/api/v1/users/patient",
                json={"email": "a@x.co", "password": "secret123"},
            ).status_code
            == 201
        )


def test_admin_endpoints_deny_non_admin(client: TestClient, authed) -> None:
    authed("patient")

    # require_admin re-reads the live role from Firebase rather than trusting
    # the token claims, so the lookup must be stubbed too.
    with patch.object(
        firebase_auth, "get_user", return_value=_authed_record("patient")
    ):
        assert client.get("/api/v1/admin/users").status_code == 403
        assert client.get("/api/v1/admin/health").status_code == 403


def test_admin_endpoints_allow_admin(client: TestClient, authed) -> None:
    authed("admin")

    with patch.object(
        firebase_auth, "get_user", return_value=_authed_record("admin")
    ), patch.object(firebase_auth, "list_users") as list_users:
        list_users.return_value.users = [_record()]
        list_users.return_value.next_page_token = ""
        assert client.get("/api/v1/admin/users").status_code == 200
