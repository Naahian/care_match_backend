from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status

from app.llm.client import LLMClient, get_llm_client
from app.repositories.triage import TriageRepository
from app.schemas.models import TriageRequest, TriageResponse, Urgency


class TriageService:
    def __init__(
        self,
        repository: TriageRepository = Depends(TriageRepository),
        llm: LLMClient = Depends(get_llm_client),
    ) -> None:
        self._repo = repository
        self._llm = llm

    def assess(self, request: TriageRequest, patient_id: str) -> TriageResponse:
        payload = request.model_dump()
        result = self._llm.triage(payload)

        now = datetime.now(timezone.utc)
        triage_id = self._repo.create(
            {
                **payload,
                "patient_id": patient_id,
                "urgency": result.urgency.value,
                "summary": result.summary,
                "recommended_specialties": result.recommended_specialties,
                "red_flags": result.red_flags,
                "needs_human_review": result.needs_human_review,
                "created_at": now,
            }
        )

        return TriageResponse(
            triage_id=triage_id,
            urgency=Urgency(result.urgency.value),
            summary=result.summary,
            recommended_specialties=result.recommended_specialties,
            red_flags=result.red_flags,
            created_at=now,
        )

    def delete_assessments(self, patient_id: str) -> int:
        """Delete every triage assessment belonging to ``patient_id``.

        ``patient_id`` is always the caller's own uid as resolved by the
        endpoint, so this cannot be aimed at another patient's records.

        Raises 404 when the patient has no assessments to delete, which keeps
        the endpoint from silently reporting success on a no-op.
        """
        deleted = self._repo.delete_for_patient(patient_id)
        if deleted == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No triage assessments found for this patient",
            )
        return deleted
