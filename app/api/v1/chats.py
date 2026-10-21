from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import CurrentUser
from app.schemas.chat import (
    ChatCreate, ChatPage, ChatResponse, MessageCreate, MessagePage, MessageResponse,
)
from app.schemas.responses import SuccessResponse
from app.services.chat import ChatService

router = APIRouter(prefix="/chats", tags=["chats"])
ChatId = Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")]
Cursor = Annotated[Optional[str], Query(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


@router.post("", response_model=SuccessResponse[ChatResponse])
def create_chat(
    payload: ChatCreate,
    user: CurrentUser,
    service: ChatService = Depends(ChatService),
) -> SuccessResponse[ChatResponse]:
    """Create or reopen a direct conversation with a registered user."""
    return SuccessResponse(data=service.create_chat(payload, user.uid))


@router.get("", response_model=SuccessResponse[ChatPage])
def list_chats(
    user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: Cursor = None,
    service: ChatService = Depends(ChatService),
) -> SuccessResponse[ChatPage]:
    return SuccessResponse(data=service.list_chats(user.uid, limit, cursor))


@router.get("/{chat_id}", response_model=SuccessResponse[ChatResponse])
def get_chat(
    chat_id: ChatId,
    user: CurrentUser,
    service: ChatService = Depends(ChatService),
) -> SuccessResponse[ChatResponse]:
    return SuccessResponse(data=service.get_chat(chat_id, user.uid))


@router.get("/{chat_id}/messages", response_model=SuccessResponse[MessagePage])
def list_messages(
    chat_id: ChatId,
    user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: Cursor = None,
    service: ChatService = Depends(ChatService),
) -> SuccessResponse[MessagePage]:
    return SuccessResponse(data=service.list_messages(chat_id, user.uid, limit, cursor))


@router.post("/{chat_id}/messages", response_model=SuccessResponse[MessageResponse])
def send_message(
    chat_id: ChatId,
    payload: MessageCreate,
    user: CurrentUser,
    service: ChatService = Depends(ChatService),
) -> SuccessResponse[MessageResponse]:
    """Retry with the same client_message_id and text to avoid duplicate messages."""
    return SuccessResponse(data=service.send_message(chat_id, payload, user.uid))
