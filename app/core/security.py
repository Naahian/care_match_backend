import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth

from app.core.config import settings

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    uid: str
    email: Optional[str] = None
    email_verified: bool = False
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def role(self) -> Optional[str]:
        return self.claims.get("role")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None:
        raise _unauthorized("Missing bearer token")

    try:
        decoded = await run_in_threadpool(
            firebase_auth.verify_id_token, credentials.credentials, check_revoked=True
        )
    except firebase_auth.RevokedIdTokenError as exc:
        raise _unauthorized("Token has been revoked") from exc
    except firebase_auth.ExpiredIdTokenError as exc:
        raise _unauthorized("Token expired") from exc
    except firebase_auth.UserDisabledError as exc:
        raise _unauthorized("User is disabled") from exc
    except (firebase_auth.InvalidIdTokenError, ValueError) as exc:
        logger.warning("Invalid token: %r", exc)
        raise _unauthorized("Invalid token") from exc
    except firebase_auth.CertificateFetchError as exc:
        logger.error("Could not fetch Firebase certificates: %r", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service unavailable",
        ) from exc

    if settings.environment == "production" and not decoded.get("email_verified"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified",
        )

    return AuthenticatedUser(
        uid=decoded["uid"],
        email=decoded.get("email"),
        email_verified=bool(decoded.get("email_verified")),
        claims=decoded,
    )


async def require_admin(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    try:
        record = await run_in_threadpool(firebase_auth.get_user, user.uid)
    except firebase_auth.UserNotFoundError as exc:
        raise _unauthorized("User not found") from exc

    live_claims = record.custom_claims or {}
    logger.debug("uid=%s live role=%s", user.uid, live_claims.get("role"))

    if live_claims.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return user


