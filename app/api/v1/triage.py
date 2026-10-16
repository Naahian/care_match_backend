from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser
from app.schemas.triage import TriageDeleteResponse, TriageRequest, TriageResponse
from app.schemas.responses import SuccessResponse
from app.services.triage import TriageService

router = APIRouter(prefix="/triage", tags=["triage"])


@router.post("", response_model=SuccessResponse[TriageResponse], status_code=status.HTTP_201_CREATED)
async def create_triage(
    payload: TriageRequest,
    user: CurrentUser,
    service: TriageService = Depends(TriageService),
) -> SuccessResponse[TriageResponse]:
    return SuccessResponse(data=service.assess(payload, patient_id=user.uid), status=201, msg="Created")


@router.delete("", response_model=SuccessResponse[TriageDeleteResponse], status_code=status.HTTP_200_OK)
async def delete_triage_assessments(
    user: CurrentUser,
    service: TriageService = Depends(TriageService),
) -> SuccessResponse[TriageDeleteResponse]:
    """Delete all of the caller's own triage assessments.

    The patient is taken from the bearer token, never from a request field, so
    one patient cannot delete another's records by passing a different id.

    Returns the number of assessments removed; 404 if there were none.
    """
    return SuccessResponse(data=TriageDeleteResponse(deleted=service.delete_assessments(user.uid)))
