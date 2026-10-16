from fastapi import APIRouter, Depends, status

from app.api.deps import require_roles
from app.schemas.payments import PaymentCreate, PaymentResponse
from app.schemas.responses import SuccessResponse
from app.services.payment import PaymentService

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("", response_model=SuccessResponse[PaymentResponse], status_code=status.HTTP_201_CREATED)
async def create_payment(
    payload: PaymentCreate,
    user: require_roles("patient"),
    service: PaymentService = Depends(PaymentService),
) -> SuccessResponse[PaymentResponse]:
    return SuccessResponse(data=service.create_intent(payload, payer_uid=user.uid), status=201, msg="Created")
