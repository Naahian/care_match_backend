import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, require_roles
from app.schemas.account_emails import AccountEmailResponse, PasswordResetRequest
from app.schemas.doctors import DoctorCreate, DoctorSummary
from app.schemas.users import PatientCreate, UserCreateResponse, UserResponse, UserUpdate
from app.schemas.responses import SuccessResponse
from app.services.account_emails import AccountEmailService
from app.services.users import UserService

router = APIRouter(prefix="/users", tags=["users"])

MAX_LIST_DOCTORS_RESULTS = 200
logger = logging.getLogger(__name__)


async def _registration_response(
    record: UserResponse, emails: AccountEmailService
) -> UserCreateResponse:
    try:
        await emails.send_signup_verification(record.uid)
    except HTTPException as exc:
        # The account already exists. Keep signup successful and let the client
        # retry delivery separately rather than attempting duplicate registration.
        logger.warning(
            "Could not send signup verification for uid=%s: %s", record.uid, exc.detail
        )
        return UserCreateResponse(
            status=201,
            msg="Account created; verification email could not be sent",
            data=record,
            verification_email_sent=False,
        )
    return UserCreateResponse(
        status=201, msg="Account created; verification email sent",
        data=record, verification_email_sent=True,
    )


@router.post("/reset-password", response_model=AccountEmailResponse)
async def reset_password(
    payload: PasswordResetRequest,
    service: AccountEmailService = Depends(AccountEmailService),
) -> AccountEmailResponse:
    """Public: send a Firebase-hosted link where the user sets a new password.

    Return the same message for registered and unregistered email addresses.
    """
    await service.send_password_reset(payload.email)
    return AccountEmailResponse(
        msg="If an account exists for this email, a password reset email has been sent"
    )


@router.post(
    "/patient",
    response_model=UserCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a patient (public)",
)
async def create_patient(
    payload: PatientCreate,
    service: UserService = Depends(UserService),
    emails: AccountEmailService = Depends(AccountEmailService),
) -> UserCreateResponse:
    """Public self-registration.

    The role is fixed to ``patient`` server-side; the payload has no role field,
    so a client cannot ask for anything else.
    """
    return await _registration_response(service.create_patient(payload), emails)


@router.post(
    "/doctor",
    response_model=UserCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a doctor (public)",
)
async def create_doctor(
    payload: DoctorCreate,
    service: UserService = Depends(UserService),
    emails: AccountEmailService = Depends(AccountEmailService),
) -> UserCreateResponse:
    """Public clinician registration.

    The role is fixed to ``doctor`` server-side and the payload has no role
    field. The resulting profile is written unverified (``verified: false``);
    the creator cannot set that flag, so a self-registering doctor still has to
    pass review before being treated as a vetted clinician.
    """
    return await _registration_response(service.create_doctor(payload), emails)


@router.get(
    "/doctors",
    response_model=SuccessResponse[list[DoctorSummary]],
    summary="List doctors",
)
async def list_doctors(
    limit: int = Query(default=50, ge=1, le=MAX_LIST_DOCTORS_RESULTS),
    verified: Optional[bool] = Query(default=None),
    service: UserService = Depends(UserService),
) -> SuccessResponse[list[DoctorSummary]]:
    """Return the doctor directory.

    Requires any authenticated role. Each entry carries only matching-relevant
    fields; ``email`` and ``phone`` are stripped by ``DoctorSummary``.

    Pass ``verified=true`` to list only reviewed clinicians.
    """
    return SuccessResponse(data=service.list_doctors(limit=limit, verified=verified))


@router.get("/me", response_model=SuccessResponse[UserResponse])
async def get_my_user(
    user: CurrentUser,
    service: UserService = Depends(UserService),
) -> SuccessResponse[UserResponse]:
    """Return the caller's own account."""
    return SuccessResponse(data=service.get_self(user.uid))


@router.patch("/me", response_model=SuccessResponse[UserResponse])
async def update_my_user(
    payload: UserUpdate,
    user: CurrentUser,
    service: UserService = Depends(UserService),
) -> SuccessResponse[UserResponse]:
    """Update the caller's own profile: display_name, phone_number, photo_url.

    ``email``, ``password`` and ``role`` are rejected by the schema (422).
    """
    return SuccessResponse(data=service.update_self(user.uid, payload))


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_user(
    user: CurrentUser,
    service: UserService = Depends(UserService),
) -> None:
    """Delete the caller's own account.

    Firebase revokes the caller's tokens as part of deletion. Only the caller's
    own uid is ever passed, so no other account can be targeted.
    """
    service.delete_self(user.uid)
