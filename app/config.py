"""Configuration and settings for the Weather & Power-Cut Prediction REST API."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    app_name: str = "Raiden Grid & Weather Telemetry API"
    app_version: str = "1.0.0"
    google_api_key: str = ""
    cache_ttl_seconds: int = 300
    http_timeout_seconds: float = 10.0
    host: str = "0.0.0.0"
    port: int = 8000
    user_agent: str = "RaidenGridWeatherAPI/1.0 (Linux; Telemetry Service)"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
