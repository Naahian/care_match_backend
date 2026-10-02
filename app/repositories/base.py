from typing import Any, Optional

from fastapi import Depends
from google.cloud.firestore_v1 import Client
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.firebase import get_db


class BaseRepository:
    collection_name: str = ""

    def __init__(self, db: Client = Depends(get_db)) -> None:
        self._db = db
        # Lazy reference only; nothing is created until the first write.
        self._collection = db.collection(self.collection_name)

    def create(self, data: dict[str, Any], doc_id: Optional[str] = None) -> str:
        ref = self._collection.document(doc_id) if doc_id else self._collection.document()
        ref.set(data)  # creates the collection on first write
        return ref.id

    def get(self, doc_id: str) -> Optional[dict[str, Any]]:
        snapshot = self._collection.document(doc_id).get()
        return {"id": snapshot.id, **(snapshot.to_dict() or {})} if snapshot.exists else None

    def update(self, doc_id: str, data: dict[str, Any]) -> None:
        self._collection.document(doc_id).update(data)

    def delete(self, doc_id: str) -> None:
        self._collection.document(doc_id).delete()

    def list(self, limit: int = 50, **filters: Any) -> list[dict[str, Any]]:
        query = self._collection
        for field, value in filters.items():
            if value is None:
                continue
            query = query.where(filter=FieldFilter(field, "==", value))
        return [
            {"id": doc.id, **(doc.to_dict() or {})}
            for doc in query.limit(limit).stream()
        ]