"""Centralized configuration. Read from env, validated once."""
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    groq_api_key: str = Field(default="", description="Groq API key")
    generator_model: str = "openai/gpt-oss-120b"
    router_model: str = "openai/gpt-oss-20b"
    judge_model: str = "openai/gpt-oss-120b"

    # Slack
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_app_token: str = ""
    # Local demo only: map real Slack member IDs onto seeded employees,
    # e.g. {"U0123ABC": "U_DEMO_ANANYA"}.
    slack_user_aliases: dict[str, str] = Field(default_factory=dict)

    # Storage
    postgres_url: str = "postgresql://hrbot:hrbot@localhost:5432/hrbot"
    redis_url: str = "redis://localhost:6379/0"

    # Observability
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"

    # Behavior
    log_level: str = "INFO"
    worker_concurrency: int = 4
    retrieval_top_k: int = 20
    rerank_top_k: int = 5
    retrieval_score_threshold: float = 0.15
    semantic_cache_threshold: float = 0.95

    # Models (local)
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    reranker_model: str = "BAAI/bge-reranker-base"
    embedding_dim: int = 384


@lru_cache
def get_settings() -> Settings:
    return Settings()
