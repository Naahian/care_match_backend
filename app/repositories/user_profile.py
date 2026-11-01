from typing import Any

from google.cloud import firestore

from app.repositories.base import BaseRepository


class UserProfileRepository(BaseRepository):
    """Private personal details, keyed by the Firebase Auth UID."""

    collection_name = "user_profiles"

    def save(self, uid: str, changes: dict[str, Any]) -> None:
        # Merge creates the first profile and preserves omitted fields on edits.
        self._collection.document(uid).set(
            {**changes, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True
        )
