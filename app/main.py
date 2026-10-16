from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.core.firebase import _initialize_app
from app.schemas.responses import ErrorResponse, HealthResponse, SuccessResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    _initialize_app()
    yield


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.project_name,
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        responses={
            "default": {"model": ErrorResponse, "description": "API error"},
            422: {"model": ErrorResponse, "description": "Request validation failed"},
        },
        exception_handlers={
            HTTPException: http_exception_handler,
            RequestValidationError: validation_exception_handler,
            Exception: unhandled_exception_handler,
        },
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/health", tags=["system"], response_model=SuccessResponse[HealthResponse])
    async def health() -> SuccessResponse[HealthResponse]:
        return SuccessResponse(data=HealthResponse(environment=settings.environment))

    application.include_router(api_router, prefix=settings.api_v1_prefix)
    return application


app = create_app()
