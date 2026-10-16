from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import require_roles
from app.schemas.video import VideoTokenRequest, VideoTokenResponse
from app.schemas.responses import SuccessResponse
from app.services.video import VideoService

router = APIRouter(prefix="/video", tags=["video"])


@router.post("/token", response_model=SuccessResponse[VideoTokenResponse])
async def create_video_token(
    payload: VideoTokenRequest,
    user: require_roles("patient", "doctor", "admin"),
    service: VideoService = Depends(VideoService),
) -> SuccessResponse[VideoTokenResponse]:
    try:
        return SuccessResponse(data=service.issue_token(
            payload.appointment_id, user_id=user.uid, role=user.claims.get("role", "patient")
        ))
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
