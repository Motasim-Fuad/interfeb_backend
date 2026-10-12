import os
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings

PLACEHOLDER_SECRETS = {"change-this-secret-in-production", "change-this-setup-secret", "change-me", ""}
_LOCAL_ENVS = {"local", "dev", "development", "test"}


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Interfab QC Backend"
    ENV: str = "local"
    DEBUG: bool = True
    # Factory clock: "today" for report dates is the factory's day, not the server's (UTC) day.
    TIMEZONE: str = "Asia/Dhaka"
    # Swagger UI at /docs. Unset = on locally, off in production.
    ENABLE_DOCS: Optional[bool] = None

    # Database connection pool, per worker process. Keep pool_size * workers under the DB's connection limit.
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_TIMEOUT_SECONDS: int = 10
    DB_POOL_RECYCLE_SECONDS: int = 300

    # Database
    DATABASE_URL: str = "postgresql+psycopg2://interfab:interfab@db:5432/interfab"

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # JWT
    JWT_SECRET_KEY: str = "change-this-secret-in-production"
    JWT_ALGORITHM: str = "HS256"
    ADMIN_TOKEN_EXPIRE_MINUTES: int = 60 * 12          # 12 hours
    APP_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7        # 7 days (factory floor devices stay logged in)

    # Admin bootstrapping - only someone who knows this secret can register a NEW admin account.
    # Set your own random value in .env. Needed every time you create an admin (first one or later ones).
    ADMIN_SETUP_SECRET: str = "change-this-setup-secret"

    # CORS - comma-separated list of allowed origins. "*" for local dev; lock to real domain(s) in production.
    ALLOWED_ORIGINS: str = "*"

    # Roll lock
    ROLL_LOCK_TTL_SECONDS: int = 60 * 10               # 10 minutes

    # Storage: "local" now, switch to "s3" later without touching feature code
    STORAGE_BACKEND: str = "local"
    MEDIA_ROOT: str = "/app/media"
    MEDIA_BASE_URL: str = "http://localhost:8000/media"

    # AWS (used only when STORAGE_BACKEND=s3)
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "ap-south-1"
    AWS_S3_BUCKET: str = ""

    # FabriQ assistant. Free offline engine by default; point these at any OpenAI-compatible
    # endpoint (OpenAI, Groq, Gemini, Ollama, LM Studio) to let an LLM plan the tool calls.
    ASSISTANT_NAME: str = "FabriQ"
    ASSISTANT_LLM_BASE_URL: str = ""
    ASSISTANT_LLM_API_KEY: str = ""
    ASSISTANT_LLM_MODEL: str = ""
    ASSISTANT_LLM_TIMEOUT_SECONDS: float = 25.0
    # "hash" = built-in offline embedder; "openai" = ASSISTANT_EMBED_MODEL on the same endpoint.
    ASSISTANT_EMBEDDER: str = "hash"
    ASSISTANT_EMBED_MODEL: str = "text-embedding-3-small"

    class Config:
        env_file = ".env"
        case_sensitive = True

    @property
    def is_production(self) -> bool:
        # Render sets RENDER=true on every service, so a forgotten ENV still counts as production there.
        return self.ENV.lower() not in _LOCAL_ENVS or bool(os.environ.get("RENDER"))

    @property
    def docs_enabled(self) -> bool:
        return self.ENABLE_DOCS if self.ENABLE_DOCS is not None else not self.is_production

    @property
    def admin_setup_enabled(self) -> bool:
        strong = self.ADMIN_SETUP_SECRET not in PLACEHOLDER_SECRETS and len(self.ADMIN_SETUP_SECRET) >= 16
        return strong or (not self.is_production and bool(self.ADMIN_SETUP_SECRET))

    def assert_secure(self) -> None:
        """A placeholder JWT secret lets anyone mint an admin token, so production refuses to start."""
        if self.is_production and (self.JWT_SECRET_KEY in PLACEHOLDER_SECRETS or len(self.JWT_SECRET_KEY) < 32):
            raise RuntimeError("JWT_SECRET_KEY must be set to a random value of at least 32 characters in production.")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
