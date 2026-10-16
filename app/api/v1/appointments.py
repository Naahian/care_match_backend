from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, require_roles
from app.schemas.appointments import (
    AppointmentCreate,
    AppointmentResponse,
    AppointmentStatus,
)
from app.schemas.responses import SuccessResponse
from app.services.booking import BookingService

router = APIRouter(prefix="/appointments", tags=["appointments"])


@router.post("", response_model=SuccessResponse[AppointmentResponse], status_code=status.HTTP_201_CREATED)
async def create_appointment(
    payload: AppointmentCreate,
    user: CurrentUser,
    service: BookingService = Depends(BookingService),
) -> SuccessResponse[AppointmentResponse]:
    try:
        return SuccessResponse(data=service.create(payload, requested_by_uid=user.uid), status=201, msg="Created")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("", response_model=SuccessResponse[list[AppointmentResponse]])
async def list_appointments(
    user: CurrentUser,
    role: str = Query(default="patient", pattern="^(patient|doctor)$"),
    service: BookingService = Depends(BookingService),
) -> SuccessResponse[list[AppointmentResponse]]:
    return SuccessResponse(data=[
        AppointmentResponse.model_validate(record, from_attributes=True)
        for record in service.list_for(user.uid, role)
    ])


@router.patch("/{appointment_id}/status", response_model=SuccessResponse[AppointmentResponse])
async def update_appointment_status(
    appointment_id: str,
    user: require_roles("doctor", "admin"),
    new_status: AppointmentStatus = Query(...),
    service: BookingService = Depends(BookingService),
) -> SuccessResponse[AppointmentResponse]:
    try:
        record = service.transition(
            appointment_id, new_status, actor_uid=user.uid, actor_role="doctor"
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return SuccessResponse(data=AppointmentResponse.model_validate(record, from_attributes=True))
