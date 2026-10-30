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

Use Firestore client listeners for live messages and conversation previews, and REST for writes and older history. No WebSocket server or periodic polling is required. This feature does not send background push notifications or track read receipts.

Only participants can fetch a conversation, view its messages, or send messages. Other users receive 404, including admins who are not participants. Conversation membership is fixed; there is no API for adding participants. Storage failures return 503.

## Firestore setup

The existing Firebase credentials and project configuration are used; no additional `.env` settings or Python dependencies are needed.

- `chats/{chat_id}` stores participant UIDs, server timestamps, and the last message.
- `chats/{chat_id}/messages/{message_id}` stores text, sender UID, and creation time.
- A transaction writes each message and its conversation preview/activity timestamp together. Server timestamps determine ordering.
- A deterministic hash of the sorted participant pair identifies each conversation; atomic document creation prevents duplicate conversations.

Deploy the rules in `firestore.rules` and index in `firestore.indexes.json` before using client listeners. The included `firebase.json` points to both files. These rules allow only authenticated participants to read chats and their messages, deny all client writes, and deny client access to other collections. If the project already has rules managed outside this repository, merge its required access rules before deploying: a rules deployment replaces the deployed ruleset. With the Firebase CLI configured:

```sh
firebase deploy --only firestore:rules,firestore:indexes --project YOUR_PROJECT_ID
```

Alternatively, create a collection-scope composite index for `chats` with `participant_uids` in Array contains mode and `updated_at` descending in the Firebase console. Wait for the index to become ready. This follows the [Firestore index requirements](https://firebase.google.com/docs/firestore/query-data/index-overview) for the participant filter and activity ordering.

The backend uses the Firebase Admin SDK, which bypasses client security rules. API participant checks and transactional message writes continue to apply. Clients cannot create chats, change membership, or write messages directly to Firestore, including when they are participants or have an admin role.

## Client listeners (Flutter)

Add `cloud_firestore` to the client app. Initialize Firebase and sign in through Firebase Authentication in the same project as the backend before subscribing. Passing a bearer token only to the REST API does not sign the client SDK in.

Listen to the caller's conversation list:

```dart
final uid = FirebaseAuth.instance.currentUser!.uid;
final chatsSubscription = FirebaseFirestore.instance
    .collection('chats')
    .where('participant_uids', arrayContains: uid)
    .orderBy('updated_at', descending: true)
    .limit(20)
    .snapshots()
    .listen((snapshot) {
      // Replace the visible top-20 list with snapshot.docs in query order.
      // doc.id is chat_id; doc.data() contains the preview and timestamps.
    }, onError: (Object error) {
      // Surface permission/index errors and stop showing unauthorized data.
    });
```

The participant filter is required: rules do not filter unauthorized results out of an unrestricted query. The existing composite index supports this query. See [secure queries](https://firebase.google.com/docs/firestore/security/rules-query).

Listen to the newest messages in a selected conversation:

```dart
final messagesSubscription = FirebaseFirestore.instance
    .collection('chats')
    .doc(chatId)
    .collection('messages')
    .orderBy('created_at', descending: true)
    .limit(50)
    .snapshots()
    .listen((snapshot) {
      // Merge snapshot.docs into the accumulated timeline by doc.id.
      // doc.id is message_id; chatId comes from the parent path.
      // Convert Firestore Timestamp values with .toDate() when mapping.
    }, onError: (Object error) {
      // Surface the error; clear chat data when access is denied.
    });

// When leaving the screen (or before changing accounts):
await messagesSubscription.cancel();
await chatsSubscription.cancel();
```

These are integration snippets; import `firebase_auth` and `cloud_firestore` and connect the callbacks to the app's state management. The first snapshot supplies the current query results; subsequent snapshots supply updates. Direct Firestore documents are not REST response envelopes: message IDs and chat IDs come from paths, and dates are Firestore `Timestamp` values. See [realtime listeners](https://firebase.google.com/docs/firestore/query-data/listen).

Keep using POST endpoints to create chats and send messages. Merge POST responses and listener updates by `message_id`, so an outgoing message appears once even when both arrive. Keep reusing `client_message_id` on retries.

For older history, use the oldest loaded message ID as the REST `cursor`, then follow `data.next_cursor`. A limited listener is a moving window: retain messages that leave that window in the accumulated timeline; removal from its results alone does not mean a message was deleted. Display messages oldest first, using document ID as a tie-breaker for equal timestamps (reverse the backend's newest-first order).

After a disconnect or app resume, reconcile history: fetch the latest REST page and follow cursors until reaching a previously loaded message (or the end), merging every page by ID. A 50-message listener alone can miss messages between the old history and the current window. Complete catch-up against server results rather than treating a cached initial snapshot as proof there are no gaps. Cancel subscriptions and clear account-specific chat state on logout/account changes. Listeners provide foreground data updates; background notifications require a separate notification integration.

## Verification

The Python tests cover the HTTP workflow and Firestore query serialization/atomic writes without live database access. Security rules tests use only a local Firestore emulator and are skipped in normal runs. With Firebase CLI and its supported Java runtime installed:

```sh
firebase emulators:exec --only firestore --project demo-care-match \
  'DEBUG=false .venv/bin/python -m pytest tests/test_firestore_rules.py -q'
```

The tests check both participants, outsiders, anonymous access, filtered/unfiltered queries, denied client writes, and backend-only collections. After deployment, perform a two-user client smoke test: send through REST, observe both listeners, and verify a third user receives permission denied.
