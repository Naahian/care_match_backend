"""Client security tests; run with firebase emulators:exec (see docs/chat.md)."""

import base64
import json
import os
import time
from uuid import uuid4

import httpx
import pytest


def _token(uid, project):
    # Unsigned Firebase tokens are accepted only by the local emulator.
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    now = int(time.time())
    claims = {
        "iss": f"https://securetoken.google.com/{project}",
        "aud": project,
        "sub": uid,
        "user_id": uid,
        "iat": now,
        "exp": now + 3600,
        "auth_time": now,
        "firebase": {"sign_in_provider": "custom", "identities": {}},
    }
    return f'{encode({"alg": "none", "typ": "JWT"})}.{encode(claims)}.'


@pytest.fixture(scope="module")
def rules_db():
    host = os.environ.get("FIRESTORE_EMULATOR_HOST")
    if not host:
        pytest.skip("Requires the Firestore emulator; see docs/chat.md")
    project = os.environ.get("GCLOUD_PROJECT", "demo-care-match")
    assert project.startswith("demo-"), "Rules tests require an isolated demo project"
    assert host.split(":")[0] in {"localhost", "127.0.0.1"}
    root = f"http://{host}/v1/projects/{project}/databases/(default)/documents"
    chat_id = f"rules-{uuid4().hex}"
    fields = {
        "participant_uids": {"arrayValue": {"values": [
            {"stringValue": "alice"}, {"stringValue": "bob"},
        ]}},
        "updated_at": {"timestampValue": "2026-01-01T00:00:00Z"},
    }
    documents = {
        f"chats/{chat_id}": fields,
        f"chats/{chat_id}/messages/message-1": {
            "sender_uid": {"stringValue": "alice"},
            "text": {"stringValue": "Hello"},
            "created_at": {"timestampValue": "2026-01-01T00:00:00Z"},
        },
        f"users/{chat_id}": {"email": {"stringValue": "private@example.com"}},
    }
    with httpx.Client(timeout=15) as client:
        def request(method, path, uid=None, **kwargs):
            headers = {"Authorization": f"Bearer {_token(uid, project)}"} if uid else {}
            return client.request(method, root + path, headers=headers, **kwargs)

        try:
            for path, data in documents.items():
                response = client.patch(
                    f"{root}/{path}", json={"fields": data},
                    headers={"Authorization": "Bearer owner"},
                )
                response.raise_for_status()
            yield request, chat_id
        finally:
            for path in reversed(documents):
                client.delete(f"{root}/{path}", headers={"Authorization": "Bearer owner"})


@pytest.mark.parametrize("uid", ["alice", "bob", "outsider", None])
@pytest.mark.parametrize("suffix", ["", "/messages/message-1", "/messages"])
def test_only_participants_can_read_chat_and_messages(rules_db, uid, suffix):
    request, chat_id = rules_db
    response = request("GET", f"/chats/{chat_id}{suffix}", uid)
    assert response.status_code == (200 if uid in {"alice", "bob"} else 403), response.text


@pytest.mark.parametrize("uid", ["alice", "bob", "outsider", None])
@pytest.mark.parametrize("filtered", [True, False])
def test_chat_list_requires_own_participant_filter(rules_db, uid, filtered):
    request, _ = rules_db
    query = {
        "from": [{"collectionId": "chats"}],
        "orderBy": [{"field": {"fieldPath": "updated_at"}, "direction": "DESCENDING"}],
        "limit": 20,
    }
    if filtered:
        query["where"] = {"fieldFilter": {
            "field": {"fieldPath": "participant_uids"},
            "op": "ARRAY_CONTAINS", "value": {"stringValue": uid or "alice"},
        }}
    response = request("POST", ":runQuery", uid, json={"structuredQuery": query})
    assert response.status_code == (200 if filtered and uid else 403), response.text


def test_cannot_query_another_users_chats(rules_db):
    request, _ = rules_db
    response = request("POST", ":runQuery", "outsider", json={"structuredQuery": {
        "from": [{"collectionId": "chats"}],
        "where": {"fieldFilter": {
            "field": {"fieldPath": "participant_uids"},
            "op": "ARRAY_CONTAINS", "value": {"stringValue": "alice"},
        }},
    }})
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("method,suffix", [
    ("PATCH", ""), ("DELETE", ""), ("PATCH", "-new"),
    ("PATCH", "/messages/message-1"), ("DELETE", "/messages/message-1"),
    ("PATCH", "/messages/new-message"),
])
@pytest.mark.parametrize("uid", ["alice", "outsider", None])
def test_client_writes_are_denied(rules_db, method, suffix, uid):
    request, chat_id = rules_db
    kwargs = {"json": {"fields": {"text": {"stringValue": "forged"}}}} if method == "PATCH" else {}
    response = request(method, f"/chats/{chat_id}{suffix}", uid, **kwargs)
    assert response.status_code == 403, response.text


def test_messages_require_existing_parent_membership(rules_db):
    request, chat_id = rules_db
    response = request("GET", f"/chats/missing-{chat_id}/messages", "alice")
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
def test_other_collections_are_backend_only(rules_db, method):
    request, chat_id = rules_db
    kwargs = {"json": {"fields": {}}} if method == "PATCH" else {}
    response = request(method, f"/users/{chat_id}", "alice", **kwargs)
    assert response.status_code == 403, response.text
