import logging
from datetime import datetime, timezone
from typing import Any, Optional

from firebase_admin import auth as firebase_auth
from firebase_admin import exceptions as firebase_exceptions
from fastapi import Depends, HTTPException, status

from app.repositories.doctor import DoctorRepository
from app.schemas.models import (
    DoctorCreate,
    DoctorProfile,
    DoctorSummary,
    PatientCreate,
    Role,
    UserResponse,
    UserUpdate,
)

PROFILE_FIELDS = ("display_name", "phone_number", "photo_url")

logger = logging.getLogger(__name__)


def coerce_timestamp(value: Any) -> Optional[datetime]:
    """Coerce a Firebase timestamp to an aware datetime.

    firebase-admin exposes metadata timestamps as epoch milliseconds, but
    returns ``datetime`` in some versions and RFC3339 strings elsewhere, so
    accept all three and normalise to UTC.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def project_user(record: Any) -> UserResponse:
    """Project a UserRecord onto the API response.

    Explicit allowlist: UserRecord also exposes password_hash, password_salt and
    raw provider data, which must never reach an API response.
    """
    claims = record.custom_claims or {}
    metadata = record.user_metadata

    return UserResponse(
        uid=record.uid,
        email=record.email,
        display_name=record.display_name,
        phone_number=record.phone_number,
        photo_url=record.photo_url,
        email_verified=record.email_verified,
        disabled=record.disabled,
        role=claims.get("role"),
        providers=sorted({p.provider_id for p in record.provider_data if p.provider_id}),
        created_at=coerce_timestamp(getattr(metadata, "creation_timestamp", None)),
        last_login_at=coerce_timestamp(getattr(metadata, "last_sign_in_timestamp", None)),
    )


def _translate(exc: Exception) -> HTTPException:
    """Map Firebase Auth errors onto HTTP responses."""
    if isinstance(exc, firebase_auth.EmailAlreadyExistsError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )
    if isinstance(exc, firebase_auth.UserNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not reach Firebase Auth"
    )


class UserService:
    """Self-service account CRUD backed by Firebase Auth.

    Read, update and delete always operate on the caller's own uid — no method
    accepts an arbitrary target uid, so the endpoints are not reachable with
    someone else's id.

    Creating a doctor additionally writes a profile document to the
    ``doctors`` collection, keyed by the new account's uid.
    """

    def __init__(
        self,
        doctor_repo: DoctorRepository = Depends(DoctorRepository),
    ) -> None:
        self._doctors = doctor_repo

    def create_patient(self, payload: PatientCreate) -> UserResponse:
        """Create a patient account. The role is fixed, never taken from input."""
        return self._create_account(
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
            role="patient",
        )

    def create_doctor(self, payload: DoctorCreate) -> UserResponse:
        """Create a doctor account and its ``doctors`` profile document."""
        record = self._create_account(
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
            role="doctor",
        )
        self._create_doctor_profile(
            record.uid,
            payload.to_profile(),
            payload.email,
            record.uid,
            display_name=record.display_name,
        )
        return record

    def _create_account(
        self, *, email: str, password: str, display_name: Optional[str], role: Role
    ) -> UserResponse:
        try:
            record = firebase_auth.create_user(
                email=email,
                password=password,
                display_name=display_name,
            )
        except (firebase_exceptions.FirebaseError, ValueError) as exc:
            raise _translate(exc) from exc

        try:
            record = firebase_auth.update_user(
                record.uid, custom_claims={"role": role}
            )
        except firebase_exceptions.FirebaseError:
            # Account exists but the claim write failed; an admin can
            # backfill it. Do not fail the signup.
            pass

        return project_user(record)

    def _create_doctor_profile(
        self,
        uid: str,
        profile: DoctorProfile,
        email: str,
        created_uid: str,
        display_name: Optional[str] = None,
    ) -> None:
        """Write the doctors document, rolling the account back on failure.

        Without the rollback a Firestore error would leave an Auth account
        whose role claim says "doctor" but which has no profile document.
        """
        try:
            self._doctors.create_for_user(
                uid, profile, email=email, display_name=display_name
            )
        except Exception as exc:
            try:
                firebase_auth.delete_user(created_uid)
            except firebase_exceptions.FirebaseError:
                pass
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not create doctor profile; account was rolled back",
            ) from exc

    def list_doctors(
        self, limit: int = 50, *, verified: Optional[bool] = None
    ) -> list[DoctorSummary]:
        """Return the doctor directory.

        Firestore documents are projected through ``DoctorSummary``, which drops
        ``email`` and ``phone``, so listing doctors does not broadcast contact
        details to every authenticated caller.
        """
        try:
            rows = self._doctors.list_all(limit=limit, verified=verified)
        except firebase_exceptions.FirebaseError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not reach Firestore",
            ) from exc

        summaries: list[DoctorSummary] = []
        for row in rows:
            uid = row.get("uid") or row.get("id")
            if not uid:
                continue
            summaries.append(DoctorSummary.model_validate({**row, "uid": uid}))
        return summaries

    def get_self(self, uid: str) -> UserResponse:
        try:
            record = firebase_auth.get_user(uid)
        except firebase_exceptions.FirebaseError as exc:
            raise _translate(exc) from exc
        return project_user(record)

    def update_self(self, uid: str, payload: UserUpdate) -> UserResponse:
        """Apply a profile-only update.

        Any subset of the three profile fields may be supplied; omitted fields
        are left untouched. A field sent as ``null`` or ``""`` is cleared, which
        maps onto Firebase's DELETE_ATTRIBUTE sentinel (a bare empty string is
        rejected by the Firebase SDK, so it is translated rather than passed
        through).

        ``email``, ``password`` and ``role`` are not in the schema, so no
        credential or role field can reach Firebase from this path.
        """
        changes: dict[str, Any] = {}

        for field in PROFILE_FIELDS:
            if field not in payload.model_fields_set:
                continue

            value = getattr(payload, field)
            if value is None or (isinstance(value, str) and not value.strip()):
                changes[field] = firebase_auth.DELETE_ATTRIBUTE
            else:
                changes[field] = value

        if not changes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update"
            )

        try:
            record = firebase_auth.update_user(uid, **changes)
        except (firebase_exceptions.FirebaseError, ValueError) as exc:
            raise _translate(exc) from exc

        self._sync_doctor_display_name(uid, record.display_name)
        return project_user(record)

    def _sync_doctor_display_name(self, uid: str, display_name: Optional[str]) -> None:
        """Mirror a display-name change onto the ``doctors`` document.

        The directory reads names from Firestore, so leaving it stale would make
        a doctor who renamed themselves still appear under the old name. Only
        ``display_name`` is mirrored; the other two profile fields are not
        stored on that document.

        Best-effort: a patient has no doctors document, and a Firestore failure
        here must not fail an otherwise successful account update.
        """
        if not display_name:
            return
        try:
            if self._doctors.get(uid) is not None:
                self._doctors.update(uid, {"display_name": display_name})
        except Exception:
            logger.warning("Could not sync display_name for doctor %s", uid)

    def delete_self(self, uid: str) -> None:
        try:
            firebase_auth.delete_user(uid)
        except firebase_exceptions.FirebaseError as exc:
            raise _translate(exc) from exc
