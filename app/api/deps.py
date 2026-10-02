from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.core.security import AuthenticatedUser, get_current_user

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


def require_roles(*roles: str):
    """Return an annotated dependency restricting access to the given roles.

    Usage::

        async def endpoint(user: require_roles("doctor", "admin")): ...
    """
    if not roles:
        raise ValueError("require_roles() requires at least one role")

    async def _dependency(user: CurrentUser) -> AuthenticatedUser:
        if user.claims.get("role") not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(roles)}",
            )
        return user

    return Annotated[AuthenticatedUser, Depends(_dependency)]
