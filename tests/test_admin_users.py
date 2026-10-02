from datetime import datetime, timezone

import firebase_admin._user_mgt as _user_mgt
import pytest

from app.services.users import coerce_timestamp, project_user


def _record(**overrides):
    data = {
        "localId": "uid-1",
        "email": "patient@example.com",
        "displayName": "Jane Patient",
        "phoneNumber": "+15550100",
        "photoUrl": "https://example.com/a.png",
        "emailVerified": True,
        "disabled": False,
        "passwordHash": "SUPER_SECRET_HASH",
        "passwordSalt": "SUPER_SECRET_SALT",
        "validSince": "1700000000",
        "createdAt": "1700000000000",
        "lastLoginAt": "1700000900000",
        "customAttributes": '{"role": "patient", "plan": "gold"}',
        "providerUserInfo": [
            {
                "providerId": "password",
                "rawId": "patient@example.com",
                "email": "patient@example.com",
            },
            {
                "providerId": "google.com",
                "rawId": "1234567890",
                "email": "patient@example.com",
            },
        ],
    }
    data.update(overrides)
    return _user_mgt.ExportedUserRecord(data)


def test_projection_maps_all_safe_fields() -> None:
    user = project_user(_record())

    assert user.uid == "uid-1"
    assert user.email == "patient@example.com"
    assert user.display_name == "Jane Patient"
    assert user.phone_number == "+15550100"
    assert user.email_verified is True
    assert user.disabled is False
    assert user.role == "patient"
    assert user.providers == ["google.com", "password"]
    assert user.created_at is not None
    assert user.created_at.year == 2023


def test_projection_excludes_credential_material() -> None:
    record = _record()
    user = project_user(record)

    dumped = user.model_dump_json()
    assert "SUPER_SECRET_HASH" not in dumped
    assert "SUPER_SECRET_SALT" not in dumped

    # No credential-named field may exist on the schema. ("password" as a
    # provider id in `providers` is expected and is not a credential.)
    leaked = {
        field
        for field in user.model_dump()
        if "password" in field or "salt" in field or "hash" in field
    }
    assert leaked == set()

    # Field names are exactly the intended allowlist.
    assert set(user.model_dump()) == {
        "uid",
        "email",
        "display_name",
        "phone_number",
        "photo_url",
        "email_verified",
        "disabled",
        "role",
        "providers",
        "created_at",
        "last_login_at",
    }


def test_projection_handles_sparse_record() -> None:
    user = project_user(_user_mgt.ExportedUserRecord({"localId": "uid-2"}))

    assert user.uid == "uid-2"
    assert user.email is None
    assert user.role is None
    assert user.providers == []
    assert user.created_at is None
    assert user.last_login_at is None


def test_projection_handles_missing_custom_claims() -> None:
    user = project_user(_record(customAttributes=None, customClaims=None))

    assert user.role is None


@pytest.mark.parametrize("value", [None, "", "not-a-timestamp", object()])
def test_coerce_timestamp_rejects_bad_input(value) -> None:
    assert coerce_timestamp(value) is None


def test_coerce_timestamp_parses_epoch_millis() -> None:
    parsed = coerce_timestamp(1700000000000)

    assert parsed == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_coerce_timestamp_parses_utc_z_suffix() -> None:
    parsed = coerce_timestamp("2024-03-01T12:30:00Z")

    assert parsed is not None
    assert parsed.year == 2024
    assert parsed.utcoffset().total_seconds() == 0


def test_coerce_timestamp_assumes_utc_for_naive_datetime() -> None:
    parsed = coerce_timestamp(datetime(2024, 3, 1, 12, 30, 0))

    assert parsed is not None
    assert parsed.utcoffset().total_seconds() == 0
