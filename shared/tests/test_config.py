import os
import tempfile
from pathlib import Path

from eidos_shared.config import BaseServiceSettings


def test_base_service_settings_defaults() -> None:
    settings = BaseServiceSettings()
    assert settings.log_level == "INFO"
    assert settings.data_dir == Path.home() / ".config" / "eidos"
    assert settings.hindsight_url == "http://localhost:8888"
    assert settings.hindsight_bank == "hermes"
    assert settings.reflexd_url == "http://localhost:8770"
    assert settings.gateway_mcp_url == "http://localhost:8767/mcp"
    assert settings.apperception_mcp_url == "http://localhost:8780"
    assert settings.predict_learn_mcp_url == "http://localhost:8781"
    assert settings.identity_mcp_url == "http://localhost:8782"


def test_base_service_settings_env_override() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("LOG_LEVEL=DEBUG\n")
        f.write("HINDSIGHT_URL=http://custom:9999\n")
        f.write("DATA_DIR=/custom/path\n")
        env_path = f.name

    try:
        os.environ["LOG_LEVEL"] = "DEBUG"
        os.environ["HINDSIGHT_URL"] = "http://custom:9999"
        os.environ["DATA_DIR"] = "/custom/path"
        settings = BaseServiceSettings()
        assert settings.log_level == "DEBUG"
        assert settings.hindsight_url == "http://custom:9999"
        assert settings.data_dir == Path("/custom/path")
    finally:
        os.unlink(env_path)
        os.environ.pop("LOG_LEVEL", None)
        os.environ.pop("HINDSIGHT_URL", None)
        os.environ.pop("DATA_DIR", None)


def test_base_service_settings_case_insensitive() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("log_level=WARNING\n")
        env_path = f.name

    try:
        os.environ["LOG_LEVEL"] = "WARNING"
        settings = BaseServiceSettings()
        assert settings.log_level == "WARNING"
    finally:
        os.unlink(env_path)
        os.environ.pop("LOG_LEVEL", None)
