from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Interfab QC Backend"
    ENV: str = "local"
    DEBUG: bool = True

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
