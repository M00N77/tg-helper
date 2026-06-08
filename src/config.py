from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class LLMDefaults:
    # Имена моделей на май 2026 — менять при выходе новых
    OPENAI_CHAT_LIGHT = "gpt-5-mini"
    OPENAI_CHAT_HEAVY = "gpt-5.5"
    OPENAI_EMBED = "text-embedding-3-small"

    GEMINI_CHAT_LIGHT = "gemini-2.5-flash"
    GEMINI_CHAT_HEAVY = "gemini-3-flash-preview"
    GEMINI_EMBED = "text-embedding-004"

    GROQ_CHAT_LIGHT = "llama-4-scout-17b-16e-instruct"
    GROQ_CHAT_HEAVY = "llama-4-maverick-17b-128e-instruct"
    GROQ_EMBED = "text-embedding-3-small"  # Groq не поддерживает embed — заглушка


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(..., description="Токен control-бота из @BotFather")
    owner_telegram_id: int = Field(..., description="Telegram user_id основного владельца")
    allowed_telegram_ids: list[int] = Field(
        default_factory=list,
        description="Дополнительные Telegram user_id, которым разрешено пользоваться ботом (через запятую)",
    )

    @field_validator("allowed_telegram_ids", mode="before")
    @classmethod
    def parse_comma_separated(cls, v: object) -> object:
        if isinstance(v, str):
            v = [int(x.strip()) for x in v.split(",") if x.strip()]
        return v
    encryption_key: str = Field(..., description="Fernet-ключ (base64)")
    database_url: str = Field(..., description="PostgreSQL connection string (postgresql+asyncpg://...)")

    @property
    def data_dir(self) -> Path:
        path = PROJECT_ROOT / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def all_allowed_ids(self) -> set[int]:
        return {self.owner_telegram_id} | set(self.allowed_telegram_ids)


settings = Settings()
