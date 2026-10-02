from typing import Any, Optional

from app.repositories.base import BaseRepository
from app.schemas.models import DoctorProfile


class DoctorRepository(BaseRepository):
    """The ``doctors`` collection, keyed by the Firebase Auth uid."""

    collection_name = "doctors"

    def create_for_user(
        self,
        uid: str,
        profile: DoctorProfile,
        *,
        email: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> str:
        return self.create(
            self._document(uid, profile, email=email, display_name=display_name),
            doc_id=uid,
        )

    def update_profile(
        self, uid: str, profile: DoctorProfile, **fields: Any
    ) -> dict[str, Any]:
        data = self._document(uid, profile)
        data.update(fields)
        self.update(uid, data)
        return data

    def list_all(
        self, limit: int = 50, *, verified: Optional[bool] = None
    ) -> list[dict[str, Any]]:
        """Return doctor documents, optionally restricted to verified ones.

        Firestore single-field filters like this one are served from the
        collection's automatic index, so no composite index is required.
        """
        return self.list(limit=limit, verified=verified)

    def _document(
        self,
        uid: str,
        profile: DoctorProfile,
        *,
        email: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> dict[str, Any]:
        return {
            "uid": uid,
            "email": email,
            "display_name": display_name,
            "phone": profile.phone,
            "clinics": [clinic.model_dump() for clinic in profile.clinics],
            "specialties": profile.specialties,
            "degrees": profile.degrees,
            "time_available": [
                {"start": slot.start.strftime("%H:%M"), "end": slot.end.strftime("%H:%M")}
                for slot in profile.time_available
            ],
            "days_available": profile.days_available,
            "verified": profile.verified,
        }
