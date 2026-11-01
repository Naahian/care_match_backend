import json
from io import BytesIO
from unittest.mock import Mock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from firebase_admin import auth as firebase_auth
from google.api_core.exceptions import ServiceUnavailable
from PIL import Image

from app.core.config import Settings
from app.core.security import AuthenticatedUser, get_current_user
from app.main import create_app
from app.services.media import MAX_PHOTO_BYTES, SupabaseMediaStorage
from app.services.users import UserService
from app.services.booking import BookingService
from tests.test_users_routes import _record
from tests.user_profiles_fake import FakeUserProfiles


def image_bytes(size=(30, 20)):
    output = BytesIO()
    image = Image.new("RGB", size, "blue")
    exif = image.getexif()
    exif[270] = "private metadata"
    image.save(output, format="JPEG", exif=exif)
    return output.getvalue()


def test_docs_default_to_multipart_with_visible_photo_file_field():
    operation = create_app().openapi()["paths"]["/api/v1/users/me"]["patch"]
    content = operation["requestBody"]["content"]
    assert next(iter(content)) == "multipart/form-data"
    properties = content["multipart/form-data"]["schema"]["properties"]
    assert next(iter(properties)) == "photo"
    assert properties["photo"]["type"] == "string"
    assert properties["photo"]["format"] == "binary"
    assert "application/json" in content


@pytest.fixture
def media_api(monkeypatch):
    settings = Settings(_env_file=None, supabase_url="https://project.supabase.co",
                        supabase_secret_key="sb_secret_test")
    profiles = FakeUserProfiles()
    calls = []
    state = {"fail_upload": False, "fail_delete": False}

    def handle(request):
        calls.append(request)
        assert request.headers["apikey"] == settings.supabase_secret_key
        if settings.supabase_secret_key.startswith("sb_secret_"):
            assert "authorization" not in request.headers
        else:
            assert request.headers["authorization"] == f"Bearer {settings.supabase_secret_key}"
        if request.url.path.startswith("/storage/v1/object/sign/"):
            return httpx.Response(200, json={"signedURL": request.url.path.removeprefix("/storage/v1") + "?token=temporary"})
        if request.method == "POST" and state["fail_upload"]:
            return httpx.Response(500, text="private provider failure sb_secret_test")
        if request.method == "DELETE" and state["fail_delete"]:
            return httpx.Response(500, text="cleanup failure")
        return httpx.Response(200, json={"Key": request.url.path})

    client_class = httpx.Client
    monkeypatch.setattr("app.services.media.httpx.Client",
                        lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs))
    monkeypatch.setattr(firebase_auth, "get_user", Mock(return_value=_record()))
    monkeypatch.setattr(firebase_auth, "update_user", Mock(return_value=_record()))
    service = UserService(Mock(), profiles, SupabaseMediaStorage(settings))
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(uid="caller-uid", claims={"role": "patient"})
    app.dependency_overrides[UserService] = lambda: service
    return TestClient(app), profiles, calls, settings, state, app


def uploads(calls):
    return [r for r in calls if r.method == "POST" and "/sign/" not in r.url.path]


def test_upload_normalizes_image_persists_owned_path_and_returns_fresh_links(media_api):
    client, profiles, calls, _, _, _ = media_api
    response = client.patch("/api/v1/users/me", data={"gender": "Female", "blood_group": "o+"},
                            files={"photo": ("../../another-user.png", image_bytes((2000, 1000)), "image/png")})
    assert response.status_code == 200, response.text
    path = profiles.rows["caller-uid"]["photo_path"]
    assert path == "caller-uid.jpg"
    upload = uploads(calls)[0]
    assert upload.headers["content-type"] == "image/jpeg"
    assert upload.headers["x-upsert"] == "true"
    with Image.open(BytesIO(upload.content)) as normalized:
        assert normalized.size == (1024, 512)
        assert normalized.format == "JPEG"
        assert not normalized.getexif()
    body = response.json()["data"]
    assert body["photo_url"].startswith("https://project.supabase.co/storage/v1/object/sign/profile_images/")
    assert "sb_secret" not in response.text
    assert "photo_path" not in body
    assert body["gender"] == "Female"
    assert body["blood_group"] == "O+"
    assert client.get("/api/v1/users/me").json()["data"]["photo_url"] == body["photo_url"]
    assert len(uploads(calls)) == 1
    firebase_auth.update_user.assert_not_called()


def test_public_storage_returns_public_url_and_replaces_old_photo(media_api):
    client, profiles, calls, settings, _, _ = media_api
    settings.supabase_storage_public = True
    profiles.rows["caller-uid"] = {"photo_path": "profiles/old.jpg", "gender": "Other"}
    response = client.patch("/api/v1/users/me", files={"photo": ("photo.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 200, response.text
    assert "/object/public/profile_images/" in response.json()["data"]["photo_url"]
    assert response.json()["data"]["gender"] == "Other"
    assert not any("/sign/" in r.url.path for r in calls)
    assert calls[-1].method == "DELETE"
    assert json.loads(calls[-1].content) == {"prefixes": ["profiles/old.jpg"]}
    assert profiles.rows["caller-uid"]["photo_path"] != "profiles/old.jpg"


def test_text_edit_preserves_photo_and_removal_clears_it_after_save(media_api):
    client, profiles, calls, _, _, _ = media_api
    profiles.rows["caller-uid"] = {"photo_path": "profiles/old.jpg"}
    response = client.patch("/api/v1/users/me", json={"gender": "Male"})
    assert response.status_code == 200, response.text
    assert "/profiles/old.jpg" in response.json()["data"]["photo_url"]
    assert not uploads(calls)
    response = client.patch("/api/v1/users/me", json={"remove_photo": True})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["photo_url"] is None
    assert profiles.rows["caller-uid"] == {"gender": "Male", "photo_path": None, "photo_version": None}
    firebase_auth.update_user.assert_called_once_with("caller-uid", photo_url=firebase_auth.DELETE_ATTRIBUTE)
    assert json.loads(calls[-1].content) == {"prefixes": ["profiles/old.jpg"]}


@pytest.mark.parametrize("body,status", [
    (b"", 422), (b"not an image", 422), (b"<svg></svg>", 422),
    (b"x" * (MAX_PHOTO_BYTES + 1), 413), (b"\x89PNG\r\n\x1a\ncorrupt", 422),
])
def test_invalid_images_never_reach_supabase_or_change_profile(media_api, body, status):
    client, profiles, calls, _, _, _ = media_api
    response = client.patch("/api/v1/users/me", data={"display_name": "New name"},
                            files={"photo": ("photo.jpg", body, "image/jpeg")})
    assert response.status_code == status, response.text
    assert not calls
    assert not profiles.writes
    firebase_auth.update_user.assert_not_called()


@pytest.mark.parametrize("fields", [
    {"remove_photo": "true"}, {"photo_url": "https://other.example/photo.jpg"},
    {"uid": "someone-else"},
])
def test_conflicting_or_forbidden_form_fields_rejected_before_upload(media_api, fields):
    client, profiles, calls, _, _, _ = media_api
    response = client.patch("/api/v1/users/me", data=fields,
                            files={"photo": ("photo.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 422, response.text
    assert not profiles.writes
    assert not calls


def test_storage_failure_does_not_apply_other_profile_changes_or_expose_secrets(media_api):
    client, profiles, calls, _, state, _ = media_api
    state["fail_upload"] = True
    response = client.patch("/api/v1/users/me", data={"display_name": "New name"},
                            files={"photo": ("photo.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 502
    assert "sb_secret" not in response.text
    assert not profiles.writes
    firebase_auth.update_user.assert_not_called()
    assert not any(r.method == "DELETE" for r in calls)  # Preserve a stable UID object on uncertain upload failure.


def test_auth_failure_cleans_new_upload_and_preserves_old_reference(media_api):
    client, profiles, calls, _, _, _ = media_api
    profiles.rows["caller-uid"] = {"photo_path": "profiles/old.jpg"}
    firebase_auth.update_user.side_effect = ValueError("Invalid phone")
    response = client.patch("/api/v1/users/me", data={"phone_number": "invalid"},
                            files={"photo": ("photo.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 400
    assert profiles.rows["caller-uid"]["photo_path"] == "profiles/old.jpg"
    deleted = json.loads(calls[-1].content)["prefixes"]
    assert deleted != ["profiles/old.jpg"]


def test_uncertain_profile_write_keeps_photo_and_reports_partial_failure(media_api):
    client, profiles, calls, _, _, _ = media_api
    profiles.rows["caller-uid"] = {"photo_path": "profiles/old.jpg"}
    with patch.object(profiles, "save", side_effect=ServiceUnavailable("timeout")):
        response = client.patch("/api/v1/users/me", files={"photo": ("photo.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 503
    assert "Some changes may have been saved" in response.text
    assert not any(r.method == "DELETE" for r in calls)


def test_photo_upload_requires_authentication(media_api):
    client, profiles, calls, _, _, app = media_api
    app.dependency_overrides.pop(get_current_user)
    response = client.patch("/api/v1/users/me", files={"photo": ("photo.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 401
    assert not calls
    assert not profiles.writes


def test_missing_configuration_only_blocks_photo_operations(media_api):
    client, profiles, calls, settings, _, _ = media_api
    settings.supabase_secret_key = None
    assert client.patch("/api/v1/users/me", json={"gender": "Female"}).status_code == 200
    response = client.patch("/api/v1/users/me", files={"photo": ("photo.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 503
    assert not calls
    assert "photo_path" not in profiles.rows["caller-uid"]


def test_legacy_service_role_key_is_supported(media_api):
    client, _, _, settings, _, _ = media_api
    settings.supabase_secret_key = "legacy-service-role-jwt"
    response = client.patch("/api/v1/users/me", files={"photo": ("photo.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 200, response.text


def test_publishable_key_is_rejected_without_uploading(media_api):
    client, profiles, calls, settings, _, _ = media_api
    settings.supabase_secret_key = "sb_publishable_not_a_server_key"
    response = client.patch("/api/v1/users/me", files={"photo": ("photo.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 503
    assert "secret or service-role key" in response.text
    assert not profiles.writes
    assert not calls


def test_old_object_cleanup_failure_does_not_undo_saved_photo(media_api, caplog):
    client, profiles, _, settings, state, _ = media_api
    settings.supabase_storage_public = True
    profiles.rows["caller-uid"] = {"photo_path": "profiles/old.jpg"}
    state["fail_delete"] = True
    response = client.patch("/api/v1/users/me", files={"photo": ("photo.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 200
    assert profiles.rows["caller-uid"]["photo_path"] != "profiles/old.jpg"
    assert "cleanup failed" in caplog.text


def test_appointment_names_do_not_depend_on_photo_storage(media_api):
    _, profiles, calls, settings, _, app = media_api
    profiles.rows["caller-uid"] = {"photo_path": "profiles/existing.jpg"}
    settings.supabase_secret_key = None
    users = app.dependency_overrides[UserService]()
    result = BookingService(Mock(), users)._response_record({"patient_id": "caller-uid", "id": "a"})
    assert result["patient_name"] == "Caller"
    assert not calls


def test_repeated_uploads_keep_uid_filename_and_refresh_display_version(media_api):
    client, profiles, calls, settings, _, _ = media_api
    settings.supabase_storage_public = True
    first = client.patch("/api/v1/users/me", files={"photo": ("one.png", image_bytes(), "image/png")})
    second = client.patch("/api/v1/users/me", files={"photo": ("two.png", image_bytes(), "image/png")})
    assert first.status_code == second.status_code == 200
    assert profiles.rows["caller-uid"]["photo_path"] == "caller-uid.jpg"
    assert first.json()["data"]["photo_url"] != second.json()["data"]["photo_url"]
    assert all(r.url.path == "/storage/v1/object/profile_images/caller-uid.jpg" for r in uploads(calls))
    assert not any(r.method == "DELETE" for r in calls)


def test_auth_failure_does_not_delete_existing_uid_object(media_api):
    client, profiles, calls, _, _, _ = media_api
    profiles.rows["caller-uid"] = {"photo_path": "caller-uid.jpg"}
    firebase_auth.update_user.side_effect = ValueError("Invalid phone")
    response = client.patch("/api/v1/users/me", data={"phone_number": "invalid"},
                            files={"photo": ("photo.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 503
    assert "Photo was uploaded" in response.text
    assert not any(r.method == "DELETE" for r in calls)
