from typing import Any

from google.cloud import firestore

from app.repositories.base import BaseRepository


class UserProfileRepository(BaseRepository):
    """Private personal details, keyed by the Firebase Auth UID."""

    collection_name = "user_profiles"

    def get_many(self, uids: list[str]) -> dict[str, dict[str, Any]]:
        if not uids:
            return {}
        snapshots = self._db.get_all([self._collection.document(uid) for uid in uids])
        return {snapshot.id: snapshot.to_dict() or {} for snapshot in snapshots if snapshot.exists}

    def save(self, uid: str, changes: dict[str, Any]) -> None:
        # Merge creates the first profile and preserves omitted fields on edits.
        self._collection.document(uid).set(
            {**changes, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True
        )
