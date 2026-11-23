from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Lumen BI"
    public_url: str = "http://localhost:8080"
    data_dir: Path = Path("./data")

    database_url: str = ""  # empty -> SQLite file in data_dir (development only)
    redis_url: str = "redis://localhost:6379/0"

    secret_key: str = "dev-secret-change-me"
    encryption_key: str = ""

    access_token_minutes: int = 15
    refresh_token_days: int = 7
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    password_reset_minutes: int = 60
    cookie_secure: bool = False
    sso_state_minutes: int = 10

    # Background jobs: "celery" (Docker), "inline" (background thread, for development without Redis),
    # "eager" (run immediately in the request; tests)
    jobs_mode: str = "celery"
    timezone: str = "Asia/Dubai"  # schedules are interpreted in this time zone
    max_upload_mb: int = 200
    snapshots_to_keep: int = 3
    # Let API connections call localhost/link-local addresses (development with tools/fake_sources.py only)
    allow_local_api_urls: bool = False

    admin_email: str = "admin@example.com"
    admin_password: str = "ChangeMe123!"
    admin_name: str = "Administrator"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "Lumen BI <no-reply@example.com>"
    smtp_tls: bool = True

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'metadata.db').resolve()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
