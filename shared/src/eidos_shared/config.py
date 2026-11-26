from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    log_level: str = "INFO"
    data_dir: Path = Path.home() / ".config" / "eidos"
    hindsight_url: str = "http://localhost:8888"
    hindsight_bank: str = "hermes"
    reflexd_url: str = "http://localhost:8770"
    gateway_mcp_url: str = "http://localhost:8767/mcp"
    apperception_mcp_url: str = "http://localhost:8780"
    predict_learn_mcp_url: str = "http://localhost:8781"
    identity_mcp_url: str = "http://localhost:8782"
