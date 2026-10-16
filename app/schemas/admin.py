from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.common import Role
from app.schemas.users import UserResponse


class AdminUserPage(BaseModel):
    users: list[UserResponse] = Field(default_factory=list)
    next_page_token: Optional[str] = None


class AdminUserUpdate(BaseModel):
    role: Role
    disabled: bool = False
