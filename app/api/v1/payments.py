from fastapi import APIRouter, Depends, status

from app.api.deps import require_roles
from app.schemas.models import PaymentCreate, PaymentResponse
from app.services.payment import PaymentService

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
async def create_payment(
    payload: PaymentCreate,
    user: require_roles("patient"),
    service: PaymentService = Depends(PaymentService),
) -> PaymentResponse:
    return service.create_intent(payload, payer_uid=user.uid)
