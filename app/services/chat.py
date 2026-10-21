from contextlib import contextmanager
from typing import Optional

from fastapi import Depends, HTTPException
from firebase_admin import auth as firebase_auth
from firebase_admin import exceptions as firebase_exceptions
from google.api_core.exceptions import GoogleAPIError

from app.repositories.chat import ChatNotFound, ChatRepository, InvalidChatCursor, MessageConflict
from app.schemas.chat import (
    ChatCreate, ChatPage, ChatResponse, MessageCreate, MessagePage, MessageResponse,
)


class ChatService:
    def __init__(self, repository: ChatRepository = Depends(ChatRepository)) -> None:
        self._repo = repository

    @staticmethod
    @contextmanager
    def _storage_errors():
        try:
            yield
        except ChatNotFound as exc:
            # Do not reveal whether another user's conversation exists.
            raise HTTPException(404, "Chat not found") from exc
        except MessageConflict as exc:
            raise HTTPException(409, "Message ID was already used for another message") from exc
        except InvalidChatCursor as exc:
            raise HTTPException(400, str(exc)) from exc
        except GoogleAPIError as exc:
            raise HTTPException(503, "Chat storage is unavailable") from exc

    def create_chat(self, payload: ChatCreate, uid: str) -> ChatResponse:
        if payload.recipient_uid == uid:
            raise HTTPException(400, "Cannot create a chat with yourself")
        try:
            recipient = firebase_auth.get_user(payload.recipient_uid)
        except firebase_auth.UserNotFoundError as exc:
            raise HTTPException(404, "Recipient not found") from exc
        except ValueError as exc:
            raise HTTPException(400, "Invalid recipient UID") from exc
        except firebase_exceptions.FirebaseError as exc:
            raise HTTPException(503, "User lookup is unavailable") from exc
        if recipient.disabled:
            raise HTTPException(404, "Recipient not found")
        with self._storage_errors():
            return ChatResponse.model_validate(self._repo.get_or_create(uid, recipient.uid))

    def get_chat(self, chat_id: str, uid: str) -> ChatResponse:
        with self._storage_errors():
            return ChatResponse.model_validate(self._repo.get_for_user(chat_id, uid))

    def list_chats(self, uid: str, limit: int, cursor: Optional[str]) -> ChatPage:
        with self._storage_errors():
            rows, next_cursor = self._repo.list_for_user(uid, limit, cursor)
            return ChatPage(items=rows, next_cursor=next_cursor)

    def list_messages(
        self, chat_id: str, uid: str, limit: int, cursor: Optional[str]
    ) -> MessagePage:
        with self._storage_errors():
            rows, next_cursor = self._repo.list_messages(chat_id, uid, limit, cursor)
            return MessagePage(items=rows, next_cursor=next_cursor)

    def send_message(self, chat_id: str, payload: MessageCreate, uid: str) -> MessageResponse:
        with self._storage_errors():
            return MessageResponse.model_validate(self._repo.send_message(
                chat_id, uid, payload.text, str(payload.client_message_id)
            ))
