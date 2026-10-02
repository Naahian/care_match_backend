from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from firebase_admin import auth as firebase_auth

from app.core.security import AuthenticatedUser, get_current_user
from app.main import app


def _record(**overrides):
    data = {
        "localId": "caller-uid",
        "email": "caller@example.com",
        "displayName": "Caller",
        "emailVerified": True,
        "disabled": False,
        "passwordHash": "SECRET_HASH",
        "passwordSalt": "SECRET_SALT",
        "customAttributes": '{"role": "patient"}',
        "providerUserInfo": [
            {"providerId": "password", "rawId": "caller@example.com"}
        ],
    }
    data.update(overrides)
    import firebase_admin._user_mgt as m

    return m.ExportedUserRecord(data)


def _user(role: str = "patient") -> AuthenticatedUser:
    return AuthenticatedUser(
        uid="caller-uid", email="caller@example.com", claims={"role": role}
    )


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def as_patient(client: TestClient):
    app.dependency_overrides[get_current_user] = lambda: _user("patient")
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def as_admin(client: TestClient):
    app.dependency_overrides[get_current_user] = lambda: _user("admin")
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def anonymous(client: TestClient):
    yield client
    app.dependency_overrides.clear()


class _StubRepo:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def create_for_user(self, uid, profile, *, email=None, display_name=None):
        self.calls.append((uid, profile, email))
        return uid

    def get(self, doc_id):
        return None

    def update(self, doc_id, data):
        pass


@pytest.fixture
def stub_repo(monkeypatch) -> _StubRepo:
    repo = _StubRepo()
    from app.services.users import UserService

    monkeypatch.setattr(
        UserService,
        "__init__",
        lambda self, doctor_repo=None: setattr(self, "_doctors", repo),
    )
    return repo


USERS = "/api/v1/users"


# --- patient creation (public) -------------------------------------------


def test_create_patient_is_public(anonymous: TestClient, stub_repo: _StubRepo) -> None:
    with patch.object(
        firebase_auth, "create_user", return_value=_record()
    ) as create, patch.object(firebase_auth, "update_user", return_value=_record()):
        response = anonymous.post(
            f"{USERS}/patient",
            json={"email": "new@example.com", "password": "secret123"},
        )

    assert response.status_code == 201
    assert create.call_args.kwargs["email"] == "new@example.com"
    assert stub_repo.calls == []


def test_create_patient_cannot_request_doctor_role(anonymous: TestClient) -> None:
    with patch.object(firebase_auth, "create_user"), patch.object(
        firebase_auth, "update_user"
    ):
        response = anonymous.post(
            f"{USERS}/patient",
            json={
                "email": "sneaky@example.com",
                "password": "secret123",
                "role": "doctor",
            },
        )

    assert response.status_code == 422


def test_create_patient_cannot_send_doctor_fields(anonymous: TestClient) -> None:
    with patch.object(firebase_auth, "create_user"), patch.object(
        firebase_auth, "update_user"
    ):
        response = anonymous.post(
            f"{USERS}/patient",
            json={
                "email": "sneaky@example.com",
                "password": "secret123",
                "phone": "+15550100",
                "specialties": ["Cardiology"],
            },
        )

    assert response.status_code == 422


def test_create_patient_rejects_short_password(anonymous: TestClient) -> None:
    response = anonymous.post(
        f"{USERS}/patient", json={"email": "a@example.com", "password": "short"}
    )
    assert response.status_code == 422


def test_create_patient_rejects_missing_email(anonymous: TestClient) -> None:
    response = anonymous.post(f"{USERS}/patient", json={"password": "secret123"})
    assert response.status_code == 422


def test_create_patient_duplicate_email_returns_409(anonymous: TestClient) -> None:
    with patch.object(
        firebase_auth,
        "create_user",
        side_effect=firebase_auth.EmailAlreadyExistsError("dupe", None, None),
    ):
        response = anonymous.post(
            f"{USERS}/patient",
            json={"email": "dupe@example.com", "password": "secret123"},
        )

    assert response.status_code == 409


# --- doctor creation (public) --------------------------------------------


def _doctor_body() -> dict:
    return {
        "email": "doc@example.com",
        "password": "secret123",
        "display_name": "Dr Doc",
        "phone": "+15550100",
        "clinics": [{"name": "City Clinic", "location": "Downtown"}],
        "specialties": ["Cardiology"],
        "degrees": ["MBBS"],
        "time_available": [{"start": "09:00", "end": "17:00"}],
        "days_available": ["Monday"],
    }


def test_create_doctor_is_public(anonymous: TestClient, stub_repo: _StubRepo) -> None:
    """No auth required: doctor self-registration is open."""
    with patch.object(
        firebase_auth,
        "create_user",
        return_value=_record(localId="doc-uid", displayName="Dr Doc"),
    ), patch.object(
        firebase_auth,
        "update_user",
        return_value=_record(localId="doc-uid", customAttributes='{"role":"doctor"}'),
    ) as update:
        response = anonymous.post(f"{USERS}/doctor", json=_doctor_body())

    assert response.status_code == 201
    assert update.call_args.kwargs["custom_claims"] == {"role": "doctor"}
    assert len(stub_repo.calls) == 1
    uid, profile, email = stub_repo.calls[0]
    assert uid == "doc-uid"
    assert profile.phone == "+15550100"
    assert profile.specialties == ["Cardiology"]


def test_create_doctor_profile_starts_unverified(
    anonymous: TestClient, stub_repo: _StubRepo
) -> None:
    with patch.object(
        firebase_auth, "create_user", return_value=_record(localId="doc-uid")
    ), patch.object(firebase_auth, "update_user", return_value=_record()):
        response = anonymous.post(f"{USERS}/doctor", json=_doctor_body())

    assert response.status_code == 201
    assert stub_repo.calls[0][1].verified is False


def test_create_doctor_cannot_self_assert_verified(anonymous: TestClient) -> None:
    """A public endpoint must not let the creator claim verification."""
    body = _doctor_body()
    body["verified"] = True
    response = anonymous.post(f"{USERS}/doctor", json=body)

    assert response.status_code == 422


def test_create_doctor_cannot_request_role(anonymous: TestClient) -> None:
    body = _doctor_body()
    body["role"] = "admin"
    response = anonymous.post(f"{USERS}/doctor", json=body)

    assert response.status_code == 422


def test_create_doctor_requires_phone(anonymous: TestClient) -> None:
    body = _doctor_body()
    body.pop("phone")
    response = anonymous.post(f"{USERS}/doctor", json=body)

    assert response.status_code == 422


def test_create_doctor_rejects_bad_weekday(anonymous: TestClient) -> None:
    body = _doctor_body()
    body["days_available"] = ["Funday"]
    response = anonymous.post(f"{USERS}/doctor", json=body)

    assert response.status_code == 422


def test_create_doctor_rejects_reversed_time(anonymous: TestClient) -> None:
    body = _doctor_body()
    body["time_available"] = [{"start": "17:00", "end": "09:00"}]
    response = anonymous.post(f"{USERS}/doctor", json=body)

    assert response.status_code == 422


def test_generic_create_endpoint_is_gone(client: TestClient) -> None:
    """POST /users no longer exists; role is chosen by the path."""
    response = client.post(
        f"{USERS}",
        json={"email": "a@example.com", "password": "secret123", "role": "doctor"},
    )

    assert response.status_code in (404, 405)


# --- self routes ----------------------------------------------------------


def test_get_me_returns_own_account(as_patient: TestClient) -> None:
    with patch.object(firebase_auth, "get_user", return_value=_record()) as get:
        response = as_patient.get(f"{USERS}/me")

    assert response.status_code == 200
    assert response.json()["uid"] == "caller-uid"
    assert get.call_args.args[0] == "caller-uid"


def test_get_me_response_has_no_credentials(as_patient: TestClient) -> None:
    with patch.object(firebase_auth, "get_user", return_value=_record()):
        body = as_patient.get(f"{USERS}/me").json()

    assert "SECRET_HASH" not in str(body)
    assert "password_hash" not in body
    assert "password_salt" not in body


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_self_routes_require_auth(client: TestClient, method: str) -> None:
    response = getattr(client, method)(f"{USERS}/me")
    assert response.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"display_name": "Only Name"},
        {"phone_number": "+15550100"},
        {"photo_url": "https://example.com/a.png"},
        {"display_name": "A", "phone_number": "+15550100"},
        {"display_name": "Real", "phone_number": "", "photo_url": ""},
        {"phone_number": None},
    ],
)
def test_patch_me_accepts_partial_profiles(as_patient: TestClient, body) -> None:
    with patch.object(firebase_auth, "update_user", return_value=_record()) as update:
        response = as_patient.patch(f"{USERS}/me", json=body)

    assert response.status_code == 200, response.text
    assert update.called
    assert update.call_args.args[0] == "caller-uid"


@pytest.mark.parametrize(
    "field,value",
    [
        ("role", "admin"),
        ("email", "new@example.com"),
        ("password", "newsecret1"),
        ("disabled", True),
        ("uid", "someone-else"),
    ],
)
def test_patch_me_rejects_non_profile_fields(
    as_patient: TestClient, field: str, value
) -> None:
    with patch.object(firebase_auth, "update_user") as update:
        response = as_patient.patch(f"{USERS}/me", json={field: value})

    assert response.status_code == 422, f"{field} must not be updatable"
    update.assert_not_called()


def test_patch_me_rejects_empty_body(as_patient: TestClient) -> None:
    with patch.object(firebase_auth, "update_user") as update:
        response = as_patient.patch(f"{USERS}/me", json={})

    assert response.status_code == 400
    update.assert_not_called()


def test_patch_me_never_echoes_password(as_patient: TestClient) -> None:
    with patch.object(
        firebase_auth, "update_user", return_value=_record(phoneNumber="+15550100")
    ):
        response = as_patient.patch(f"{USERS}/me", json={"phone_number": "+15550100"})

    assert response.status_code == 200
    assert "password" not in response.json()


def test_delete_me_deletes_own_account(as_patient: TestClient) -> None:
    with patch.object(firebase_auth, "delete_user") as delete:
        response = as_patient.delete(f"{USERS}/me")

    assert response.status_code == 204
    assert delete.call_args.args[0] == "caller-uid"


def test_delete_me_returns_404_when_already_gone(as_patient: TestClient) -> None:
    with patch.object(
        firebase_auth,
        "delete_user",
        side_effect=firebase_auth.UserNotFoundError("x", None),
    ):
        response = as_patient.delete(f"{USERS}/me")

    assert response.status_code == 404
