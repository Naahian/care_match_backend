import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from firebase_admin import auth as firebase_auth
from google.api_core.exceptions import ServiceUnavailable

from app.core.security import AuthenticatedUser, get_current_user
from app.main import create_app
from app.repositories.chat import ChatNotFound, InvalidChatCursor, MessageConflict
from app.services.chat import ChatService


class MemoryChats:
    def __init__(self):
        self.chats = {}
        self.messages = {}

    def get_or_create(self, uid, recipient_uid):
        participants = sorted([uid, recipient_uid])
        chat_id = hashlib.sha256(json.dumps(participants).encode()).hexdigest()
        if chat_id not in self.chats:
            now = datetime.now(timezone.utc)
            self.chats[chat_id] = dict(chat_id=chat_id, participant_uids=participants,
                                      created_at=now, updated_at=now, last_message=None)
            self.messages[chat_id] = {}
        return self.chats[chat_id].copy()

    def get_for_user(self, chat_id, uid):
        chat = self.chats.get(chat_id)
        if not chat or uid not in chat["participant_uids"]:
            raise ChatNotFound()
        return chat.copy()

    @staticmethod
    def page(rows, limit, cursor, id_field):
        if cursor:
            indices = [i for i, row in enumerate(rows) if row[id_field] == cursor]
            if not indices:
                raise InvalidChatCursor("Invalid cursor")
            rows = rows[indices[0] + 1:]
        return rows[:limit], rows[limit - 1][id_field] if len(rows) > limit else None

    def list_for_user(self, uid, limit, cursor):
        rows = [row for row in self.chats.values() if uid in row["participant_uids"]]
        rows.sort(key=lambda row: (row["updated_at"], row["chat_id"]), reverse=True)
        return self.page(rows, limit, cursor, "chat_id")

    def list_messages(self, chat_id, uid, limit, cursor):
        self.get_for_user(chat_id, uid)
        rows = sorted(self.messages[chat_id].values(),
                      key=lambda row: (row["created_at"], row["message_id"]), reverse=True)
        return self.page(rows, limit, cursor, "message_id")

    def send_message(self, chat_id, uid, text, message_id):
        self.get_for_user(chat_id, uid)
        existing = self.messages[chat_id].get(message_id)
        if existing:
            if existing["sender_uid"] != uid or existing["text"] != text:
                raise MessageConflict()
            return existing.copy()
        now = datetime.now(timezone.utc)
        message = dict(message_id=message_id, chat_id=chat_id,
                       sender_uid=uid, text=text, created_at=now)
        self.messages[chat_id][message_id] = message
        self.chats[chat_id].update(updated_at=now, last_message=message)
        return message.copy()


@pytest.fixture
def chat_api(monkeypatch):
    repo = MemoryChats()
    app = create_app()
    current = {"uid": "alice"}
    users = {"alice", "bob", "carol"}

    def lookup(uid):
        if uid not in users:
            raise firebase_auth.UserNotFoundError("Missing", None)
        return SimpleNamespace(uid=uid, disabled=False)

    monkeypatch.setattr(firebase_auth, "get_user", lookup)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(uid=current["uid"])
    app.dependency_overrides[ChatService] = lambda: ChatService(repo)
    return TestClient(app), repo, current, app


def create_chat(client, recipient="bob"):
    response = client.post("/api/v1/chats", json={"recipient_uid": recipient})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_users_can_open_same_chat_and_exchange_messages(chat_api):
    client, repo, current, _ = chat_api
    chat = create_chat(client)
    assert chat["participant_uids"] == ["alice", "bob"]
    assert create_chat(client)["chat_id"] == chat["chat_id"]
    current["uid"] = "bob"
    assert create_chat(client, "alice")["chat_id"] == chat["chat_id"]
    assert len(repo.chats) == 1
    path = f'/api/v1/chats/{chat["chat_id"]}'
    response = client.post(path + "/messages", json={"text": "Hello Alice"})
    assert response.status_code == 200
    first = response.json()["data"]
    assert first["sender_uid"] == "bob"
    current["uid"] = "alice"
    response = client.post(path + "/messages", json={"text": "Hi Bob"})
    second = response.json()["data"]
    assert second["sender_uid"] == "alice"
    rows = client.get(path + "/messages").json()["data"]["items"]
    assert [message["text"] for message in rows] == ["Hi Bob", "Hello Alice"]
    assert client.get(path).json()["data"]["last_message"] == second
    assert client.get("/api/v1/chats").json()["data"]["items"][0]["last_message"] == second


def test_message_retry_does_not_duplicate_or_update_chat(chat_api):
    client, repo, current, _ = chat_api
    chat_id = create_chat(client)["chat_id"]
    path = f"/api/v1/chats/{chat_id}/messages"
    body = {"text": "Hello", "client_message_id": str(uuid4())}
    first = client.post(path, json=body).json()
    updated = repo.chats[chat_id]["updated_at"]
    assert client.post(path, json=body).json() == first
    assert repo.chats[chat_id]["updated_at"] == updated
    assert len(repo.messages[chat_id]) == 1
    assert client.post(path, json={**body, "text": "Changed"}).status_code == 409
    current["uid"] = "bob"
    assert client.post(path, json=body).status_code == 409


def test_non_participant_cannot_read_or_send(chat_api):
    client, _, current, _ = chat_api
    path = f'/api/v1/chats/{create_chat(client)["chat_id"]}'
    current["uid"] = "carol"
    assert client.get(path).status_code == 404
    assert client.get(path + "/messages").status_code == 404
    assert client.post(path + "/messages", json={"text": "intrusion"}).status_code == 404
    assert client.get("/api/v1/chats").json()["data"]["items"] == []


@pytest.mark.parametrize("method,suffix,body", [
    ("post", "", {"recipient_uid": "bob"}), ("get", "", None),
    ("get", "/" + "a" * 64, None), ("get", "/" + "a" * 64 + "/messages", None),
    ("post", "/" + "a" * 64 + "/messages", {"text": "Hi"}),
])
def test_all_chat_routes_require_authentication(chat_api, method, suffix, body):
    client, _, _, app = chat_api
    del app.dependency_overrides[get_current_user]
    response = client.request(method, "/api/v1/chats" + suffix, json=body)
    assert response.status_code == 401


def test_unknown_disabled_and_self_recipient(chat_api, monkeypatch):
    client, _, _, _ = chat_api
    assert client.post("/api/v1/chats", json={"recipient_uid": "alice"}).status_code == 400
    assert client.post("/api/v1/chats", json={"recipient_uid": "missing"}).status_code == 404
    monkeypatch.setattr(firebase_auth, "get_user", lambda uid: SimpleNamespace(uid=uid, disabled=True))
    assert client.post("/api/v1/chats", json={"recipient_uid": "bob"}).status_code == 404


@pytest.mark.parametrize("body", [
    {}, {"text": ""}, {"text": "  \n "}, {"text": "x" * 4001},
    {"text": "Hi", "sender_uid": "bob"}, {"text": "Hi", "client_message_id": "invalid"},
])
def test_message_validation(chat_api, body):
    client, repo, _, _ = chat_api
    chat_id = create_chat(client)["chat_id"]
    assert client.post(f"/api/v1/chats/{chat_id}/messages", json=body).status_code == 422
    assert not repo.messages[chat_id]


def test_message_pagination_and_new_messages(chat_api):
    client, _, _, _ = chat_api
    path = f'/api/v1/chats/{create_chat(client)["chat_id"]}/messages'
    for text in ("one", "two", "three"):
        assert client.post(path, json={"text": text}).status_code == 200
    page = client.get(path, params={"limit": 2}).json()["data"]
    assert [row["text"] for row in page["items"]] == ["three", "two"]
    assert page["next_cursor"] == page["items"][-1]["message_id"]
    client.post(path, json={"text": "four"})
    last = client.get(path, params={"limit": 2, "cursor": page["next_cursor"]}).json()["data"]
    assert [row["text"] for row in last["items"]] == ["one"]
    assert last["next_cursor"] is None


def test_chat_pagination_and_cursor_validation(chat_api):
    client, _, _, _ = chat_api
    create_chat(client, "bob")
    create_chat(client, "carol")
    page = client.get("/api/v1/chats", params={"limit": 1}).json()["data"]
    last = client.get("/api/v1/chats", params={"limit": 1, "cursor": page["next_cursor"]}).json()["data"]
    assert len(last["items"]) == 1
    assert last["items"][0]["chat_id"] != page["items"][0]["chat_id"]
    assert last["next_cursor"] is None
    assert client.get("/api/v1/chats", params={"cursor": "unknown"}).status_code == 400
    assert client.get("/api/v1/chats", params={"cursor": "path/injection"}).status_code == 422
    assert client.get("/api/v1/chats", params={"limit": 101}).status_code == 422
    assert client.get("/api/v1/chats/not-a-chat-id").status_code == 422


def test_storage_error_is_service_unavailable(chat_api, monkeypatch):
    client, repo, _, _ = chat_api

    def unavailable(*args):
        raise ServiceUnavailable("backend credentials and internal details")

    monkeypatch.setattr(repo, "list_for_user", unavailable)
    response = client.get("/api/v1/chats")
    assert response.status_code == 503
    assert "credentials" not in response.text
