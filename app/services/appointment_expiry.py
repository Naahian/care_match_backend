import asyncio
import logging

from app.core.firebase import get_db
from app.repositories.appointment import AppointmentRepository

logger = logging.getLogger(__name__)


def cancel_expired_appointments() -> None:
    AppointmentRepository(get_db()).cancel_expired()


async def run_appointment_expiry() -> None:
    while True:
        try:
            await asyncio.to_thread(cancel_expired_appointments)
        except Exception:
            logger.exception("Automatic appointment cancellation failed; retrying in 60 seconds")
        await asyncio.sleep(60)
