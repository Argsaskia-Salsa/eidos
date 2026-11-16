import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from reflexd.gateway_client import GatewayClient
from reflexd.registry import clear
from reflexd.server import create_app


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
    return create_app()


@pytest.fixture
async def client(app):
    async with TestClient(TestServer(app)) as client:
        yield client


@pytest.fixture
def sample_reflexes_dir():
    return Path(__file__).parent / "fixtures" / "sample_reflexes"