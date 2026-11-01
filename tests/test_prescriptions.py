import json
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient
from firebase_admin import auth as firebase_auth
from PIL import Image

from app.core.config import Settings
from app.core.security import AuthenticatedUser, get_current_user
from app.main import create_app
from app.services.media import SupabaseMediaStorage

POST = "/api/v1/prescriptions/patients/patient-uid"
GET = "/api/v1/users/me/prescriptions"


def image_bytes(format="PNG"):
    output = BytesIO()
    Image.new("RGB", (30, 20), "white").save(output, format=format)
    return output.getvalue()


@pytest.fixture
def prescription_api(monkeypatch):
    calls, files = [], {}
    state = {"uid": "doctor-uid", "role": "doctor", "live_role": "doctor",
             "patient_role": "patient", "name": "Dr Ahmed", "conflicts": 0, "fail": False}
    settings = Settings(_env_file=None, supabase_url="https://project.supabase.co",
                        supabase_secret_key="sb_secret_test", supabase_storage_public=True)

    def lookup(uid):
        if state.get("missing") == uid:
            raise firebase_auth.UserNotFoundError("missing")
        return SimpleNamespace(custom_claims={"role": state["live_role"] if uid == "doctor-uid" else state["patient_role"]},
                               display_name=state["name"])

    monkeypatch.setattr(firebase_auth, "get_user", lookup)

    def handle(request):
        calls.append(request)
        assert request.headers["apikey"] == "sb_secret_test"
        if state["fail"]:
            return httpx.Response(500, json={"message": "private sb_secret_test"})
        if "/object/list/" in request.url.path:
            body = json.loads(request.content)
            prefix = body["prefix"]
            rows = [{"id": name, "name": name.removeprefix(prefix),
                     "created_at": "2026-11-01T10:00:00Z", "metadata": {"size": len(data)}}
                    for name, data in sorted(files.items()) if name.startswith(prefix)]
            if state.get("list_override") is not None:
                rows = state["list_override"]
            return httpx.Response(200, json=rows[body["offset"]:body["offset"] + body["limit"]])
        if "/object/sign/" in request.url.path:
            return httpx.Response(200, json=[{"path": path,
                "signedURL": f"/object/sign/prescription/{quote(path)}?token=test"}
                for path in json.loads(request.content)["paths"]])
        path = request.url.path.removeprefix("/storage/v1/object/prescription/")
        if state["conflicts"] or path in files:
            state["conflicts"] = max(0, state["conflicts"] - 1)
            return httpx.Response(409, json={"code": "ResourceAlreadyExists"})
        files[path] = request.content
        return httpx.Response(200, json={"Key": path})

    original = httpx.Client
    monkeypatch.setattr("app.services.media.httpx.Client", lambda **kwargs: original(
        transport=httpx.MockTransport(handle), **kwargs))
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        uid=state["uid"], claims={"role": state["role"]})
    app.dependency_overrides[SupabaseMediaStorage] = lambda: SupabaseMediaStorage(settings)
    return TestClient(app), calls, files, state, app


def upload(client, data=None):
    return client.post(POST, files={"file": ("../../untrusted.png", image_bytes() if data is None else data, "image/png")})


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
def test_upload_converts_images_and_uses_live_doctor_name(prescription_api, format):
    client, calls, files, _, _ = prescription_api
    response = upload(client, image_bytes(format))
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["doctor_uid"] == "doctor-uid"
    assert data["doctor_name"] == "Dr Ahmed"
    assert data["index"] == 0
    assert data["filename"] == "Dr Ahmed_0.jpg"
    assert data["path"] == "patient-uid/Dr Ahmed_0.jpg"
    assert data["bucket"] == "prescription"
    assert data["content_type"] == "image/jpeg"
    assert data["size_bytes"] == len(files[data["path"]])
    with Image.open(BytesIO(files[data["path"]])) as image:
        assert image.format == "JPEG"
        assert image.size == (30, 20)
    assert calls[-1].headers["x-upsert"] == "false"
    assert calls[-1].headers["cache-control"] == "no-store"


def test_number_uses_all_patient_files_and_keeps_other_patient_separate(prescription_api):
    client, _, files, *_ = prescription_api
    files.update({"patient-uid/Other Doctor_0.jpg": b"old", "other-patient/Test.jpg": b"private"})
    assert upload(client).json()["data"]["filename"] == "Dr Ahmed_1.jpg"
    assert upload(client).json()["data"]["filename"] == "Dr Ahmed_2.jpg"
    assert files["patient-uid/Other Doctor_0.jpg"] == b"old"


def test_counts_more_than_one_storage_page(prescription_api):
    client, calls, files, *_ = prescription_api
    files.update({f"patient-uid/Old_{i}.jpg": b"x" for i in range(1002)})
    assert upload(client).json()["data"]["index"] == 1002
    lists = [json.loads(r.content) for r in calls if "/object/list/" in r.url.path]
    assert [r["offset"] for r in lists] == [0, 1000]


def test_folder_placeholders_are_not_counted(prescription_api):
    client, _, _, state, _ = prescription_api
    state["list_override"] = [{"id": None, "name": "folder"}, {"id": "file", "name": "old.jpg"}]
    assert upload(client).json()["data"]["index"] == 1


def test_collision_advances_suffix_without_overwriting(prescription_api):
    client, _, files, state, _ = prescription_api
    files["patient-uid/Dr Ahmed_1.jpg"] = b"old"
    assert upload(client).json()["data"]["index"] == 2
    assert files["patient-uid/Dr Ahmed_1.jpg"] == b"old"
    state["conflicts"] = 10
    assert upload(client).status_code == 409


@pytest.mark.parametrize("name", ["Dr/Bad\\Name", "../../Dr?X", "ডাক্তার", None])
def test_unsafe_or_missing_doctor_names_stay_in_patient_folder(prescription_api, name):
    client, _, files, state, _ = prescription_api
    state["name"] = name
    response = upload(client)
    assert response.status_code == 201
    path = response.json()["data"]["path"]
    assert path.count("/") == 1
    assert path.startswith("patient-uid/")
    assert path in files


@pytest.mark.parametrize("role", ["patient", "admin", "guest"])
def test_upload_rejects_other_roles(prescription_api, role):
    client, calls, _, state, _ = prescription_api
    state["role"] = role
    assert upload(client).status_code == 403
    assert not calls


def test_stale_doctor_token_is_rejected(prescription_api):
    client, calls, _, state, _ = prescription_api
    state["live_role"] = "patient"
    assert upload(client).status_code == 403
    assert not calls


def test_target_must_exist_and_be_patient(prescription_api):
    client, calls, _, state, _ = prescription_api
    state["missing"] = "patient-uid"
    assert upload(client).status_code == 404
    state.pop("missing")
    state["patient_role"] = "doctor"
    assert upload(client).status_code == 422
    assert not calls


@pytest.mark.parametrize("data", [b"", b"not an image", b"%PDF-1.4\n%%EOF", image_bytes("GIF")])
def test_invalid_files_are_rejected(prescription_api, data):
    client, calls, *_ = prescription_api
    assert upload(client, data).status_code == 422
    assert not calls


def test_oversize_request_is_bounded(prescription_api, monkeypatch):
    client, calls, *_ = prescription_api
    monkeypatch.setattr("app.api.prescription_uploads.MAX_REPORT_BYTES", 100)
    assert upload(client, b"x" * (64 * 1024 + 101)).status_code == 413
    assert not calls


def test_only_one_file_field_allowed(prescription_api):
    client, calls, *_ = prescription_api
    assert client.post(POST, json={"file": "url"}).status_code == 415
    assert client.post(POST, files={"wrong": ("x.jpg", image_bytes())}).status_code == 422
    assert client.post(POST, files={"file": ("x.jpg", image_bytes())}, data={"doctor_name": "forged"}).status_code == 400
    assert client.post(POST, files=[("file", ("x.jpg", image_bytes())), ("file", ("y.jpg", image_bytes()))]).status_code == 400
    assert not calls


def test_patient_lists_only_own_prescriptions_with_signed_links(prescription_api):
    client, calls, files, state, _ = prescription_api
    assert upload(client).status_code == 201
    files["other-patient/Secret.jpg"] = b"private"
    state.update(uid="patient-uid", role="patient")
    response = client.get(GET, params={"patient_uid": "other-patient"})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()["data"]
    assert data["next_offset"] is None
    assert len(data["prescriptions"]) == 1
    item = data["prescriptions"][0]
    assert item["filename"] == "Dr Ahmed_0.jpg"
    assert item["bucket"] == "prescription"
    assert "/object/sign/prescription/patient-uid/" in item["download_url"]
    assert item["expires_at"]
    assert "Secret" not in response.text
    assert json.loads(calls[-1].content)["paths"] == ["patient-uid/Dr Ahmed_0.jpg"]


def test_empty_list_and_pagination(prescription_api):
    client, _, files, state, _ = prescription_api
    state.update(uid="patient-uid", role="patient")
    assert client.get(GET).json()["data"] == {"prescriptions": [], "next_offset": None}
    files.update({f"patient-uid/Doctor_{i}.jpg": b"x" for i in range(3)})
    page = client.get(GET, params={"limit": 2}).json()["data"]
    assert page["next_offset"] == 2
    assert len(page["prescriptions"]) == 2
    assert len(client.get(GET, params={"offset": 2}).json()["data"]["prescriptions"]) == 1
    assert client.get(GET, params={"limit": 101}).status_code == 422


@pytest.mark.parametrize("role", ["doctor", "admin", "guest"])
def test_get_requires_patient_role(prescription_api, role):
    client, calls, _, state, _ = prescription_api
    state["role"] = role
    assert client.get(GET).status_code == 403
    assert not calls


def test_both_endpoints_require_authentication(prescription_api):
    client, calls, _, _, app = prescription_api
    app.dependency_overrides.pop(get_current_user)
    assert upload(client).status_code == 401
    assert client.get(GET).status_code == 401
    assert not calls


def test_provider_error_is_sanitized_without_upload_retry(prescription_api):
    client, calls, _, state, _ = prescription_api
    state["fail"] = True
    response = upload(client)
    assert response.status_code == 502
    assert "sb_secret_test" not in response.text
    assert len(calls) == 1


def test_openapi_shows_file_picker():
    operation = create_app().openapi()["paths"]["/api/v1/prescriptions/patients/{patient_uid}"]["post"]
    schema = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert schema["required"] == ["file"]
    assert schema["properties"]["file"]["format"] == "binary"
