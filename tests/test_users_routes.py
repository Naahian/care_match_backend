from unittest.mock import AsyncMock, patch
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from firebase_admin import auth as firebase_auth
from google.api_core.exceptions import ServiceUnavailable

from app.core.security import AuthenticatedUser, get_current_user
from app.main import app
from app.repositories.user_profile import UserProfileRepository
from tests.user_profiles_fake import FakeUserProfiles


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
        lambda self, doctor_repo=None, profile_repo=None, media=None: setattr(self, "_doctors", repo),
    )
    return repo


USERS = "/api/v1/users"


@pytest.fixture(autouse=True)
def personal_profiles():
    repository = FakeUserProfiles()
    app.dependency_overrides[UserProfileRepository] = lambda: repository
    yield repository
    app.dependency_overrides.pop(UserProfileRepository, None)


@pytest.fixture(autouse=True)
def signup_emails(monkeypatch):
    from app.services.account_emails import AccountEmailService

    send = AsyncMock()
    monkeypatch.setattr(AccountEmailService, "send_signup_verification", send)
    return send


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
    assert response.json()["data"]["uid"] == "caller-uid"
    assert get.call_args.args[0] == "caller-uid"


def test_get_me_response_has_no_credentials(as_patient: TestClient) -> None:
    with patch.object(firebase_auth, "get_user", return_value=_record()):
        body = as_patient.get(f"{USERS}/me").json()["data"]

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
        {"remove_photo": True},
        {"display_name": "A", "phone_number": "+15550100"},
        {"display_name": "Real", "phone_number": "", "remove_photo": True},
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
        ("photo_url", "https://example.com/arbitrary.png"),
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
    assert "password" not in response.json()["data"]


def test_patch_personal_details_round_trip_without_updating_auth(
    as_patient: TestClient, personal_profiles,
) -> None:
    with patch.object(firebase_auth, "get_user", return_value=_record()) as get, patch.object(
        firebase_auth, "update_user"
    ) as update:
        response = as_patient.patch(f"{USERS}/me", json={
            "date_of_birth": "1994-02-28", "gender": " female ", "blood_group": "ab+",
        })
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["date_of_birth"] == "1994-02-28"
        assert data["gender"] == "Female"
        assert data["blood_group"] == "AB+"
        assert personal_profiles.rows["caller-uid"] == {
            "date_of_birth": "1994-02-28", "gender": "Female", "blood_group": "AB+",
        }
        assert as_patient.get(f"{USERS}/me").json()["data"] == data
    update.assert_not_called()
    assert all(call.args == ("caller-uid",) for call in get.call_args_list)


def test_patch_mixed_profile_splits_auth_and_personal_fields(
    as_patient: TestClient, personal_profiles,
) -> None:
    personal_profiles.rows["caller-uid"] = {"blood_group": "O-"}
    with patch.object(firebase_auth, "update_user", return_value=_record(displayName="Renamed")) as update:
        response = as_patient.patch(f"{USERS}/me", json={
            "display_name": "Renamed", "date_of_birth": "2000-02-29", "gender": "Male",
        })
    assert response.status_code == 200, response.text
    update.assert_called_once_with("caller-uid", display_name="Renamed")
    assert personal_profiles.writes == [
        ("caller-uid", {"date_of_birth": "2000-02-29", "gender": "Male"})
    ]
    data = response.json()["data"]
    assert data["display_name"] == "Renamed"
    assert data["blood_group"] == "O-"


@pytest.mark.parametrize("clear", [None, "", "   "])
def test_patch_clears_personal_fields_and_preserves_omitted_fields(
    as_patient: TestClient, personal_profiles, clear,
) -> None:
    personal_profiles.rows["caller-uid"] = {
        "date_of_birth": "1990-01-01", "gender": "Other", "blood_group": "A+",
    }
    personal_profiles.rows["other-uid"] = {"gender": "Male"}
    with patch.object(firebase_auth, "get_user", return_value=_record()):
        response = as_patient.patch(f"{USERS}/me", json={"date_of_birth": clear, "gender": clear})
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["date_of_birth"] is None
        assert data["gender"] is None
        assert data["blood_group"] == "A+"
        assert as_patient.get(f"{USERS}/me").json()["data"]["gender"] is None
    assert personal_profiles.rows["other-uid"] == {"gender": "Male"}


@pytest.mark.parametrize("body", [
    {"date_of_birth": "2001-02-29"},
    {"date_of_birth": "2000-13-01"},
    {"date_of_birth": "2000-01-01T00:00:00Z"},
    {"date_of_birth": 0},
    {"date_of_birth": (date.today() + timedelta(days=1)).isoformat()},
    {"gender": "invalid"}, {"gender": 1},
    {"blood_group": "C+"}, {"blood_group": "AB"},
])
def test_patch_rejects_invalid_personal_details_without_writes(
    as_patient: TestClient, personal_profiles, body,
) -> None:
    with patch.object(firebase_auth, "get_user") as get, patch.object(firebase_auth, "update_user") as update:
        response = as_patient.patch(f"{USERS}/me", json=body)
    assert response.status_code == 422, response.text
    assert personal_profiles.writes == []
    get.assert_not_called()
    update.assert_not_called()


def test_get_profile_merges_only_allowed_personal_fields(
    as_patient: TestClient, personal_profiles,
) -> None:
    personal_profiles.rows["caller-uid"] = {
        "date_of_birth": "1990-01-01", "gender": "Male", "blood_group": "O+",
        "uid": "other-uid", "role": "admin", "email": "other@example.com",
        "password_hash": "PRIVATE", "updated_at": "internal metadata",
    }
    with patch.object(firebase_auth, "get_user", return_value=_record()):
        data = as_patient.get(f"{USERS}/me").json()["data"]
    assert data["date_of_birth"] == "1990-01-01"
    assert data["uid"] == "caller-uid"
    assert data["role"] == "patient"
    assert data["email"] == "caller@example.com"
    assert "PRIVATE" not in str(data)
    assert "updated_at" not in data


def test_personal_profile_write_failure_reports_partial_update(
    as_patient: TestClient, personal_profiles,
) -> None:
    with patch.object(firebase_auth, "update_user", return_value=_record()) as update, patch.object(
        personal_profiles, "save", side_effect=ServiceUnavailable("offline")
    ):
        response = as_patient.patch(f"{USERS}/me", json={"display_name": "Renamed", "gender": "Other"})
    update.assert_called_once_with("caller-uid", display_name="Renamed")
    assert response.status_code == 503
    assert "Some changes may have been saved" in response.text


def test_personal_profile_read_failure_prevents_auth_update(
    as_patient: TestClient, personal_profiles,
) -> None:
    with patch.object(firebase_auth, "update_user") as update, patch.object(
        personal_profiles, "get", side_effect=ServiceUnavailable("offline")
    ):
        response = as_patient.patch(f"{USERS}/me", json={"display_name": "Renamed", "gender": "Other"})
    assert response.status_code == 503
    update.assert_not_called()
    assert personal_profiles.writes == []


def test_personal_details_do_not_create_profile_for_missing_account(
    as_patient: TestClient, personal_profiles,
) -> None:
    with patch.object(firebase_auth, "get_user", side_effect=firebase_auth.UserNotFoundError("missing", None)):
        response = as_patient.patch(f"{USERS}/me", json={"gender": "Female"})
    assert response.status_code == 404
    assert personal_profiles.writes == []


def test_delete_me_deletes_own_account(as_patient: TestClient, personal_profiles) -> None:
    personal_profiles.rows["caller-uid"] = {"blood_group": "A+"}
    with patch.object(firebase_auth, "delete_user") as delete:
        response = as_patient.delete(f"{USERS}/me")

    assert response.status_code == 204
    assert delete.call_args.args[0] == "caller-uid"
    assert personal_profiles.deleted == ["caller-uid"]
    assert personal_profiles.rows == {}


def test_delete_me_returns_404_when_already_gone(as_patient: TestClient) -> None:
    with patch.object(
        firebase_auth,
        "delete_user",
        side_effect=firebase_auth.UserNotFoundError("x", None),
    ):
        response = as_patient.delete(f"{USERS}/me")

    assert response.status_code == 404
