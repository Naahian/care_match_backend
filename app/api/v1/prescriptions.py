from fastapi import APIRouter, Depends

from app.api.deps import require_roles
from app.api.prescription_uploads import PRESCRIPTION_REQUEST_BODY, parse_prescription_upload
from app.schemas.prescriptions import PrescriptionResponse
from app.schemas.responses import SuccessResponse
from app.services.prescriptions import PrescriptionService

router = APIRouter(prefix="/prescriptions", tags=["prescriptions"])


@router.post("/patients/{patient_uid}", status_code=201,
             response_model=SuccessResponse[PrescriptionResponse],
             summary="Upload a prescription image for a patient",
             openapi_extra=PRESCRIPTION_REQUEST_BODY)
def create_prescription(
    patient_uid: str,
    user: require_roles("doctor"),
    data: bytes = Depends(parse_prescription_upload),
    service: PrescriptionService = Depends(PrescriptionService),
) -> SuccessResponse[PrescriptionResponse]:
    return SuccessResponse(data=service.create_prescription(user.uid, patient_uid, data),
                           status=201, msg="Created")
