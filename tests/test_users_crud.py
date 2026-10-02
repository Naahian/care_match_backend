from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from firebase_admin import auth as firebase_auth
from pydantic import ValidationError

from app.schemas.models import (
    Clinic,
    DoctorCreate,
    DoctorProfile,
    PatientCreate,
    TimeRange,
    UserUpdate,
)
from app.services.users import UserService


def _record(**overrides):
    data = {
        "localId": "uid-1",
        "email": "user@example.com",
        "displayName": "User One",
        "emailVerified": False,
        "disabled": False,
        "passwordHash": "SECRET_HASH",
        "passwordSalt": "SECRET_SALT",
        "customAttributes": '{"role": "patient"}',
        "providerUserInfo": [
            {"providerId": "password", "rawId": "user@example.com"}
        ],
    }
    data.update(overrides)
    import firebase_admin._user_mgt as m

    return m.ExportedUserRecord(data)


class FakeAuth:
    """Minimal stand-in for Firebase Auth, sharing state across calls.

    Real ``update_user`` returns the *whole* updated record, including fields it
    did not change, so these fakes do the same rather than resetting to a
    pristine record.
    """

    def __init__(self) -> None:
        self.record = _record()

    def create_user(self, **kwargs):
        self.record = _record(
            email=kwargs.get("email"), displayName=kwargs.get("display_name")
        )
        return self.record

    def update_user(self, uid, **kwargs):
        import json

        claims = kwargs.get("custom_claims")
        if claims:
            self.record = _record(
                localId=uid,
                email=self.record.email,
                displayName=self.record.display_name,
                customAttributes=json.dumps(claims),
            )
        return self.record


@contextmanager
def fake_auth(role: str = "patient"):
    """Patch ``create_user``/``update_user`` with one shared stateful fake.

    Both fakes share a record so that ``update_user`` returns the same account
    ``create_user`` made, exactly as the real SDK does.
    """
    fake = FakeAuth()
    with patch.object(firebase_auth, "create_user", side_effect=fake.create_user), patch.object(
        firebase_auth, "update_user", side_effect=fake.update_user
    ) as update:
        yield fake, update


class FakeDoctorRepo:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, DoctorProfile, object]] = []
        self.display_names: list[tuple[str, object]] = []
        self.updates: list[tuple[str, dict]] = []
        self.rows: list[dict] = []
        self.get_result = None
        self.get_raises: Exception | None = None
        self.fail = fail

    def create_for_user(self, uid, profile, *, email=None, display_name=None):
        self.calls.append((uid, profile, email))
        self.display_names.append((uid, display_name))
        if self.fail:
            raise RuntimeError("firestore unavailable")
        return uid

    def list_all(self, limit=50, *, verified=None):
        return list(self.rows)

    def get(self, doc_id):
        if self.get_raises is not None:
            raise self.get_raises
        return self.get_result

    def update(self, doc_id, data):
        self.updates.append((doc_id, data))


@pytest.fixture
def repo() -> FakeDoctorRepo:
    return FakeDoctorRepo()


@pytest.fixture
def service(repo: FakeDoctorRepo) -> UserService:
    return UserService(doctor_repo=repo)


def _profile_kwargs() -> dict:
    return {
        "phone": "+15550100",
        "clinics": [{"name": "City Clinic", "location": "Downtown"}],
        "specialties": ["Cardiology"],
        "degrees": ["MBBS", "MD"],
        "time_available": [{"start": "09:00", "end": "17:00"}],
        "days_available": ["Monday", "Wednesday"],
    }


# --- payload separation ---------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("role", "doctor"),
        ("role", "admin"),
        ("doctor_profile", {"phone": "+1"}),
        ("specialties", ["Cardiology"]),
        ("phone", "+15550100"),
        ("clinics", [{"name": "A", "location": "B"}]),
    ],
)
def test_patient_payload_has_no_role_or_doctor_fields(field: str, value) -> None:
    """The patient payload must not accept role or any doctor-only field."""
    with pytest.raises(ValidationError):
        PatientCreate(
            email="p@example.com", password="secret123", **{field: value}
        )


def test_patient_payload_fields() -> None:
    payload = PatientCreate(email="p@example.com", password="secret123")

    assert set(payload.model_dump()) == {"email", "password", "display_name"}


def test_doctor_payload_includes_profile_fields_flat() -> None:
    payload = DoctorCreate(
        email="d@example.com", password="secret123", **_profile_kwargs()
    )

    assert set(payload.model_dump()) == {
        "email",
        "password",
        "display_name",
        "phone",
        "clinics",
        "specialties",
        "degrees",
        "time_available",
        "days_available",
    }


def test_doctor_payload_converts_to_profile() -> None:
    payload = DoctorCreate(
        email="d@example.com", password="secret123", **_profile_kwargs()
    )
    profile = payload.to_profile()

    assert isinstance(profile, DoctorProfile)
    assert profile.phone == "+15550100"
    assert profile.clinics == [Clinic(name="City Clinic", location="Downtown")]
    assert profile.time_available == [TimeRange(start="09:00", end="17:00")]


def test_doctor_payload_applies_profile_normalisation() -> None:
    payload = DoctorCreate(
        email="d@example.com",
        password="secret123",
        phone="+1",
        specialties=["Cardio", " Cardio "],
        days_available=["Monday", "Monday"],
    )

    assert payload.specialties == ["Cardio"]
    assert payload.days_available == ["Monday"]


def test_doctor_payload_rejects_invalid_weekday() -> None:
    with pytest.raises(ValidationError):
        DoctorCreate(
            email="d@example.com",
            password="secret123",
            phone="+1",
            days_available=["Funday"],  # type: ignore[list-item]
        )


def test_doctor_payload_rejects_reversed_time_range() -> None:
    with pytest.raises(ValidationError):
        DoctorCreate(
            email="d@example.com",
            password="secret123",
            phone="+1",
            time_available=[{"start": "17:00", "end": "09:00"}],
        )


def test_doctor_payload_requires_phone() -> None:
    with pytest.raises(ValidationError):
        DoctorCreate(email="d@example.com", password="secret123")  # type: ignore[call-arg]


# --- patient creation -----------------------------------------------------


def test_create_patient_sets_patient_role(service: UserService) -> None:
    payload = PatientCreate(email="p@example.com", password="secret123")

    with fake_auth("patient") as (_, update):
        result = service.create_patient(payload)

    assert result.role == "patient"
    assert update.call_args.kwargs["custom_claims"] == {"role": "patient"}


def test_create_patient_writes_no_doctor_document(
    service: UserService, repo: FakeDoctorRepo
) -> None:
    payload = PatientCreate(email="p@example.com", password="secret123")

    with fake_auth("patient"):
        service.create_patient(payload)

    assert repo.calls == []


def test_create_patient_duplicate_email_returns_409(service: UserService) -> None:
    payload = PatientCreate(email="dupe@example.com", password="secret123")

    with patch.object(
        firebase_auth,
        "create_user",
        side_effect=firebase_auth.EmailAlreadyExistsError("exists", None, None),
    ):
        with pytest.raises(HTTPException) as exc:
            service.create_patient(payload)

    assert exc.value.status_code == 409


def test_create_weak_password_maps_to_400(service: UserService) -> None:
    payload = PatientCreate(email="a@example.com", password="secret123")

    with patch.object(firebase_auth, "create_user", side_effect=ValueError("bad pw")):
        with pytest.raises(HTTPException) as exc:
            service.create_patient(payload)

    assert exc.value.status_code == 400


# --- doctor creation ------------------------------------------------------


def test_create_doctor_writes_profile_document(
    service: UserService, repo: FakeDoctorRepo
) -> None:
    payload = DoctorCreate(
        email="d@example.com", password="secret123", **_profile_kwargs()
    )

    with fake_auth("doctor"):
        result = service.create_doctor(payload)

    assert result.role == "doctor"
    assert len(repo.calls) == 1
    uid, profile, email = repo.calls[0]
    assert uid == "uid-1"
    assert profile.phone == "+15550100"
    assert email == "d@example.com"


def test_failed_profile_write_rolls_back_account(repo: FakeDoctorRepo) -> None:
    repo.fail = True
    service = UserService(doctor_repo=repo)
    payload = DoctorCreate(
        email="d@example.com", password="secret123", **_profile_kwargs()
    )

    with fake_auth("doctor"), patch.object(firebase_auth, "delete_user") as delete:
        with pytest.raises(HTTPException) as exc:
            service.create_doctor(payload)

    assert exc.value.status_code == 502
    delete.assert_called_once_with("uid-1")


def test_service_exposes_no_generic_create() -> None:
    """There must be no role-taking create path left on the service."""
    public = {
        name
        for name in dir(UserService)
        if not name.startswith("_") and callable(getattr(UserService, name))
    }
    assert public == {
        "create_patient",
        "create_doctor",
        "list_doctors",
        "get_self",
        "update_self",
        "delete_self",
    }


# --- profile projection ---------------------------------------------------


def test_repository_maps_profile_to_firestore_document() -> None:
    from app.repositories.doctor import DoctorRepository

    captured: dict = {}

    class _Doc:
        id = "uid-1"

        def set(self, data):
            captured.update(data)

    class _Coll:
        def document(self, doc_id=None):
            captured["_doc_id"] = doc_id
            return _Doc()

    class _Db:
        def collection(self, name):
            captured["_collection"] = name
            return _Coll()

    repo = DoctorRepository(db=_Db())
    doc_id = repo.create_for_user(
        "uid-1",
        DoctorProfile(**_profile_kwargs()),
        email="d@example.com",
        display_name="Dr Who",
    )

    assert doc_id == "uid-1"
    assert captured["_collection"] == "doctors"
    assert captured["_doc_id"] == "uid-1"
    assert captured["uid"] == "uid-1"
    assert captured["display_name"] == "Dr Who"
    assert captured["phone"] == "+15550100"
    assert captured["clinics"] == [{"name": "City Clinic", "location": "Downtown"}]
    assert captured["specialties"] == ["Cardiology"]
    assert captured["degrees"] == ["MBBS", "MD"]
    assert captured["time_available"] == [{"start": "09:00", "end": "17:00"}]
    assert captured["days_available"] == ["Monday", "Wednesday"]
    assert captured["verified"] is False


def test_repository_persists_verified_flag() -> None:
    from app.repositories.doctor import DoctorRepository

    captured: dict = {}

    class _Doc:
        id = "uid"

        def set(self, data):
            captured.update(data)

    class _Coll:
        def document(self, doc_id=None):
            return _Doc()

    class _Db:
        def collection(self, name):
            return _Coll()

    repo = DoctorRepository(db=_Db())
    repo.create_for_user(
        "uid", DoctorProfile(**_profile_kwargs(), verified=True), email="d@x.co"
    )

    assert captured["verified"] is True


def test_profile_defaults_to_unverified() -> None:
    assert DoctorProfile(**_profile_kwargs()).verified is False


# --- display_name ---------------------------------------------------------


def test_create_doctor_persists_display_name_to_document(
    service: UserService, repo: FakeDoctorRepo
) -> None:
    """The directory reads names from Firestore, so registration must store one."""
    payload = DoctorCreate(
        email="d@example.com",
        password="secret123",
        display_name="Dr Tamzid",
        **_profile_kwargs(),
    )

    with fake_auth("doctor"):
        service.create_doctor(payload)

    uid, display_name = repo.display_names[0]
    assert uid == "uid-1"
    assert display_name == "Dr Tamzid"


def test_list_doctors_returns_stored_display_name(service: UserService) -> None:
    service._doctors.rows = [
        {
            "id": "doc-uid",
            "uid": "doc-uid",
            "display_name": "Dr Tamzid",
            "specialties": ["Cardiology"],
            "verified": True,
        }
    ]

    doctors = service.list_doctors()

    assert doctors[0].display_name == "Dr Tamzid"


def test_list_doctors_tolerates_legacy_doc_without_display_name(
    service: UserService,
) -> None:
    """Pre-fix documents have no display_name and must not break the listing."""
    service._doctors.rows = [{"id": "legacy", "uid": "legacy"}]

    doctors = service.list_doctors()

    assert doctors[0].uid == "legacy"
    assert doctors[0].display_name is None


def test_update_self_mirrors_display_name_to_doctor_document(
    service: UserService, repo: FakeDoctorRepo
) -> None:
    repo.get_result = {"uid": "uid-1"}
    payload = UserUpdate(display_name="Dr Renamed")

    with patch.object(
        firebase_auth,
        "update_user",
        return_value=_record(displayName="Dr Renamed"),
    ):
        service.update_self("uid-1", payload)

    assert repo.updates == [("uid-1", {"display_name": "Dr Renamed"})]


def test_update_self_skips_firestore_when_no_doctor_document(
    service: UserService, repo: FakeDoctorRepo
) -> None:
    """A patient has no doctors doc; the update must still succeed."""
    repo.get_result = None
    payload = UserUpdate(display_name="New Name")

    with patch.object(
        firebase_auth, "update_user", return_value=_record(displayName="New Name")
    ):
        result = service.update_self("uid-1", payload)

    assert result.display_name == "New Name"
    assert repo.updates == []


def test_display_name_sync_failure_does_not_fail_update(
    service: UserService, repo: FakeDoctorRepo
) -> None:
    repo.get_raises = RuntimeError("firestore down")
    payload = UserUpdate(display_name="Dr Renamed")

    with patch.object(
        firebase_auth,
        "update_user",
        return_value=_record(displayName="Dr Renamed"),
    ):
        result = service.update_self("uid-1", payload)

    assert result.display_name == "Dr Renamed"


def test_repository_document_is_firestore_serialisable() -> None:
    from app.repositories.doctor import DoctorRepository

    captured: dict = {}

    class _Doc:
        id = "uid"

        def set(self, data):
            captured.update(data)

    class _Coll:
        def document(self, doc_id=None):
            return _Doc()

    class _Db:
        def collection(self, name):
            return _Coll()

    repo = DoctorRepository(db=_Db())
    repo.create_for_user("uid", DoctorProfile(**_profile_kwargs()))

    doc = {k: v for k, v in captured.items() if not k.startswith("_")}
    assert set(doc) == {
        "uid",
        "email",
        "display_name",
        "phone",
        "clinics",
        "specialties",
        "degrees",
        "time_available",
        "days_available",
        "verified",
    }
    assert all(isinstance(v, (str, list, bool, type(None))) for v in doc.values())


# --- update (unchanged by the split) --------------------------------------


def test_update_rejects_role_field() -> None:
    with pytest.raises(ValidationError):
        UserUpdate(role="admin")  # type: ignore[call-arg]


@pytest.mark.parametrize("field", ["email", "password", "role", "uid", "disabled"])
def test_update_schema_forbids_non_profile_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        UserUpdate(**{field: "x"})  # type: ignore[arg-type]


def test_update_accepts_only_the_three_profile_fields() -> None:
    payload = UserUpdate(
        display_name="A", phone_number="+15550100", photo_url="https://x.dev/a.png"
    )

    assert set(payload.model_dump()) == {"display_name", "phone_number", "photo_url"}


def test_update_applies_all_three_fields(service: UserService) -> None:
    payload = UserUpdate(
        display_name="A", phone_number="+15550100", photo_url="https://x.dev/a.png"
    )

    with patch.object(firebase_auth, "update_user", return_value=_record()) as update:
        service.update_self("uid-1", payload)

    assert update.call_args.kwargs == {
        "display_name": "A",
        "phone_number": "+15550100",
        "photo_url": "https://x.dev/a.png",
    }
    assert update.call_args.args[0] == "uid-1"


@pytest.mark.parametrize(
    "field,value",
    [
        ("display_name", "Only Name"),
        ("phone_number", "+15550100"),
        ("photo_url", "https://x.dev/a.png"),
    ],
)
def test_update_accepts_any_single_field(
    service: UserService, field: str, value: str
) -> None:
    payload = UserUpdate(**{field: value})

    with patch.object(firebase_auth, "update_user", return_value=_record()) as update:
        service.update_self("uid-1", payload)

    assert update.call_args.kwargs == {field: value}


@pytest.mark.parametrize(
    "field", ["display_name", "phone_number", "photo_url"]
)
def test_empty_string_clears_field_instead_of_400(
    service: UserService, field: str
) -> None:
    payload = UserUpdate(**{field: ""})

    with patch.object(firebase_auth, "update_user", return_value=_record()) as update:
        service.update_self("uid-1", payload)

    assert update.call_args.kwargs == {field: firebase_auth.DELETE_ATTRIBUTE}


@pytest.mark.parametrize(
    "field", ["display_name", "phone_number", "photo_url"]
)
def test_explicit_null_clears_field(service: UserService, field: str) -> None:
    payload = UserUpdate.model_validate({field: None})

    with patch.object(firebase_auth, "update_user", return_value=_record()) as update:
        service.update_self("uid-1", payload)

    assert update.call_args.kwargs == {field: firebase_auth.DELETE_ATTRIBUTE}


def test_omitted_field_is_left_untouched(service: UserService) -> None:
    with patch.object(firebase_auth, "update_user", return_value=_record()) as update:
        service.update_self("uid-1", UserUpdate(display_name="A"))

    assert "phone_number" not in update.call_args.kwargs
    assert "photo_url" not in update.call_args.kwargs


def test_whitespace_only_value_clears_field(service: UserService) -> None:
    with patch.object(firebase_auth, "update_user", return_value=_record()) as update:
        service.update_self("uid-1", UserUpdate(display_name="   "))

    assert update.call_args.kwargs == {"display_name": firebase_auth.DELETE_ATTRIBUTE}


def test_single_field_update_with_other_empty_strings_succeeds(
    service: UserService,
) -> None:
    payload = UserUpdate.model_validate(
        {"display_name": "Real Name", "phone_number": "", "photo_url": ""}
    )

    with patch.object(firebase_auth, "update_user", return_value=_record()) as update:
        service.update_self("uid-1", payload)

    assert update.call_args.kwargs == {
        "display_name": "Real Name",
        "phone_number": firebase_auth.DELETE_ATTRIBUTE,
        "photo_url": firebase_auth.DELETE_ATTRIBUTE,
    }


def test_update_never_sends_credentials_or_claims(service: UserService) -> None:
    payload = UserUpdate(display_name="A", phone_number="+1", photo_url="https://x")

    with patch.object(firebase_auth, "update_user", return_value=_record()) as update:
        service.update_self("uid-1", payload)

    kwargs = update.call_args.kwargs
    assert "password" not in kwargs
    assert "email" not in kwargs
    assert "custom_claims" not in kwargs
    assert "disabled" not in kwargs


def test_empty_update_is_rejected(service: UserService) -> None:
    with pytest.raises(HTTPException) as exc:
        service.update_self("uid-1", UserUpdate())

    assert exc.value.status_code == 400


def test_update_targets_only_the_given_uid(service: UserService) -> None:
    with patch.object(firebase_auth, "update_user", return_value=_record()) as update:
        service.update_self("caller-uid", UserUpdate(display_name="X"))

    assert update.call_args.args[0] == "caller-uid"


def test_get_never_exposes_credentials(service: UserService) -> None:
    with patch.object(firebase_auth, "get_user", return_value=_record()):
        user = service.get_self("uid-1")

    dumped = user.model_dump_json()
    assert "SECRET_HASH" not in dumped
    assert "SECRET_SALT" not in dumped


def test_delete_targets_only_the_given_uid(service: UserService) -> None:
    with patch.object(firebase_auth, "delete_user") as delete:
        service.delete_self("caller-uid")

    assert delete.call_args.args[0] == "caller-uid"


def test_missing_user_returns_404(service: UserService) -> None:
    with patch.object(
        firebase_auth,
        "get_user",
        side_effect=firebase_auth.UserNotFoundError("nope", None),
    ):
        with pytest.raises(HTTPException) as exc:
            service.get_self("ghost")

    assert exc.value.status_code == 404
