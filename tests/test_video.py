import base64
import hashlib
import hmac
import time
from unittest.mock import Mock
from zlib import crc32

import pytest
from agora_token_builder.AccessToken import unPackContent, unPackMessages
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security import AuthenticatedUser, get_current_user
from app.main import create_app
from app.repositories.appointment import AppointmentRepository
from app.schemas.appointments import AppointmentStatus
from app.services.booking import BookingService
from app.services.video import VideoService


@pytest.fixture
def video_api():
    repository = Mock()
    repository.get_for_user.return_value = {"video_room_id": "old-room-name"}
    settings = Settings(
        _env_file=None,
        agora_app_id="a" * 32,
        agora_app_certificate="b" * 32,
    )
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        uid="patient-1", claims={"role": "patient"}
    )
    app.dependency_overrides[VideoService] = lambda: VideoService(repository, settings)
    return TestClient(app), repository, settings, app


def test_video_token_is_signed_for_appointment_with_rtc_privileges(video_api):
    client, repository, settings, _ = video_api
    before = int(time.time())
    response = client.post("/api/v1/video/token", json={"appointment_id": "appointment-1"})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) == {"token", "channelName"}
    assert data["channelName"] == "appointment-1"
    token = data["token"]
    assert token[:35] == "006" + settings.agora_app_id
    signature, channel_crc, uid_crc, message = unPackContent(base64.b64decode(token[35:]))
    expected = hmac.new(
        settings.agora_app_certificate.encode(),
        settings.agora_app_id.encode() + b"appointment-1" + message,
        hashlib.sha256,
    ).digest()
    assert hmac.compare_digest(signature, expected)
    assert channel_crc == crc32(b"appointment-1")
    assert uid_crc == 0
    _, _, privileges = unPackMessages(message)
    assert set(privileges) == {1, 2, 3, 4}
    assert all(before + 3600 <= expiry <= int(time.time()) + 3600
               for expiry in privileges.values())
    repository.get_for_user.assert_called_once_with("appointment-1", "patient-1", "patient")
    repository.update.assert_not_called()


def test_video_token_rejects_missing_or_inaccessible_appointment(video_api):
    client, repository, _, _ = video_api
    repository.get_for_user.return_value = None
    response = client.post("/api/v1/video/token", json={"appointment_id": "missing"})
    assert response.status_code == 404


@pytest.mark.parametrize("credential", ["agora_app_id", "agora_app_certificate"])
def test_video_token_requires_agora_credentials(video_api, credential):
    client, _, settings, _ = video_api
    setattr(settings, credential, None)
    response = client.post("/api/v1/video/token", json={"appointment_id": "appointment-1"})
    assert response.status_code == 503


def test_video_token_requires_authentication(video_api):
    client, _, _, app = video_api
    app.dependency_overrides.pop(get_current_user)
    assert client.post("/api/v1/video/token", json={"appointment_id": "appointment-1"}).status_code == 401


@pytest.mark.parametrize("appointment_id", ["", "a" * 65])
def test_video_token_validates_channel_length(video_api, appointment_id):
    client, repository, _, _ = video_api
    assert client.post("/api/v1/video/token", json={"appointment_id": appointment_id}).status_code == 422
    repository.get_for_user.assert_not_called()


@pytest.mark.parametrize("role,uid,expected", [
    ("doctor", "doctor-1", 200),
    ("patient", "patient-1", 200),
    ("admin", "admin-1", 200),
    ("doctor", "other-doctor", 404),
    ("patient", "other-patient", 404),
    ("doctor", "patient-1", 200),
    ("patient", "doctor-1", 200),
])
def test_created_appointment_video_access_uses_participant_uids(video_api, role, uid, expected):
    client, _, settings, app = video_api
    rows = {}
    repository = AppointmentRepository(Mock())

    def create(data):
        rows["new-appointment"] = {"id": "new-appointment", **data}
        return "new-appointment"

    repository.create = Mock(side_effect=create)
    repository.get = Mock(side_effect=rows.get)
    app.dependency_overrides[BookingService] = lambda: BookingService(repository)
    app.dependency_overrides[VideoService] = lambda: VideoService(repository, settings)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        uid=uid, claims={"role": role}
    )
    response = client.post("/api/v1/appointments", json={
        "doctor_id": "doctor-1",
        "patient_id": "patient-1",
        "scheduled_for": "2099-01-01T12:00:00Z",
        "reason": "Consultation",
    })
    assert response.status_code == 201, response.text
    appointment_id = response.json()["data"]["appointment_id"]
    response = client.post("/api/v1/video/token", json={"appointment_id": appointment_id})
    assert response.status_code == expected, response.text
    if expected == 200:
        assert response.json()["data"]["channelName"] == appointment_id


def test_admin_cannot_access_missing_appointment():
    repository = AppointmentRepository(Mock())
    repository.get = Mock(return_value=None)
    assert repository.get_for_user("missing", "admin-1", "admin") is None


def test_unknown_role_cannot_access_appointment():
    repository = AppointmentRepository(Mock())
    repository.get = Mock(return_value={"patient_id": "patient-1"})
    assert repository.get_for_user("appointment-1", "patient-1", "guest") is None


def test_doctor_joining_as_patient_cannot_change_appointment_status():
    repository = AppointmentRepository(Mock())
    repository.get = Mock(return_value={"patient_id": "doctor-as-patient", "doctor_id": "treating-doctor"})
    repository.update = Mock()
    with pytest.raises(PermissionError):
        BookingService(repository).transition(
            "appointment-1", AppointmentStatus.CONFIRMED,
            actor_uid="doctor-as-patient", actor_role="doctor",
        )
    repository.update.assert_not_called()
