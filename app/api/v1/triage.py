from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser
from app.schemas.models import TriageRequest, TriageResponse
from app.services.triage import TriageService

router = APIRouter(prefix="/triage", tags=["triage"])


@router.post("", response_model=TriageResponse, status_code=status.HTTP_201_CREATED)
async def create_triage(
    payload: TriageRequest,
    user: CurrentUser,
    service: TriageService = Depends(TriageService),
) -> TriageResponse:
    return service.assess(payload, patient_id=user.uid)


@router.delete("", status_code=status.HTTP_200_OK)
async def delete_triage_assessments(
    user: CurrentUser,
    service: TriageService = Depends(TriageService),
) -> dict[str, int]:
    """Delete all of the caller's own triage assessments.

    The patient is taken from the bearer token, never from a request field, so
    one patient cannot delete another's records by passing a different id.

    Returns the number of assessments removed; 404 if there were none.
    """
    return {"deleted": service.delete_assessments(user.uid)}
