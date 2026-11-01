from typing import Any, Optional
from datetime import datetime, timezone

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.appointment_rules import ACTIVE_STATUSES, is_expired
from app.core.config import settings

from app.repositories.base import BaseRepository


class AppointmentRepository(BaseRepository):
    collection_name = "appointments"

    @staticmethod
    def _with_id(record: dict[str, Any]) -> dict[str, Any]:
        record = dict(record)
        record["appointment_id"] = record.pop("id")
        return record

    def list_for_user(self, user_id, role, limit=50):
        field = "doctor_id" if role == "doctor" else "patient_id"
        return [self._with_id(self._expire_record(r)) for r in self.list(limit=limit, **{field: user_id})]

    def get(self, doc_id: str) -> Optional[dict[str, Any]]:
        record = super().get(doc_id)
        return self._expire_record(record) if record else None

    def _expire_record(self, record: dict[str, Any]) -> dict[str, Any]:
        if is_expired(record):
            return self._change_status(record["id"]) or record
        return record

    def _change_status(
        self, appointment_id: str, new_status: str | None = None,
        actor_uid: str | None = None, actor_role: str = "doctor",
    ) -> Optional[dict[str, Any]]:
        ref = self._collection.document(appointment_id)

        @firestore.transactional
        def change(transaction):
            snapshot = ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            record = {"id": snapshot.id, **snapshot.to_dict()}
            if new_status is not None and (
                actor_role not in {"doctor", "admin"}
                or (actor_role != "admin" and record.get("doctor_id") != actor_uid)
            ):
                raise PermissionError("Appointment not found for this user")
            expired = is_expired(record)
            target = "cancelled" if expired else new_status
            if target is None or target == record.get("status"):
                return record
            if record.get("status") not in ACTIVE_STATUSES:
                raise ValueError("A completed or cancelled appointment cannot change status")
            changes = {
                "status": target,
                "updated_at": datetime.now(timezone.utc),
                "updated_by": "system" if expired else actor_uid,
            }
            if target == "completed":
                amount = settings.doctor_appointment_earning_tk
                changes["earnings_amount_tk"] = amount
                # The status gate above and this atomic increment prevent
                # retries and concurrent completions from crediting twice.
                earnings = self._db.collection("doctor_earnings").document(record["doctor_id"])
                transaction.set(earnings, {
                    "total_earnings": firestore.Increment(amount),
                    "updated_at": changes["updated_at"],
                }, merge=True)
            transaction.update(ref, changes)
            return {**record, **changes}

        record = change(self._db.transaction())
        if new_status is not None and record and record["status"] != new_status:
            raise ValueError("This appointment has expired and was cancelled")
        return record

    def transition(self, appointment_id: str, new_status: str, actor_uid: str, actor_role: str):
        return self._change_status(appointment_id, new_status, actor_uid, actor_role)

    def cancel_expired(self) -> None:
        # A single-field query avoids scanning terminal appointment history
        # or requiring a composite index. No list limit may skip overdue rows.
        query = self._collection.where(filter=FieldFilter(
            "status", "in", sorted(ACTIVE_STATUSES)
        ))
        for snapshot in query.stream():
            record = {"id": snapshot.id, **snapshot.to_dict()}
            if is_expired(record):
                self._change_status(snapshot.id)

    def list_by_doctor(self, doctor_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.list(limit=limit, doctor_id=doctor_id)

    def get_for_user(
        self, appointment_id: str, user_id: str, role: str
    ) -> Optional[dict[str, Any]]:
        record = self.get(appointment_id)
        if not record:
            return None
        if role == "admin":
            return record
        if role not in {"doctor", "patient"}:
            return None
        # Account roles do not determine which side of an appointment a user
        # occupies (a doctor can also book an appointment as a patient).
        return record if user_id in (record.get("doctor_id"), record.get("patient_id")) else None
