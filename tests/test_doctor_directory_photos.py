from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from firebase_admin import auth as firebase_auth
from firebase_admin import exceptions as firebase_exceptions
from google.api_core.exceptions import ServiceUnavailable

from app.core.config import Settings
from app.main import create_app
from app.repositories.user_profile import UserProfileRepository
from app.services.media import SupabaseMediaStorage
from app.services.users import UserService
from tests.user_profiles_fake import FakeUserProfiles


@pytest.fixture
def directory(monkeypatch):
    profiles = FakeUserProfiles()
    doctors = Mock()
    doctors.list_all.return_value = [{"uid": "doctor-uid", "display_name": "Dr Ahmed",
                                    "email": "private@example.com", "phone": "private",
                                    "total_earnings": 500, "date_of_birth": "1990-01-01"}]
    auth = Mock(return_value=SimpleNamespace(users=[]))
    monkeypatch.setattr(firebase_auth, "get_users", auth)
    calls = []
    settings = Settings(_env_file=None, supabase_url="https://project.supabase.co",
                        supabase_secret_key="sb_secret_test")

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"signedURL": request.url.path.removeprefix("/storage/v1") + "?token=temporary"})

    original = httpx.Client
    monkeypatch.setattr("app.services.media.httpx.Client", lambda **kwargs: original(
        transport=httpx.MockTransport(handle), **kwargs))
    service = UserService(doctors, profiles, SupabaseMediaStorage(settings))
    app = create_app()
    app.dependency_overrides[UserService] = lambda: service
    return TestClient(app), profiles, doctors, auth, calls, settings


@pytest.mark.parametrize("public", [False, True])
def test_directory_returns_current_profile_photo_with_version(directory, public):
    client, profiles, _, auth, calls, settings = directory
    settings.supabase_storage_public = public
    profiles.rows["doctor-uid"] = {"photo_path": "doctor-uid.jpg", "photo_version": "v123",
                                    "gender": "Male", "blood_group": "B+"}
    response = client.get("/api/v1/users/doctors")
    assert response.status_code == 200, response.text
    doctor = response.json()["data"][0]
    expected = ("https://project.supabase.co/storage/v1/object/public/profile_images/doctor-uid.jpg?v=v123"
                if public else "https://project.supabase.co/storage/v1/object/sign/profile_images/doctor-uid.jpg?token=temporary&v=v123")
    assert doctor["photo_url"] == expected
    assert doctor["display_name"] == "Dr Ahmed"
    for field in ("photo_path", "photo_version", "email", "phone", "gender", "blood_group", "date_of_birth", "total_earnings"):
        assert field not in doctor
    auth.assert_not_called()
    assert len(calls) == (0 if public else 1)


def test_legacy_auth_photo_is_returned(directory):
    client, _, _, auth, calls, _ = directory
    auth.return_value.users = [SimpleNamespace(uid="doctor-uid", photo_url="https://example.com/legacy.jpg")]
    assert client.get("/api/v1/users/doctors").json()["data"][0]["photo_url"] == "https://example.com/legacy.jpg"
    assert len(auth.call_args.args[0]) == 1
    assert not calls


def test_missing_photo_or_missing_auth_account_returns_null(directory):
    client, _, doctors, auth, calls, _ = directory
    doctors.list_all.return_value.append({"id": "legacy-doctor"})
    auth.return_value.users = [SimpleNamespace(uid="doctor-uid", photo_url=None)]
    response = client.get("/api/v1/users/doctors")
    assert response.status_code == 200
    assert [r["photo_url"] for r in response.json()["data"]] == [None, None]
    assert not calls


def test_removed_photo_does_not_restore_legacy_auth_photo(directory):
    client, profiles, _, auth, calls, _ = directory
    profiles.rows["doctor-uid"] = {"photo_path": None}
    assert client.get("/api/v1/users/doctors").json()["data"][0]["photo_url"] is None
    auth.assert_not_called()
    assert not calls


def test_legacy_auth_reads_are_batched_and_profiles_loaded_once(directory):
    client, profiles, doctors, auth, _, _ = directory
    doctors.list_all.return_value = [{"uid": f"doctor-{i}"} for i in range(200)]
    profiles.get_many = Mock(wraps=profiles.get_many)
    response = client.get("/api/v1/users/doctors", params={"limit": 200, "verified": True})
    assert response.status_code == 200
    assert len(response.json()["data"]) == 200
    assert [len(call.args[0]) for call in auth.call_args_list] == [100, 100]
    profiles.get_many.assert_called_once()
    doctors.list_all.assert_called_once_with(limit=200, verified=True)
    doctors.get_total_earnings.assert_not_called()


def test_profile_lookup_failure_returns_sanitized_error(directory):
    client, profiles, _, auth, *_ = directory
    profiles.get_many = Mock(side_effect=ServiceUnavailable("private details"))
    response = client.get("/api/v1/users/doctors")
    assert response.status_code == 503
    assert "private details" not in response.text
    auth.assert_not_called()


def test_legacy_auth_lookup_failure_returns_sanitized_error(directory):
    client, _, _, auth, *_ = directory
    auth.side_effect = firebase_exceptions.UnavailableError("private details")
    response = client.get("/api/v1/users/doctors")
    assert response.status_code == 503
    assert "private details" not in response.text


def test_batch_profile_repository_keys_snapshots_by_uid_and_ignores_missing():
    db = Mock()
    db.collection.return_value.document.side_effect = lambda uid: uid
    db.get_all.return_value = [Mock(id="b", exists=True, to_dict=lambda: {"photo_path": "b.jpg"}),
                             Mock(id="a", exists=False)]
    repository = UserProfileRepository(db)
    assert repository.get_many(["a", "b"]) == {"b": {"photo_path": "b.jpg"}}
    assert db.get_all.call_args.args[0] == ["a", "b"]
    db.get_all.reset_mock()
    assert repository.get_many([]) == {}
    db.get_all.assert_not_called()
