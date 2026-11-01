from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]  # adjust so this is the folder containing .env

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    project_name: str = "care_match_backend"
    api_v1_prefix: str = "/api/v1"
    environment: str = "development"
    debug: bool = True
    appointment_timezone: str = "Asia/Dhaka"

    @field_validator("appointment_timezone")
    @classmethod
    def _appointment_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("appointment_timezone must be a valid IANA timezone") from exc
        return value

    firebase_credentials_path: Optional[str] = None
    firebase_api_key: Optional[str] = None
    firebase_storage_bucket: Optional[str] = None
    google_cloud_project: Optional[str] = None

    supabase_url: Optional[str] = None
    supabase_secret_key: Optional[str] = Field(default=None, repr=False)
    supabase_storage_public: bool = False
    supabase_signed_url_seconds: int = Field(default=86400, ge=60, le=604800)
    supabase_timeout_seconds: float = Field(default=30, gt=0)

    agora_app_id: Optional[str] = None
    agora_app_certificate: Optional[str] = None

    
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-3.1-flash"
    llm_timeout_seconds: float = 30.0

    payment_provider_secret: Optional[str] = None
    sslcommerz_store_id: Optional[str] = None
    sslcommerz_store_password: Optional[str] = None
    sslcommerz_sandbox: bool = True
    sslcommerz_callback_base_url: str = "http://localhost:8000"
    sslcommerz_timeout_seconds: float = Field(default=30.0, gt=0)
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
