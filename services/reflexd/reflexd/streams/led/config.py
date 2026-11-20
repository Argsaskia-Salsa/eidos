"""LED Stream configuration.
Class attributes - no dataclasses/Pydantic needed.
"""

import os
from pathlib import Path

# Load .env file if exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # dotenv not installed, rely on environment variables


class LEDConfig:
    """LED Stream configuration from environment variables."""
    # Network
    WS_HOST: str = os.getenv("WS_HOST", "0.0.0.0")
    WS_PORT: int = int(os.getenv("WS_PORT", "8772"))
    HTTP_PORT: int = int(os.getenv("HTTP_PORT", "8773"))

    # Defaults
    DEFAULT_FPS: float = float(os.getenv("DEFAULT_FPS", "20"))
    DEFAULT_BRIGHTNESS: float = float(os.getenv("DEFAULT_BRIGHTNESS", "1.0"))

    # Hardware
    NUM_LEDS: int = int(os.getenv("NUM_LEDS", "12"))
    COLOR_ORDER: str = os.getenv("COLOR_ORDER", "grb")  # grb, rgb, etc.

    # Gateway/MCP
    GATEWAY_MCP_URL: str = os.getenv("GATEWAY_MCP_URL", "http://127.0.0.1:8767/mcp")
    STACKCHAN_TOKEN: str = os.getenv("STACKCHAN_TOKEN", "")
    TARGET: str = os.getenv("TARGET", "base_ring")
    MAX_FPS: float = float(os.getenv("MAX_FPS", "20"))
    HOST_IP: str = os.getenv("HOST_IP", "127.0.0.1")

    @property
    def WS_SERVER_URL(self) -> str:
        return f"ws://{self.HOST_IP}:{self.WS_PORT}"

    @property
    def HTTP_SERVER_URL(self) -> str:
        return f"http://{self.HOST_IP}:{self.HTTP_PORT}"


# Singleton config instance
CONFIG = LEDConfig()