from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from google.api_core.exceptions import Conflict
from google.auth.credentials import AnonymousCredentials
from google.cloud import firestore
from google.cloud.firestore_v1 import DocumentReference, DocumentSnapshot, Query

from app.repositories.chat import ChatNotFound, ChatRepository, MessageConflict

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
CHAT_ID = "a" * 64
MESSAGE_ID = "00000000-0000-4000-8000-000000000001"


def snapshot(ref, data):
    return DocumentSnapshot(ref, data, data is not None, NOW, NOW, NOW)


@pytest.fixture
def repository():
    db = firestore.Client(project="demo-chat-test", credentials=AnonymousCredentials())
    return ChatRepository(db=db)


def test_pair_creation_is_unique_and_uses_server_timestamps(repository):
    def get(ref, **kwargs):
        return snapshot(ref, {"participant_uids": ["alice", "bob"], "created_at": NOW,
                              "updated_at": NOW, "last_message": None})

    with patch.object(DocumentReference, "create", autospec=True) as create:
        with patch.object(DocumentReference, "get", get):
            first = repository.get_or_create("alice", "bob")
            create.side_effect = Conflict("Already exists")
            second = repository.get_or_create("bob", "alice")
    assert first == second
    assert create.call_args_list[0].args[0].path == create.call_args_list[1].args[0].path
    data = create.call_args_list[0].args[1]
    assert data["participant_uids"] == ["alice", "bob"]
    assert data["created_at"] is firestore.SERVER_TIMESTAMP
    assert data["updated_at"] is firestore.SERVER_TIMESTAMP


@pytest.mark.parametrize("operation", ["get", "list_messages", "send_message"])
@pytest.mark.parametrize("chat", [None, {"participant_uids": ["bob", "carol"]}])
def test_membership_enforced_in_repository(repository, monkeypatch, operation, chat):
    monkeypatch.setattr(firestore, "transactional", lambda function: function)
    with patch.object(DocumentReference, "get", lambda ref, **kwargs: snapshot(ref, chat)):
        with pytest.raises(ChatNotFound):
            if operation == "get":
                repository.get_for_user(CHAT_ID, "alice")
            elif operation == "list_messages":
                repository.list_messages(CHAT_ID, "alice", 20, None)
            else:
                repository.send_message(CHAT_ID, "alice", "Hello", MESSAGE_ID)


def test_send_serializes_atomic_message_and_preview_with_server_timestamps(repository, monkeypatch):
    monkeypatch.setattr(firestore, "transactional", lambda function: function)
    transaction = repository._db.transaction()
    monkeypatch.setattr(repository._db, "transaction", lambda: transaction)

    def get(ref, **kwargs):
        if ref.path == f"chats/{CHAT_ID}":
            assert kwargs.get("transaction") is transaction
            return snapshot(ref, {"participant_uids": ["alice", "bob"]})
        if kwargs.get("transaction") is transaction:
            return snapshot(ref, None)
        return snapshot(ref, {"sender_uid": "alice", "text": "Hello", "created_at": NOW})

    with patch.object(DocumentReference, "get", get):
        message = repository.send_message(CHAT_ID, "alice", "Hello", MESSAGE_ID)
    assert message["sender_uid"] == "alice"
    assert message["message_id"] == MESSAGE_ID
    writes = transaction._write_pbs
    assert len(writes) == 2
    assert writes[0].update.name.endswith(f"chats/{CHAT_ID}/messages/{MESSAGE_ID}")
    assert writes[0].current_document.exists is False
    assert [transform.field_path for transform in writes[0].update_transforms] == ["created_at"]
    assert writes[1].update.name.endswith(f"chats/{CHAT_ID}")
    assert {transform.field_path for transform in writes[1].update_transforms} == {
        "updated_at", "last_message.created_at",
    }
    assert writes[1].update.fields["last_message"].map_value.fields["text"].string_value == "Hello"


@pytest.mark.parametrize("sender,text,conflict", [
    ("alice", "Hello", False), ("alice", "Different", True), ("bob", "Hello", True),
])
def test_transaction_retries_existing_message_without_writes(repository, monkeypatch, sender, text, conflict):
    monkeypatch.setattr(firestore, "transactional", lambda function: function)
    transaction = repository._db.transaction()
    monkeypatch.setattr(repository._db, "transaction", lambda: transaction)

    def get(ref, **kwargs):
        if ref.path == f"chats/{CHAT_ID}":
            return snapshot(ref, {"participant_uids": ["alice", "bob"]})
        return snapshot(ref, {"sender_uid": sender, "text": text, "created_at": NOW})

    with patch.object(DocumentReference, "get", get):
        if conflict:
            with pytest.raises(MessageConflict):
                repository.send_message(CHAT_ID, "alice", "Hello", MESSAGE_ID)
        else:
            assert repository.send_message(CHAT_ID, "alice", "Hello", MESSAGE_ID)["text"] == "Hello"
    assert transaction._write_pbs == []


def test_chat_query_filters_members_orders_and_limits_with_cursor(repository):
    collection = repository._collection
    cursor_ref = collection.document("b" * 64)
    cursor = snapshot(cursor_ref, {"participant_uids": ["alice", "bob"], "updated_at": NOW})
    docs = [snapshot(collection.document(str(index)), {
        "participant_uids": ["alice", "bob"], "updated_at": NOW,
    }) for index in range(3)]
    captured = []

    def stream(query, **kwargs):
        captured.append(query._to_protobuf())
        return iter(docs)

    with patch.object(DocumentReference, "get", return_value=cursor):
        with patch.object(Query, "stream", stream):
            rows, next_cursor = repository.list_for_user("alice", 2, cursor.id)
    assert len(rows) == 2
    assert next_cursor == docs[1].id
    query = captured[0]
    assert query.where.field_filter.field.field_path == "participant_uids"
    assert query.where.field_filter.value.string_value == "alice"
    assert query.order_by[0].field.field_path == "updated_at"
    assert query.limit == 3
    # Snapshot cursors include a document reference tie-breaker, even at equal times.
    assert len(query.start_at.values) == 2
    assert query.start_at.values[-1].reference_value.endswith(cursor_ref.path)


def test_chat_cursor_must_belong_to_user(repository):
    with patch.object(DocumentReference, "get", lambda ref, **kw: snapshot(
        ref, {"participant_uids": ["bob", "carol"], "updated_at": NOW}
    )):
        with pytest.raises(ValueError, match="Invalid chat cursor"):
            repository.list_for_user("alice", 20, "b" * 64)


def test_messages_paginate_only_within_authorized_chat(repository):
    refs = repository._collection.document(CHAT_ID).collection("messages")
    cursor = snapshot(refs.document(MESSAGE_ID), {"created_at": NOW, "text": "old"})
    captured = []

    def get(ref, **kwargs):
        if ref.path == f"chats/{CHAT_ID}":
            return snapshot(ref, {"participant_uids": ["alice", "bob"]})
        assert ref.path == f"chats/{CHAT_ID}/messages/{MESSAGE_ID}"
        return cursor

    def stream(query, **kwargs):
        captured.append(query._to_protobuf())
        return iter([])

    with patch.object(DocumentReference, "get", get):
        with patch.object(Query, "stream", stream):
            assert repository.list_messages(CHAT_ID, "alice", 10, MESSAGE_ID) == ([], None)
    assert captured[0].from_[0].collection_id == "messages"
    assert captured[0].order_by[0].field.field_path == "created_at"
    assert captured[0].limit == 11
    assert len(captured[0].start_at.values) == 2
