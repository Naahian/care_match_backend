from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, require_roles
from app.schemas.models import (
    DoctorCreate,
    DoctorSummary,
    PatientCreate,
    UserResponse,
    UserUpdate,
)
from app.services.users import UserService

router = APIRouter(prefix="/users", tags=["users"])

MAX_LIST_DOCTORS_RESULTS = 200


@router.post(
    "/patient",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a patient (public)",
)
async def create_patient(
    payload: PatientCreate,
    service: UserService = Depends(UserService),
) -> UserResponse:
    """Public self-registration.

    The role is fixed to ``patient`` server-side; the payload has no role field,
    so a client cannot ask for anything else.
    """
    return service.create_patient(payload)


@router.post(
    "/doctor",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a doctor (public)",
)
async def create_doctor(
    payload: DoctorCreate,
    service: UserService = Depends(UserService),
) -> UserResponse:
    """Public clinician registration.

    The role is fixed to ``doctor`` server-side and the payload has no role
    field. The resulting profile is written unverified (``verified: false``);
    the creator cannot set that flag, so a self-registering doctor still has to
    pass review before being treated as a vetted clinician.
    """
    return service.create_doctor(payload)


@router.get(
    "/doctors",
    response_model=list[DoctorSummary],
    summary="List doctors",
)
async def list_doctors(
    user: require_roles("patient", "doctor", "admin"),
    limit: int = Query(default=50, ge=1, le=MAX_LIST_DOCTORS_RESULTS),
    verified: Optional[bool] = Query(default=None),
    service: UserService = Depends(UserService),
) -> list[DoctorSummary]:
    """Return the doctor directory.

    Requires any authenticated role. Each entry carries only matching-relevant
    fields; ``email`` and ``phone`` are stripped by ``DoctorSummary``.

    Pass ``verified=true`` to list only reviewed clinicians.
    """
    return service.list_doctors(limit=limit, verified=verified)


@router.get("/me", response_model=UserResponse)
async def get_my_user(
    user: CurrentUser,
    service: UserService = Depends(UserService),
) -> UserResponse:
    """Return the caller's own account."""
    return service.get_self(user.uid)


@router.patch("/me", response_model=UserResponse)
async def update_my_user(
    payload: UserUpdate,
    user: CurrentUser,
    service: UserService = Depends(UserService),
) -> UserResponse:
    """Update the caller's own profile: display_name, phone_number, photo_url.

    ``email``, ``password`` and ``role`` are rejected by the schema (422).
    """
    return service.update_self(user.uid, payload)


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
