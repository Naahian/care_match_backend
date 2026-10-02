from typing import Optional

import firebase_admin
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.security import AuthenticatedUser, require_admin
from app.schemas.models import AdminUserPage, AdminUserUpdate
from app.services.users import project_user

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

MAX_LIST_USERS_RESULTS = 1000


@router.get("/health")
async def admin_health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/users", response_model=AdminUserPage)
async def get_users(
    limit: int = Query(default=50, ge=1, le=MAX_LIST_USERS_RESULTS),
    page_token: Optional[str] = Query(default=None),
) -> AdminUserPage:
    """List Firebase Auth users, one page at a time.

    Pass the returned ``next_page_token`` back as ``page_token`` to page forward.
    """
    try:
        page = firebase_admin.auth.list_users(
            page_token=page_token or None, max_results=limit
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid page_token"
        ) from exc
    except firebase_admin.exceptions.FirebaseError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to reach Firebase Auth",
        ) from exc

    return AdminUserPage(
        users=[project_user(record) for record in page.users],
        next_page_token=page.next_page_token or None,
    )


@router.post("/users/{uid}/role", status_code=status.HTTP_204_NO_CONTENT)
async def set_user_role(
    uid: str,
    payload: AdminUserUpdate,
    admin: AuthenticatedUser = Depends(require_admin),
) -> None:
    try:
        firebase_admin.auth.update_user(
            uid,
            disabled=payload.disabled,
            custom_claims={"role": payload.role},
        )
    except firebase_admin.auth.UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from exc
