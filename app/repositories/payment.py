from typing import Any, Optional

from app.repositories.base import BaseRepository


class PaymentRepository(BaseRepository):
    collection_name = "payments"

    def get_by_idempotency_key(self, idempotency_key: str) -> Optional[dict[str, Any]]:
        results = self.list(limit=1, idempotency_key=idempotency_key)
        return results[0] if results else None

    def list_for_appointment(self, appointment_id: str) -> list[dict[str, Any]]:
        return self.list(limit=50, appointment_id=appointment_id)
