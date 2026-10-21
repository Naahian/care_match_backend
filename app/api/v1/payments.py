from typing import Literal
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from app.api.deps import require_roles
from app.schemas.payments import PaymentCreate, PaymentResponse
from app.schemas.responses import SuccessResponse
from app.services.payment import PaymentService

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("", response_model=SuccessResponse[PaymentResponse], status_code=status.HTTP_201_CREATED)
def create_payment(
    payload: PaymentCreate,
    user: require_roles("patient"),
    service: PaymentService = Depends(PaymentService),
) -> SuccessResponse[PaymentResponse]:
    return SuccessResponse(data=service.create_intent(payload, payer_uid=user.uid), status=201, msg="Created")


@router.post("/sslcommerz/{event}", response_model=SuccessResponse[PaymentResponse])
async def sslcommerz_callback(
    event: Literal["success", "fail", "cancel", "ipn"],
    request: Request,
    service: PaymentService = Depends(PaymentService),
) -> SuccessResponse[PaymentResponse]:
    # Gateway callbacks are public; the service authenticates their contents.
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Expected a form-encoded SSLCOMMERZ callback")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16384:
            raise HTTPException(413, "Callback payload is too large")
    try:
        fields = parse_qsl(body.decode("utf-8"), keep_blank_values=True, max_num_fields=100)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(400, "Invalid callback form") from exc
    payload = dict(fields)
    if len(fields) != len(payload):
        raise HTTPException(400, "Duplicate callback fields")
    result = await run_in_threadpool(service.handle_callback, payload, event)
    return SuccessResponse(data=result)


@router.get("/{payment_id}", response_model=SuccessResponse[PaymentResponse])
def get_payment(
    payment_id: str,
    user: require_roles("patient"),
    service: PaymentService = Depends(PaymentService),
) -> SuccessResponse[PaymentResponse]:
    return SuccessResponse(data=service.get_payment(payment_id, user.uid))
