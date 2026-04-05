from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Pyxis"
    DEBUG: bool = False

    DATABASE_URL: str = "postgresql+asyncpg://pyxis:pyxis@localhost:5432/pyxis"
    REDIS_URL: str = "redis://localhost:6379"

    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-6"

    SECRET_KEY: str = "change-me-in-production"

    # Embedding model (local, no external API needed — keeps customer IaC private)
    EMBED_MODEL: str = "BAAI/bge-small-en-v1.5"

    # How many knowledge chunks to inject into AI context per query
    RAG_TOP_K: int = 8

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
