import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from firebase_admin import auth as firebase_auth
from firebase_admin import exceptions as firebase_exceptions
from google.api_core.exceptions import ServiceUnavailable

from app.core.config import settings
from app.core.security import AuthenticatedUser, get_current_user
from app.main import create_app
from app.repositories.doctor import DoctorRepository
from app.services.booking import BookingService
from app.services.users import UserService
from tests.test_appointments import record, store
from tests.test_users_routes import _record
from tests.user_profiles_fake import FakeUserProfiles


@pytest.fixture
def earnings_api(store, monkeypatch):
    rows, repo = store
    monkeypatch.setattr(settings, "doctor_appointment_earning_tk", 100)
    claims = {"role": "doctor", "plan": "gold"}

    def lookup(uid):
        return _record(localId=uid, customAttributes=json.dumps(
            claims if uid == "doctor" else {"role": "patient"}))

    mirror = Mock(side_effect=lambda uid, data: claims.update(data))
    monkeypatch.setattr(firebase_auth, "get_user", lookup)
    monkeypatch.setattr(firebase_auth, "set_custom_user_claims", mirror)
    users = UserService(DoctorRepository(repo._db), FakeUserProfiles())
    app = create_app()
    actor = {"uid": "doctor", "role": "doctor"}
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        uid=actor["uid"], claims={"role": actor["role"]})
    app.dependency_overrides[UserService] = lambda: users
    app.dependency_overrides[BookingService] = lambda: BookingService(repo, users)
    return TestClient(app), rows, repo._db.earnings_rows, claims, mirror, actor, users


def complete(client, uid="a"):
    return client.patch(f"/api/v1/appointments/{uid}/status", params={"new_status": "completed"})


def test_completion_credits_once_and_users_me_returns_auth_total(earnings_api):
    client, rows, earnings, claims, mirror, *_ = earnings_api
    rows["a"] = record()
    assert complete(client).status_code == 200
    assert complete(client).status_code == 200
    assert earnings["doctor"]["total_earnings"] == 100
    assert rows["a"]["earnings_amount_tk"] == 100
    assert claims == {"role": "doctor", "plan": "gold", "total_earnings": 100}
    assert mirror.call_count == 1
    assert client.get("/api/v1/users/me").json()["data"]["total_earnings"] == 100
    rows["b"] = record()
    assert complete(client, "b").status_code == 200
    assert client.get("/api/v1/users/me").json()["data"]["total_earnings"] == 200


def test_amount_is_configurable_and_retries_keep_original_credit(earnings_api, monkeypatch):
    client, rows, earnings, *_ = earnings_api
    rows["a"] = record()
    monkeypatch.setattr(settings, "doctor_appointment_earning_tk", 150)
    assert complete(client).status_code == 200
    monkeypatch.setattr(settings, "doctor_appointment_earning_tk", 250)
    assert complete(client).status_code == 200
    assert earnings["doctor"]["total_earnings"] == 150
    assert rows["a"]["earnings_amount_tk"] == 150


def test_admin_completion_credits_treating_doctor(earnings_api):
    client, rows, earnings, _, _, actor, _ = earnings_api
    rows["a"] = record()
    actor.update(uid="admin", role="admin")
    assert complete(client).status_code == 200
    assert earnings["doctor"]["total_earnings"] == 100
    assert "admin" not in earnings


@pytest.mark.parametrize("status", ["requested", "confirmed", "cancelled"])
def test_noncompletion_does_not_credit(earnings_api, status):
    client, rows, earnings, _, mirror, *_ = earnings_api
    rows["a"] = record()
    assert client.patch("/api/v1/appointments/a/status", params={"new_status": status}).status_code == 200
    assert not earnings
    mirror.assert_not_called()


def test_expired_and_unauthorized_completions_do_not_credit(earnings_api):
    from datetime import datetime, timezone
    client, rows, earnings, _, _, actor, _ = earnings_api
    rows["a"] = record(scheduled_for=datetime(2000, 1, 1, tzinfo=timezone.utc))
    assert complete(client).status_code == 409
    rows["b"] = record()
    actor.update(uid="other", role="doctor")
    assert complete(client, "b").status_code == 404
    actor.update(uid="patient", role="patient")
    assert complete(client, "b").status_code == 403
    assert not earnings


def test_auth_mirror_failure_does_not_lose_or_repeat_credit(earnings_api):
    client, rows, earnings, claims, mirror, *_ = earnings_api
    rows["a"] = record()
    mirror.side_effect = firebase_exceptions.UnavailableError("temporary failure")
    assert complete(client).status_code == 200
    response = client.get("/api/v1/users/me")
    assert response.status_code == 200
    assert response.json()["data"]["total_earnings"] == 100
    mirror.side_effect = lambda uid, data: claims.update(data)
    assert complete(client).status_code == 200
    assert claims["total_earnings"] == 100
    assert earnings["doctor"]["total_earnings"] == 100


def test_get_me_repairs_stale_auth_total_from_authoritative_store(earnings_api):
    client, _, earnings, claims, *_ = earnings_api
    earnings["doctor"] = {"total_earnings": 300}
    claims["total_earnings"] = 100
    assert client.get("/api/v1/users/me").json()["data"]["total_earnings"] == 300
    assert claims["total_earnings"] == 300


def test_doctor_without_credits_starts_at_zero(earnings_api):
    client, _, _, claims, *_ = earnings_api
    assert client.get("/api/v1/users/me").json()["data"]["total_earnings"] == 0
    assert claims["total_earnings"] == 0


def test_users_cannot_edit_earnings(earnings_api):
    client, _, earnings, *_ = earnings_api
    assert client.patch("/api/v1/users/me", json={"total_earnings": 9999}).status_code == 422
    assert not earnings


def test_unavailable_earnings_store_does_not_return_stale_auth_total(earnings_api, monkeypatch):
    client, _, _, claims, _, _, users = earnings_api
    claims["total_earnings"] = 100
    monkeypatch.setattr(users._doctors, "get_total_earnings", Mock(side_effect=ServiceUnavailable("outage")))
    assert client.get("/api/v1/users/me").status_code == 503


def test_existing_completed_appointments_are_not_retroactively_credited(earnings_api):
    client, rows, earnings, *_ = earnings_api
    rows["a"] = record(status="completed")
    assert complete(client).status_code == 200
    assert not earnings


def test_admin_role_update_preserves_earnings_and_other_claims(monkeypatch):
    from app.core.security import require_admin
    app = create_app()
    app.dependency_overrides[require_admin] = lambda: AuthenticatedUser(uid="admin", claims={"role": "admin"})
    monkeypatch.setattr(firebase_auth, "get_user", lambda uid: _record(
        localId=uid, customAttributes=json.dumps({"role": "doctor", "total_earnings": 200, "plan": "gold"})))
    update = Mock()
    monkeypatch.setattr(firebase_auth, "update_user", update)
    response = TestClient(app).post("/api/v1/admin/users/doctor/role", json={"role": "doctor", "disabled": False})
    assert response.status_code == 204, response.text
    assert update.call_args.kwargs["custom_claims"] == {"role": "doctor", "total_earnings": 200, "plan": "gold"}
