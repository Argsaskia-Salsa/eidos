import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from reflexd.gateway_client import GatewayClient
from reflexd.registry import clear
from reflexd.server import _running, create_app

# Add reflexes directory (services/reflexd/reflexes) to Python path
REFLEXES_ROOT = Path(__file__).parent.parent / "reflexes"
sys.path.insert(0, str(REFLEXES_ROOT.parent))


@pytest.fixture(autouse=True)
def clear_reflexes_modules():
    """Clear reflexes modules from sys.modules to ensure test isolation."""
    # Clear before test
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("reflexes."):
            del sys.modules[mod_name]
    if "reflexes" in sys.modules:
        del sys.modules["reflexes"]
    yield
    # Clear after test
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("reflexes."):
            del sys.modules[mod_name]
    if "reflexes" in sys.modules:
        del sys.modules["reflexes"]


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_gateway_client():
    client = AsyncMock(spec=GatewayClient)
    client.url = "http://test-gateway:8767/mcp"
    client.token = "test-token"
    client.session_id = "test-session-id"
    client.session = AsyncMock()
    client.connect = AsyncMock()
    client.call = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def temp_reflexes_dir(tmp_path):
    reflexes_dir = tmp_path / "reflexes"
    reflexes_dir.mkdir()
    return reflexes_dir


@pytest.fixture
def app():
    clear()
    _running.clear()
    return create_app()


@pytest.fixture
async def client(app):
    async with TestClient(TestServer(app)) as client:
        yield client


@pytest.fixture
def sample_reflexes_dir():
    return Path(__file__).parent / "fixtures" / "sample_reflexes"
