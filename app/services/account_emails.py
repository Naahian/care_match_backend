"""Send Firebase Authentication template emails through Google's delivery service."""

import httpx
from fastapi import HTTPException, status
from fastapi.concurrency import run_in_threadpool
from firebase_admin import auth as firebase_auth
from firebase_admin import exceptions as firebase_exceptions

from app.core.config import settings


class AccountEmailService:
    async def send_signup_verification(self, uid: str) -> None:
        """Send the Email address verification template to the new uid's email."""
        self._check_configuration()
        try:
            custom_token = await run_in_threadpool(firebase_auth.create_custom_token, uid)
            if isinstance(custom_token, bytes):
                custom_token = custom_token.decode("utf-8")
        except (firebase_exceptions.FirebaseError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not prepare Firebase verification email",
            ) from exc
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken",
                    params={"key": settings.firebase_api_key},
                    json={"token": custom_token, "returnSecureToken": True},
                )
            response.raise_for_status()
            token = response.json()["idToken"]
            if not isinstance(token, str) or not token:
                raise ValueError("Missing ID token")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not authenticate the new account for Firebase email delivery",
            ) from exc
        # VERIFY_EMAIL selects Firebase Authentication's configured Email
        # address verification template and Google's default email sender.
        await self._send({"requestType": "VERIFY_EMAIL", "idToken": token})

    async def send_password_reset(self, email: str) -> None:
        await self._send({"requestType": "PASSWORD_RESET", "email": email})

    def _check_configuration(self) -> None:
        if not settings.firebase_api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="FIREBASE_API_KEY must be configured for Firebase email delivery",
            )

    async def _send(self, payload: dict[str, str]) -> None:
        self._check_configuration()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode",
                    params={"key": settings.firebase_api_key},
                    json=payload,
                )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not reach Firebase email service",
            ) from exc

        if response.is_success:
            return

        try:
            error = response.json()["error"]["message"].split(" : ", 1)[0]
        except (ValueError, KeyError, TypeError, AttributeError):
            error = None

        # Keep the public reset response independent of account existence.
        if payload["requestType"] == "PASSWORD_RESET" and error in {
            "EMAIL_NOT_FOUND", "USER_NOT_FOUND", "USER_DISABLED"
        }:
            return
        if error in {"INVALID_ID_TOKEN", "TOKEN_EXPIRED", "USER_NOT_FOUND", "USER_DISABLED"}:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired Firebase ID token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if response.status_code == 429 or error in {"TOO_MANY_ATTEMPTS_TRY_LATER", "QUOTA_EXCEEDED"}:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many email requests; try again later",
            )
        if error in {"INVALID_EMAIL", "MISSING_EMAIL"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid email address"
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Firebase could not send the account email",
        )
