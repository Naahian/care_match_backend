from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from firebase_admin import auth as firebase_auth
from firebase_admin import exceptions as firebase_exceptions
from PIL import Image

from app.core.config import Settings
from app.core.security import AuthenticatedUser, get_current_user
from app.main import create_app
from app.services.media import SupabaseMediaStorage
from app.services.medical_reports import MAX_REPORT_BYTES

URL = "/api/v1/admin/patients/patient-uid/medical-reports"
PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


def image_bytes(format):
    output = BytesIO()
    Image.new("RGB", (30, 20), "blue").save(output, format=format)
    return output.getvalue()


@pytest.fixture
def reports_api(monkeypatch):
    settings = Settings(_env_file=None, supabase_url="https://project.supabase.co",
                        supabase_secret_key="sb_secret_test", supabase_storage_public=True)
    calls = []
    state = {"admin_role": "admin", "patient_role": "patient", "status": 200,
             "body": {"Key": "stored"}, "timeout": False}

    def get_user(uid):
        if uid == "admin-uid":
            return SimpleNamespace(custom_claims={"role": state["admin_role"]})
        if state.get("auth_error"):
            raise state["auth_error"]
        return SimpleNamespace(custom_claims={"role": state["patient_role"]})

    lookup = Mock(side_effect=get_user)
    monkeypatch.setattr(firebase_auth, "get_user", lookup)

    def handle(request):
        calls.append(request)
        if state["timeout"]:
            raise httpx.ReadTimeout("private provider details", request=request)
        return httpx.Response(state["status"], json=state["body"])

    client_class = httpx.Client
    monkeypatch.setattr("app.services.media.httpx.Client",
                        lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs))
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        uid="admin-uid", claims={"role": "admin"},
    )
    app.dependency_overrides[SupabaseMediaStorage] = lambda: SupabaseMediaStorage(settings)
    return TestClient(app), calls, state, settings, app, lookup


def upload(client, filename="Blood Test.pdf", data=PDF, url=URL):
    return client.post(url, data={"filename": filename},
                       files={"file": ("device-original.bin", data, "application/octet-stream")})


def test_admin_upload_uses_patient_folder_and_admin_filename(reports_api):
    client, calls, _, _, _, lookup = reports_api
    response = upload(client)
    assert response.status_code == 201, response.text
    assert response.json() == {
        "status": 201, "msg": "Created", "data": {
            "patient_uid": "patient-uid", "filename": "Blood Test.pdf",
            "bucket": "medical_reports", "path": "patient-uid/Blood Test.pdf",
            "content_type": "application/pdf", "size_bytes": len(PDF),
        },
    }
    assert [c.args[0] for c in lookup.call_args_list] == ["admin-uid", "patient-uid"]
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "POST"
    assert request.url.path == "/storage/v1/object/medical_reports/patient-uid/Blood Test.pdf"
    assert b"Blood%20Test.pdf" in request.url.raw_path
    assert request.headers["content-type"] == "application/pdf"
    assert request.headers["x-upsert"] == "false"
    assert request.headers["cache-control"] == "no-store"
    assert request.headers["apikey"] == "sb_secret_test"
    assert "authorization" not in request.headers
    assert request.content == PDF
    assert "public" not in str(request.url)


@pytest.mark.parametrize("extension,format", [
    ("jpg", "JPEG"), ("jpeg", "JPEG"), ("png", "PNG"),
    ("webp", "WEBP"), ("PNG", "PNG"), ("jpg", "PNG"),
])
def test_supported_images_are_stored_as_jpeg(reports_api, extension, format):
    client, calls, *_ = reports_api
    data = image_bytes(format)
    response = upload(client, f"Scan (1).{extension}", data)
    assert response.status_code == 201, response.text
    stored = response.json()["data"]
    assert stored["content_type"] == "image/jpeg"
    assert stored["filename"] == "Scan (1).jpg"
    assert stored["path"] == "patient-uid/Scan (1).jpg"
    assert stored["size_bytes"] == len(calls[0].content)
    assert calls[0].url.path.endswith("/patient-uid/Scan (1).jpg")
    assert calls[0].headers["content-type"] == "image/jpeg"
    with Image.open(BytesIO(calls[0].content)) as jpeg:
        assert jpeg.format == "JPEG"
        assert jpeg.mode == "RGB"
        assert jpeg.size == (30, 20)
        assert jpeg.getpixel((15, 10))[2] > 240


def test_image_conversion_applies_exif_orientation_and_removes_metadata(reports_api):
    client, calls, *_ = reports_api
    source = Image.new("RGB", (30, 20), "blue")
    exif = source.getexif()
    exif[274] = 6  # Rotate 90 degrees clockwise for display.
    exif[270] = "private metadata"
    output = BytesIO()
    source.save(output, format="JPEG", exif=exif)
    response = upload(client, "Scan.jpeg", output.getvalue())
    assert response.status_code == 201, response.text
    with Image.open(BytesIO(calls[0].content)) as jpeg:
        assert jpeg.size == (20, 30)
        assert not jpeg.getexif()


@pytest.mark.parametrize("mode", ["RGBA", "P"])
def test_transparent_images_have_white_backgrounds(reports_api, mode):
    client, calls, *_ = reports_api
    if mode == "RGBA":
        source = Image.new(mode, (30, 20), (0, 0, 0, 0))
        source.paste((0, 0, 0, 255), (10, 5, 20, 15))
    else:
        source = Image.new(mode, (30, 20), 0)
        source.putpalette([0, 0, 0] * 256)
        source.info["transparency"] = 0
        source.paste(1, (10, 5, 20, 15))
    output = BytesIO()
    source.save(output, format="PNG")
    response = upload(client, "Scan.png", output.getvalue())
    assert response.status_code == 201, response.text
    with Image.open(BytesIO(calls[0].content)) as jpeg:
        assert all(channel > 250 for channel in jpeg.getpixel((0, 0)))
        assert all(channel < 10 for channel in jpeg.getpixel((15, 10)))


def test_converted_size_limit_prevents_storage_upload(reports_api, monkeypatch):
    client, calls, *_ = reports_api
    data = image_bytes("PNG")
    monkeypatch.setattr("app.services.medical_reports.MAX_REPORT_BYTES", len(data) + 1)
    response = upload(client, "Scan.png", data)
    assert response.status_code == 413
    assert "Converted report image" in response.text
    assert not calls


def test_different_image_extensions_conflict_on_same_final_jpg_name(reports_api):
    client, calls, state, *_ = reports_api
    assert upload(client, "Scan.png", image_bytes("PNG")).status_code == 201
    state.update(status=409, body={"code": "ResourceAlreadyExists"})
    assert upload(client, "Scan.webp", image_bytes("WEBP")).status_code == 409
    assert all(request.url.path.endswith("/patient-uid/Scan.jpg") for request in calls)
    assert all(request.headers["x-upsert"] == "false" for request in calls)


def test_filename_trims_outer_spaces_and_accepts_uppercase_extension(reports_api):
    client, calls, *_ = reports_api
    response = upload(client, "  CBC_2026-11-01.PDF  ")
    assert response.status_code == 201
    assert response.json()["data"]["filename"] == "CBC_2026-11-01.PDF"
    assert calls[0].url.path.endswith("/CBC_2026-11-01.PDF")


@pytest.mark.parametrize("data,extension,content_type", [
    (image_bytes("PNG"), ".jpg", "image/jpeg"),
    (image_bytes("JPEG"), ".jpg", "image/jpeg"),
    (image_bytes("WEBP"), ".jpg", "image/jpeg"),
    (PDF, ".pdf", "application/pdf"),
])
def test_report_name_without_extension_uses_file_content(reports_api, data, extension, content_type):
    client, calls, *_ = reports_api
    response = upload(client, "Blood Test", data)
    assert response.status_code == 201, response.text
    stored = response.json()["data"]
    assert stored["filename"] == "Blood Test" + extension
    assert stored["path"] == "patient-uid/Blood Test" + extension
    assert stored["content_type"] == content_type
    assert calls[0].headers["content-type"] == content_type
    if extension == ".pdf":
        assert calls[0].content == data
    else:
        with Image.open(BytesIO(calls[0].content)) as image:
            assert image.format == "JPEG"


def test_invalid_file_with_extensionless_name_is_rejected(reports_api):
    client, calls, *_ = reports_api
    assert upload(client, "Blood Test", b"not a report").status_code == 422
    assert not calls


def test_generated_extension_counts_toward_filename_limit(reports_api):
    client, calls, *_ = reports_api
    assert upload(client, "a" * 200, image_bytes("PNG")).status_code == 422
    assert not calls


@pytest.mark.parametrize("live_role", ["patient", "doctor", None])
def test_live_admin_role_required_even_with_admin_token(reports_api, live_role):
    client, calls, state, *_ = reports_api
    state["admin_role"] = live_role
    assert upload(client).status_code == 403
    assert not calls


def test_authentication_required(reports_api):
    client, calls, _, _, app, lookup = reports_api
    app.dependency_overrides.pop(get_current_user)
    assert upload(client).status_code == 401
    assert not calls
    lookup.assert_not_called()


@pytest.mark.parametrize("role", ["doctor", "admin", None])
def test_target_must_have_patient_role(reports_api, role):
    client, calls, state, *_ = reports_api
    state["patient_role"] = role
    assert upload(client).status_code == 422
    assert not calls


@pytest.mark.parametrize("error,status", [
    (firebase_auth.UserNotFoundError("missing"), 404),
    (firebase_exceptions.UnavailableError("private auth details"), 503),
])
def test_patient_lookup_errors_prevent_upload(reports_api, error, status):
    client, calls, state, *_ = reports_api
    state["auth_error"] = error
    response = upload(client)
    assert response.status_code == status
    assert "private" not in response.text
    assert not calls


@pytest.mark.parametrize("filename", [
    "", "   ", "../report.pdf", "other/report.pdf", "other\\report.pdf",
    "/report.pdf", "report%2Ftest.pdf", "report?.pdf", "report#.pdf",
    "report\x00.pdf", ".hidden.pdf", "a" * 201 + ".pdf", "report.svg",
    "report.exe", "report .pdf",
])
def test_invalid_filename_never_reaches_storage(reports_api, filename):
    client, calls, *_ = reports_api
    assert upload(client, filename).status_code == 422
    assert not calls


@pytest.mark.parametrize("filename,data,status", [
    ("report.pdf", b"", 422), ("report.pdf", b"not a pdf", 422),
    ("report.pdf", b"%PDF-1.7\ntruncated", 422),
    ("report.pdf", b"<html>forged</html>\n%%EOF", 422),
    ("report.png", PDF, 422), ("report.pdf", image_bytes("PNG"), 422),
    ("report.jpg", image_bytes("GIF"), 422),
    ("report.jpg", image_bytes("JPEG")[:50], 422),
    ("report.pdf", b"x" * (MAX_REPORT_BYTES + 1), 413),
])
def test_invalid_or_oversize_file_prevents_upload(reports_api, filename, data, status):
    client, calls, *_ = reports_api
    response = upload(client, filename, data)
    assert response.status_code == status, response.text
    assert not calls


def test_large_image_dimensions_rejected(reports_api, monkeypatch):
    client, calls, *_ = reports_api
    monkeypatch.setattr("app.services.medical_reports.MAX_REPORT_PIXELS", 100)
    assert upload(client, "scan.png", image_bytes("PNG")).status_code == 413
    assert not calls


def test_entire_request_is_bounded(reports_api, monkeypatch):
    client, calls, *_ = reports_api
    monkeypatch.setattr("app.api.medical_report_uploads.MAX_REPORT_BYTES", 100)
    response = upload(client, data=b"x" * (100 + 64 * 1024))
    assert response.status_code == 413
    assert not calls


def test_only_expected_form_fields_are_allowed(reports_api):
    client, calls, *_ = reports_api
    for fields in ({}, {"filename": "r.pdf", "patient_uid": "other"}):
        response = client.post(URL, data=fields, files={"file": ("r.pdf", PDF)})
        assert response.status_code in {400, 422}, response.text
    response = client.post(URL, data={"filename": "r.pdf", "file": "not a file"})
    assert response.status_code == 415
    response = client.post(URL, files=[("filename", (None, "a.pdf")),
                                      ("filename", (None, "b.pdf")), ("file", ("r.pdf", PDF))])
    assert response.status_code in {400, 422}
    response = client.post(URL, files=[("filename", (None, "a.pdf")),
                                      ("file", ("a.pdf", PDF)), ("file", ("b.pdf", PDF))])
    assert response.status_code == 400
    assert not calls


def test_json_and_malformed_multipart_rejected(reports_api):
    client, calls, *_ = reports_api
    assert client.post(URL, json={"filename": "r.pdf", "file": "r.pdf"}).status_code == 415
    assert client.post(URL, content=b"broken", headers={"Content-Type": "multipart/form-data"}).status_code == 400
    assert not calls


@pytest.mark.parametrize("status,body", [
    (400, {"statusCode": "409", "error": "Duplicate", "message": "The resource already exists"}),
    (409, {"code": "ResourceAlreadyExists"}),
    (400, {"code": "KeyAlreadyExists"}),
])
def test_duplicate_reports_return_conflict(reports_api, status, body):
    client, calls, state, *_ = reports_api
    state.update(status=status, body=body)
    response = upload(client)
    assert response.status_code == 409, response.text
    assert "already exists" in response.text
    assert len(calls) == 1
    assert calls[0].headers["x-upsert"] == "false"


@pytest.mark.parametrize("timeout", [False, True])
def test_storage_failure_is_sanitized_and_does_not_delete(reports_api, timeout):
    client, calls, state, *_ = reports_api
    state.update(status=500, body={"message": "private sb_secret_test"}, timeout=timeout)
    response = upload(client)
    assert response.status_code == 502
    assert "sb_secret" not in response.text
    assert "private" not in response.text
    assert len(calls) == 1
    assert calls[0].method == "POST"


@pytest.mark.parametrize("body", [
    {"code": "NoSuchBucket", "message": "private provider details"},
    {"error": "not_found", "message": "Bucket not found"},
])
def test_missing_bucket_returns_actionable_configuration_error(reports_api, body):
    client, calls, state, *_ = reports_api
    state.update(status=400, body=body)
    response = upload(client)
    assert response.status_code == 503
    assert "create the private medical_reports bucket" in response.json()["error"]
    assert "private provider details" not in response.text
    assert len(calls) == 1


@pytest.mark.parametrize("key", [None, "sb_publishable_invalid"])
def test_missing_or_public_credentials_prevent_upload(reports_api, key):
    client, calls, _, settings, *_ = reports_api
    settings.supabase_secret_key = key
    assert upload(client).status_code == 503
    assert not calls


def test_legacy_service_role_key_supported(reports_api):
    client, calls, _, settings, *_ = reports_api
    settings.supabase_secret_key = "legacy-service-role-jwt"
    assert upload(client).status_code == 201
    assert calls[0].headers["authorization"] == "Bearer legacy-service-role-jwt"


def test_docs_expose_required_file_picker_and_filename():
    operation = create_app().openapi()["paths"][URL.replace("patient-uid", "{patient_uid}")]["post"]
    schema = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert set(schema["required"]) == {"filename", "file"}
    assert schema["properties"]["file"]["format"] == "binary"
    assert "201" in operation["responses"]
