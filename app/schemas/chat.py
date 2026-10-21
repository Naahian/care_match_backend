from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class ChatCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    recipient_uid: str = Field(min_length=1, max_length=128)


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=4000)
    client_message_id: UUID = Field(default_factory=uuid4)


class MessageResponse(BaseModel):
    message_id: str
    chat_id: str
    sender_uid: str
    text: str
    created_at: datetime


class ChatResponse(BaseModel):
    chat_id: str
    participant_uids: list[str]
    created_at: datetime
    updated_at: datetime
    last_message: Optional[MessageResponse] = None


class ChatPage(BaseModel):
    items: list[ChatResponse]
    next_cursor: Optional[str] = None


class MessagePage(BaseModel):
    items: list[MessageResponse]
    next_cursor: Optional[str] = None
