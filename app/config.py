from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    database_url: str = "sqlite+aiosqlite:///./cabinet.db"
    jwt_secret: str = "change-this-secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    admin_username: str = "admin"

    @property
    def app_dir(self) -> Path:
        return Path(__file__).parent.resolve()


settings = Settings()