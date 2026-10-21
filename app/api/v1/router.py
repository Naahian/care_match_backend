from fastapi import APIRouter

from app.api.v1 import admin, appointments, chats, payments, triage, users, video

api_router = APIRouter()
api_router.include_router(triage.router)
api_router.include_router(appointments.router)
api_router.include_router(payments.router)
api_router.include_router(video.router)
api_router.include_router(users.router)
api_router.include_router(admin.router)
api_router.include_router(chats.router)

__all__ = ["api_router"]
