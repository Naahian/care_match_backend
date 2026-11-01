import json
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security import AuthenticatedUser, get_current_user
from app.main import create_app
from app.services.media import SupabaseMediaStorage

URL = "/api/v1/users/me/medical-reports"


def report(name="Blood Test.jpg"):
    return {"id": "object-id", "name": name, "metadata": {"size": 123},
            "created_at": "2026-11-01T10:00:00Z"}


@pytest.fixture
def patient_reports(monkeypatch):
    calls = []
    state = {"rows": [report()], "role": "patient", "fail": None}
    settings = Settings(_env_file=None, supabase_url="https://project.supabase.co",
                        supabase_secret_key="sb_secret_test", supabase_storage_public=True)

    def handle(request):
        calls.append(request)
        if state["fail"]:
            return httpx.Response(500, json={"message": "sb_secret_test private details"})
        if "/object/list/" in request.url.path:
            return httpx.Response(200, json=state["rows"])
        body = json.loads(request.content)
        links = [{"path": path, "signedURL": f"/object/sign/medical_reports/{quote(path)}?token=test"}
                 for path in body["paths"]]
        return httpx.Response(200, json=state.get("links", links))

    original_client = httpx.Client
    monkeypatch.setattr("app.services.media.httpx.Client", lambda **kwargs: original_client(
        transport=httpx.MockTransport(handle), **kwargs))
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        uid="patient-uid", claims={"role": state["role"]})
    app.dependency_overrides[SupabaseMediaStorage] = lambda: SupabaseMediaStorage(settings)
    return TestClient(app), calls, state, settings, app


def test_lists_own_folder_and_returns_signed_download_links(patient_reports):
    client, calls, _, settings, _ = patient_reports
    response = client.get(URL, params={"patient_uid": "another-patient"})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()["data"]
    assert data["next_offset"] is None
    item = data["reports"][0]
    assert item["patient_uid"] == "patient-uid"
    assert item["filename"] == "Blood Test.jpg"
    assert item["path"] == "patient-uid/Blood Test.jpg"
    assert item["content_type"] == "image/jpeg"
    assert item["size_bytes"] == 123
    assert item["created_at"] == "2026-11-01T10:00:00Z"
    assert item["expires_at"]
    assert item["download_url"] == "https://project.supabase.co/storage/v1/object/sign/medical_reports/patient-uid/Blood%20Test.jpg?token=test"
    assert json.loads(calls[0].content) == {
        "prefix": "patient-uid/", "limit": 21, "offset": 0,
        "sortBy": {"column": "name", "order": "asc"},
    }
    assert json.loads(calls[1].content) == {
        "paths": ["patient-uid/Blood Test.jpg"], "expiresIn": settings.supabase_signed_url_seconds,
    }
    assert "sb_secret_test" not in response.text


def test_pagination_signs_only_requested_page(patient_reports):
    client, calls, state, *_ = patient_reports
    state["rows"] = [report("A.pdf"), report("B.jpg"), report("C.jpg")]
    response = client.get(URL, params={"limit": 2, "offset": 5})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["next_offset"] == 7
    assert [r["filename"] for r in data["reports"]] == ["A.pdf", "B.jpg"]
    assert data["reports"][0]["content_type"] == "application/pdf"
    assert json.loads(calls[0].content)["offset"] == 5
    assert len(json.loads(calls[1].content)["paths"]) == 2


def test_empty_folder_returns_empty_page_without_signing(patient_reports):
    client, calls, state, *_ = patient_reports
    state["rows"] = []
    assert client.get(URL).json()["data"] == {"reports": [], "next_offset": None}
    assert len(calls) == 1


def test_folders_and_nonreports_are_not_signed(patient_reports):
    client, calls, state, *_ = patient_reports
    state["rows"] = [{"id": None, "name": "folder", "metadata": None}, report("notes.txt")]
    assert client.get(URL).json()["data"]["reports"] == []
    assert len(calls) == 1


@pytest.mark.parametrize("role", ["doctor", "admin", "guest", None])
def test_only_patients_can_list_reports(patient_reports, role):
    client, calls, state, *_ = patient_reports
    state["role"] = role
    assert client.get(URL).status_code == 403
    assert not calls


def test_missing_authentication_is_denied(patient_reports):
    client, calls, _, _, app = patient_reports
    app.dependency_overrides.pop(get_current_user)
    assert client.get(URL).status_code == 401
    assert not calls


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_invalid_pagination_is_rejected(patient_reports, params):
    client, calls, *_ = patient_reports
    assert client.get(URL, params=params).status_code == 422
    assert not calls


@pytest.mark.parametrize("rows", [{}, [None], [report("../other.pdf")],
                                  [report("other/report.jpg")],
                                  [{**report(), "metadata": {"size": -1}}]])
def test_invalid_listing_never_signs_unsafe_paths(patient_reports, rows):
    client, calls, state, *_ = patient_reports
    state["rows"] = rows
    assert client.get(URL).status_code == 502
    assert len(calls) == 1


@pytest.mark.parametrize("links", [[], [None], [{"path": "patient-uid/Blood Test.jpg", "error": "private details"}],
    [{"path": "another-patient/report.jpg", "signedURL": "/object/sign/medical_reports/another-patient/report.jpg?token=x"}],
    [{"path": "patient-uid/Blood Test.jpg", "signedURL": "https://evil.example/link?token=x"}],
    [{"path": "patient-uid/Blood Test.jpg", "signedURL": "/object/sign/medical_reports/other/report.jpg?token=x"}],
])
def test_invalid_signed_links_are_not_returned(patient_reports, links):
    client, _, state, *_ = patient_reports
    state["links"] = links
    response = client.get(URL)
    assert response.status_code == 502
    assert "private details" not in response.text
    assert "evil.example" not in response.text


def test_storage_failure_is_sanitized(patient_reports):
    client, _, state, *_ = patient_reports
    state["fail"] = True
    response = client.get(URL)
    assert response.status_code == 502
    assert "sb_secret_test" not in response.text
