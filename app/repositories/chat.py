import hashlib
import json
from typing import Any, Optional

from google.api_core.exceptions import Conflict
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.repositories.base import BaseRepository


class ChatNotFound(Exception):
    pass


class MessageConflict(Exception):
    pass


class InvalidChatCursor(ValueError):
    pass


class ChatRepository(BaseRepository):
    collection_name = "chats"

    @staticmethod
    def _chat(snapshot) -> dict[str, Any]:
        return {**snapshot.to_dict(), "chat_id": snapshot.id}

    @staticmethod
    def _message(snapshot, chat_id: str) -> dict[str, Any]:
        return {**snapshot.to_dict(), "message_id": snapshot.id, "chat_id": chat_id}

    @staticmethod
    def _require_member(snapshot, uid: str) -> None:
        if not snapshot.exists or uid not in snapshot.to_dict().get("participant_uids", []):
            raise ChatNotFound()

    def get_or_create(self, uid: str, recipient_uid: str) -> dict[str, Any]:
        participants = sorted([uid, recipient_uid])
        chat_id = hashlib.sha256(json.dumps(participants).encode()).hexdigest()
        ref = self._collection.document(chat_id)
        try:
            ref.create({
                "participant_uids": participants,
                "created_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
                "last_message": None,
            })
        except Conflict:
            # A unique pair always shares one conversation across callers/workers.
            pass
        return self._chat(ref.get())

    def get_for_user(self, chat_id: str, uid: str) -> dict[str, Any]:
        snapshot = self._collection.document(chat_id).get()
        self._require_member(snapshot, uid)
        return self._chat(snapshot)

    def list_for_user(
        self, uid: str, limit: int, cursor: Optional[str]
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
        query = self._collection.where(
            filter=FieldFilter("participant_uids", "array_contains", uid)
        ).order_by("updated_at", direction=firestore.Query.DESCENDING)
        if cursor:
            snapshot = self._collection.document(cursor).get()
            if not snapshot.exists or uid not in snapshot.to_dict().get("participant_uids", []):
                raise InvalidChatCursor("Invalid chat cursor")
            query = query.start_after(snapshot)
        docs = list(query.limit(limit + 1).stream())
        next_cursor = docs[limit - 1].id if len(docs) > limit else None
        return [self._chat(doc) for doc in docs[:limit]], next_cursor

    def list_messages(
        self, chat_id: str, uid: str, limit: int, cursor: Optional[str]
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
        self.get_for_user(chat_id, uid)
        messages = self._collection.document(chat_id).collection("messages")
        query = messages.order_by("created_at", direction=firestore.Query.DESCENDING)
        if cursor:
            snapshot = messages.document(cursor).get()
            if not snapshot.exists:
                raise InvalidChatCursor("Invalid message cursor")
            query = query.start_after(snapshot)
        docs = list(query.limit(limit + 1).stream())
        next_cursor = docs[limit - 1].id if len(docs) > limit else None
        return [self._message(doc, chat_id) for doc in docs[:limit]], next_cursor

    def send_message(
        self, chat_id: str, uid: str, text: str, message_id: str
    ) -> dict[str, Any]:
        chat_ref = self._collection.document(chat_id)
        message_ref = chat_ref.collection("messages").document(message_id)

        @firestore.transactional
        def send(transaction):
            chat = chat_ref.get(transaction=transaction)
            self._require_member(chat, uid)
            existing = message_ref.get(transaction=transaction)
            if existing.exists:
                message = existing.to_dict()
                if message["sender_uid"] != uid or message["text"] != text:
                    raise MessageConflict()
                return
            message = {
                "sender_uid": uid,
                "text": text,
                "created_at": firestore.SERVER_TIMESTAMP,
            }
            transaction.create(message_ref, message)
            transaction.update(chat_ref, {
                "updated_at": firestore.SERVER_TIMESTAMP,
                "last_message": {**message, "message_id": message_id, "chat_id": chat_id},
            })

        send(self._db.transaction())
        return self._message(message_ref.get(), chat_id)
