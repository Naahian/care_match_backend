from typing import Any, Optional

from google.api_core.exceptions import Conflict
from google.cloud import firestore

from app.repositories.base import BaseRepository


class PaymentRepository(BaseRepository):
    collection_name = "payments"

    def reserve(self, payment_id: str, data: dict[str, Any]) -> bool:
        try:
            self._collection.document(payment_id).create(data)
            return True
        except Conflict:
            return False

    def transition(self, payment_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Serialize callbacks and never downgrade a verified payment."""
        ref = self._collection.document(payment_id)

        @firestore.transactional
        def apply(transaction):
            current = ref.get(transaction=transaction).to_dict() or {}
            initializing = data.get("status") in {"pending", "initialization_failed"}
            if current.get("status") not in {"paid", "review"} and (
                not initializing or current.get("status") == "initializing"
            ):
                transaction.update(ref, data)
                current.update(data)
            return {"id": payment_id, **current}

        return apply(self._db.transaction())

    def get_by_idempotency_key(self, idempotency_key: str) -> Optional[dict[str, Any]]:
        results = self.list(limit=1, idempotency_key=idempotency_key)
        return results[0] if results else None

    def list_for_appointment(self, appointment_id: str) -> list[dict[str, Any]]:
        return self.list(limit=50, appointment_id=appointment_id)
