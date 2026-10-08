from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
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
    # Optional so health and the private provisioning CLI need no signing key.
    # Auth checks the UTF-8 byte length before using it; there is no default key.
    auth_jwt_secret: SecretStr | None = None
    # Invalid/missing origins disable cookie-backed auth, not health/provisioning.
    auth_trusted_origin: str | None = None
    # Include the proxy prefix if the public API is mounted at /api.
    auth_cookie_path: str = "/auth"

    @property
    def cookie_secure(self) -> bool | None:
        origin = self.auth_trusted_origin
        if not origin:
            return None
        try:
            parsed = urlsplit(origin)
            hostname = parsed.hostname
            if not hostname or parsed.username or parsed.password:
                return None
            host_part = f"[{hostname}]" if ":" in hostname else hostname
            expected = f"{parsed.scheme}://{host_part}"
            if parsed.port is not None:
                expected += f":{parsed.port}"
            if origin != expected:
                return None
            if parsed.scheme == "https":
                return True
            if parsed.scheme != "http":
                return None
            host = parsed.hostname
            if host is None:
                return None
            if host == "localhost":
                return False
            return False if ip_address(host).is_loopback else None
        except ValueError:
            return None

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


class IntegrationKeySettings(BaseSettings):
    """Server-only configuration; never inherit the app's root .env source.

    Parse the keyring only when credential operations are requested so missing
    or invalid configuration does not disable health or private provisioning.
    """

    model_config = SettingsConfigDict(
        env_prefix="INTEGRATION_", extra="ignore", hide_input_in_errors=True
    )

    keyring: SecretStr | None = Field(default=None, exclude=True, repr=False)
    write_key_id: str | None = Field(default=None, exclude=True, repr=False)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Explicit injection is useful for disposable tests; deployment supplies
        # process environment through an owner-approved secret delivery method.
        return init_settings, env_settings


@lru_cache
def get_settings() -> Settings:
    return Settings()
