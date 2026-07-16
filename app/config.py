from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    gemini_api_key: str
    model_name: str = "gemini/gemini-3.1-flash-lite"
    database_path: str = "data/processed/app.db"
    # Origin the frontend dev server runs on (vite.config.ts pins port 8080).
    frontend_origin: str = "http://localhost:8080"


@lru_cache
def get_settings() -> Settings:
    return Settings()
