import logging
from typing import Any, Optional

from google.cloud.firestore_v1.base_query import FieldFilter

from app.repositories.base import BaseRepository

_logger = logging.getLogger(__name__)


class TriageRepository(BaseRepository):
    collection_name = "triage_assessments"

    def list_for_patient(
        self, patient_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self.list(limit=limit, patient_id=patient_id)

    def get_by_patient(self, triage_id: str, patient_id: str) -> Optional[dict[str, Any]]:
        record = self.get(triage_id)
        if record and record.get("patient_id") == patient_id:
            return record
        return None

    def ids_for_patient(self, patient_id: str, chunk: int = 500) -> list[str]:
        """Document ids of every assessment belonging to ``patient_id``.

        Streamed in chunks rather than via ``list_for_patient``, whose default
        20-item page would silently leave older assessments behind.
        """
        ids: list[str] = []
        query = self._collection.where(
            filter=FieldFilter("patient_id", "==", patient_id)
        )
        for doc in query.stream():
            ids.append(doc.id)
            if len(ids) % chunk == 0:
                _logger.debug("scanned %d assessments for %s", len(ids), patient_id)
        return ids

    def delete_for_patient(self, patient_id: str) -> int:
        """Delete all of a patient's assessments, 500 writes per batch.

        Returns the number of documents removed. Firestore caps a batch at 500
        writes, so a long history is committed in several batches.
        """
        ids = self.ids_for_patient(patient_id)
        if not ids:
            return 0

        batch_size = 500
        for start in range(0, len(ids), batch_size):
            batch = self._db.batch()
            for doc_id in ids[start : start + batch_size]:
                batch.delete(self._collection.document(doc_id))
            batch.commit()

        return len(ids)
