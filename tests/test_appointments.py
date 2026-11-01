import asyncio
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.appointment_rules import is_expired
from app.core.config import settings
from app.core.security import AuthenticatedUser, get_current_user
from app.main import create_app
from app.repositories.appointment import AppointmentRepository
from app.schemas.appointments import AppointmentStatus
from app.schemas.users import UserResponse
from app.services.booking import BookingService
from app.services import appointment_expiry


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(settings, "appointment_timezone", "Asia/Dhaka")
    # Exercise repository transaction logic without a live Firestore server.
    monkeypatch.setattr("app.repositories.appointment.firestore.transactional", lambda fn: fn)
    rows = {}
    earnings_rows = {}
    db = Mock()
    collection = Mock()
    earnings_collection = Mock()
    db.collection.side_effect = lambda name: earnings_collection if name == "doctor_earnings" else collection
    db.earnings_rows = earnings_rows

    def earnings_document(uid):
        ref = Mock(id=uid)
        ref.get.side_effect = lambda: Mock(exists=uid in earnings_rows,
                                          to_dict=lambda: dict(earnings_rows.get(uid, {})))
        return ref

    earnings_collection.document.side_effect = earnings_document

    def set_earnings(ref, data, merge=False):
        previous = earnings_rows.get(ref.id, {}).get("total_earnings", 0)
        earnings_rows[ref.id] = {**data, "total_earnings": previous + data["total_earnings"].value}

    db.transaction.return_value.set.side_effect = set_earnings

    def document(uid=None):
        uid = uid or "new-appointment"
        ref = Mock(id=uid)

        def snapshot(**kwargs):
            return Mock(id=uid, exists=uid in rows,
                        to_dict=lambda: dict(rows.get(uid, {})))

        ref.get.side_effect = snapshot
        ref.set.side_effect = lambda data: rows.__setitem__(uid, dict(data))
        return ref

    collection.document.side_effect = document
    db.transaction.return_value.update.side_effect = lambda ref, data: rows[ref.id].update(data)

    def where(*, filter):
        def matches(data):
            value = data.get(filter.field_path)
            if filter.op_string == "==":
                return value == filter.value
            if filter.op_string == "in":
                return value in filter.value
            return value < filter.value

        query = Mock()
        query.stream.side_effect = lambda: [
            document(uid).get() for uid, data in rows.items() if matches(data)
        ]
        query.limit.return_value = query
        return query

    collection.where.side_effect = where
    return rows, AppointmentRepository(db)


def record(**changes):
    return {
        "doctor_id": "doctor", "patient_id": "patient",
        "scheduled_for": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "status": "confirmed", "reason": "Follow-up",
        "appointment_type": "video", **changes,
    }


@pytest.fixture
def api(store):
    rows, repo = store
    users = Mock()
    users.get_self.return_value = UserResponse(uid="patient", display_name="Jane Doe")
    app = create_app()
    actor = {"uid": "doctor", "role": "doctor"}
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        uid=actor["uid"], claims={"role": actor["role"]}
    )
    app.dependency_overrides[BookingService] = lambda: BookingService(repo, users)
    return TestClient(app), rows, actor, users


@pytest.mark.parametrize("appointment_type", ["video", "chat", "in_person"])
def test_type_and_patient_name_on_create_list_and_complete(api, appointment_type):
    client, rows, actor, _ = api
    response = client.post("/api/v1/appointments", json={
        "doctor_id": "doctor", "patient_id": "patient",
        "scheduled_for": "2099-01-01T12:00:00Z", "reason": "Consultation",
        "appointment_type": appointment_type,
    })
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["patient_name"] == "Jane Doe"
    assert data["appointment_type"] == appointment_type
    uid = data["appointment_id"]
    assert rows[uid]["appointment_type"] == appointment_type
    for role in ("patient", "doctor"):
        actor.update(uid=role, role=role)
        listed = client.get("/api/v1/appointments", params={"role": role}).json()["data"]
        assert listed[0]["patient_name"] == "Jane Doe"
        assert listed[0]["appointment_type"] == appointment_type
    response = client.patch(f"/api/v1/appointments/{uid}/status", params={"new_status": "completed"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "completed"
    assert response.json()["data"]["patient_name"] == "Jane Doe"


@pytest.mark.parametrize("status", ["cancelled", "completed"])
def test_patients_cannot_change_status(api, status):
    client, rows, actor, _ = api
    rows["a"] = record()
    actor.update(uid="patient", role="patient")
    assert client.patch("/api/v1/appointments/a/status", params={"new_status": status}).status_code == 403
    assert rows["a"]["status"] == "confirmed"


def test_other_doctor_cannot_complete_and_admin_can(api):
    client, rows, actor, _ = api
    rows["a"] = record()
    actor["uid"] = "another-doctor"
    assert client.patch("/api/v1/appointments/a/status", params={"new_status": "completed"}).status_code == 404
    actor.update(uid="admin", role="admin")
    assert client.patch("/api/v1/appointments/a/status", params={"new_status": "completed"}).status_code == 200


@pytest.mark.parametrize("status", ["completed", "cancelled"])
def test_terminal_appointments_cannot_be_reopened(api, status):
    client, rows, _, _ = api
    rows["a"] = record(status=status)
    response = client.patch("/api/v1/appointments/a/status", params={"new_status": "confirmed"})
    assert response.status_code == 409
    assert rows["a"]["status"] == status


def test_expiry_is_calendar_date_in_configured_timezone(store):
    appointment = record(scheduled_for=datetime(2026, 11, 1, 2, tzinfo=timezone.utc))
    # 23:59:59 on the appointment day in Dhaka, then midnight the next day.
    assert not is_expired(appointment, datetime(2026, 11, 1, 17, 59, 59, tzinfo=timezone.utc))
    assert is_expired(appointment, datetime(2026, 11, 1, 18, tzinfo=timezone.utc))
    assert not is_expired({**appointment, "status": "completed"}, datetime(2026, 11, 2, tzinfo=timezone.utc))


def test_background_sweep_has_no_fifty_record_limit_and_preserves_terminal_records(store):
    rows, repo = store
    past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    rows.update({f"a{i}": record(scheduled_for=past) for i in range(65)})
    rows["completed"] = record(scheduled_for=past, status="completed")
    rows["cancelled"] = record(scheduled_for=past, status="cancelled")
    rows["future"] = record()
    repo.cancel_expired()
    assert all(rows[f"a{i}"]["status"] == "cancelled" for i in range(65))
    assert rows["a0"]["updated_by"] == "system"
    assert rows["completed"]["status"] == "completed"
    assert rows["future"]["status"] == "confirmed"
    repo.cancel_expired()  # Repeated sweeps are safe.


def test_expiry_rechecks_latest_status_before_writing(store):
    rows, repo = store
    stale = {"id": "a", **record(scheduled_for=datetime(2000, 1, 1, tzinfo=timezone.utc))}
    rows["a"] = {**stale, "status": "completed"}
    assert repo._expire_record(stale)["status"] == "completed"
    repo._db.transaction.return_value.update.assert_not_called()


def test_list_and_status_update_cancel_expired_appointments(api):
    client, rows, _, _ = api
    rows["a"] = record(scheduled_for=datetime(2000, 1, 1, tzinfo=timezone.utc))
    listed = client.get("/api/v1/appointments", params={"role": "doctor"}).json()["data"]
    assert listed[0]["status"] == "cancelled"
    rows["a"]["status"] = "confirmed"
    response = client.patch("/api/v1/appointments/a/status", params={"new_status": "completed"})
    assert response.status_code == 409
    assert rows["a"]["status"] == "cancelled"


def test_legacy_type_and_missing_patient_profile(api):
    client, rows, _, users = api
    rows["a"] = record()
    del rows["a"]["appointment_type"]
    users.get_self.side_effect = HTTPException(status_code=404)
    data = client.get("/api/v1/appointments", params={"role": "doctor"}).json()["data"][0]
    assert data["appointment_type"] == "video"
    assert data["patient_name"] is None


@pytest.mark.parametrize("changes", [
    {"appointment_type": "phone"}, {"scheduled_for": "2099-01-01T12:00:00"},
])
def test_invalid_booking_fields_rejected(api, changes):
    client, rows, _, _ = api
    response = client.post("/api/v1/appointments", json={
        "doctor_id": "doctor", "patient_id": "patient", "reason": "Consultation",
        "scheduled_for": "2099-01-01T12:00:00Z", **changes,
    })
    assert response.status_code == 422
    assert not rows


async def test_expiry_worker_retries_after_failure_and_can_be_cancelled(monkeypatch, caplog):
    attempts = []

    def sweep():
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("temporary database outage")

    async def interval(seconds):
        assert seconds == 60
        if len(attempts) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(appointment_expiry, "cancel_expired_appointments", sweep)
    monkeypatch.setattr(appointment_expiry.asyncio, "sleep", interval)
    with pytest.raises(asyncio.CancelledError):
        await appointment_expiry.run_appointment_expiry()
    assert len(attempts) == 2
    assert "Automatic appointment cancellation failed" in caplog.text
