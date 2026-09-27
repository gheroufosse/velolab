from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

# Local development uses the same root .env as Docker Compose.
ENV_FILE = Path(__file__).resolve().parents[4] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    postgres_user: str
    postgres_db: str
    postgres_password: SecretStr
    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5434, ge=1, le=65535)

    @property
    def database_url(self) -> URL:
        # URL.create handles special characters without manual URL escaping.
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
