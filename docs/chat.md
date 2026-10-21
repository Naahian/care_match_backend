# Direct chat

Any authenticated user can start a one-to-one text conversation with another registered, enabled Firebase Auth user. All endpoints require `Authorization: Bearer <Firebase ID token>` and use the API's `status`, `msg`, and `data` response envelope.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/api/v1/chats` | Create or reopen the conversation for a user pair |
| GET | `/api/v1/chats?limit=20&cursor=...` | List the caller's conversations, most recently active first |
| GET | `/api/v1/chats/{chat_id}` | Fetch one conversation and its last message |
| POST | `/api/v1/chats/{chat_id}/messages` | Send a text message |
| GET | `/api/v1/chats/{chat_id}/messages?limit=50&cursor=...` | Fetch message history, newest first |

Create a conversation with the other user's Firebase UID:

```http
POST /api/v1/chats
Authorization: Bearer <token>
Content-Type: application/json

{"recipient_uid": "other-user-firebase-uid"}
```

The response's `data` contains `chat_id`, `participant_uids`, `created_at`, `updated_at`, and `last_message` (initially null). Both participants always get the same conversation, including simultaneous requests. Creating a conversation returns 200 whether it was created or already existed. Self-chat returns 400; a missing or disabled recipient returns 404.

Use the returned chat ID to send a message:

```http
POST /api/v1/chats/{chat_id}/messages
Authorization: Bearer <token>
Content-Type: application/json

{
  "text": "Hello! How are you feeling today?",
  "client_message_id": "ef446cd4-47d1-4ac3-b0ea-612be1a52dba"
}
```

The response's `data` contains `message_id`, `chat_id`, `sender_uid`, `text`, and `created_at`. Sender identity always comes from the authenticated user. `text` is plain text, trimmed, and must contain 1–4,000 characters; render it as text in the client. Unexpected input fields, including `sender_uid`, are rejected.

Generate a UUID in the client for each new message and reuse it when retrying the same send. Sending the same UUID/text from the same sender returns the existing message without changing conversation activity. Reusing the ID for different text or another sender returns 409. The ID is optional, but a request without it gets a new ID each time and cannot safely be retried after a network timeout.

Conversation and message lists return `data.items` and `data.next_cursor`. Pass the cursor to the same endpoint to fetch the next page; null means there are no more items. Limits are 1–100. Conversations default to 20 per page; messages default to 50. A cursor must refer to an existing document in the requested list. Invalid/missing cursor documents return 400; malformed cursor values return 422. Message order uses Firestore document IDs to break timestamp ties. Conversations can move between pages as new messages arrive; clients should merge by `chat_id`.

Clients can poll the newest message page while a conversation is open and merge results by `message_id`. Follow history cursors until reaching an already known message when catching up after an interval. Reverse newest-first results when displaying an oldest-first timeline. This implementation provides REST polling; it does not push updates over WebSockets, send notifications, or track read receipts.

Only participants can fetch a conversation, view its messages, or send messages. Other users receive 404, including admins who are not participants. Conversation membership is fixed; there is no API for adding participants. Storage failures return 503.

## Firestore setup

The existing Firebase credentials and project configuration are used; no additional `.env` settings or Python dependencies are needed.

- `chats/{chat_id}` stores participant UIDs, server timestamps, and the last message.
- `chats/{chat_id}/messages/{message_id}` stores text, sender UID, and creation time.
- A transaction writes each message and its conversation preview/activity timestamp together. Server timestamps determine ordering.
- A deterministic hash of the sorted participant pair identifies each conversation; atomic document creation prevents duplicate conversations.

Deploy the index in `firestore.indexes.json` to your Firebase project before listing chats. The included `firebase.json` points to that file. With the Firebase CLI configured:

```sh
firebase deploy --only firestore:indexes --project YOUR_PROJECT_ID
```

Alternatively, create a collection-scope composite index for `chats` with `participant_uids` in Array contains mode and `updated_at` descending in the Firebase console. Wait for the index to become ready. This follows the [Firestore index requirements](https://firebase.google.com/docs/firestore/query-data/index-overview) for the participant filter and activity ordering.

The backend uses the Firebase Admin SDK, so participant checks are enforced by these APIs. These changes do not grant client SDK access or modify Firestore security rules. Use the backend endpoints for this chat feature.

Tests cover the HTTP workflow using an in-memory repository and verify query serialization and atomic write contents with the installed Firestore SDK. They do not make live Firestore writes; deploy the index and perform a two-user smoke test against your configured project before release.
