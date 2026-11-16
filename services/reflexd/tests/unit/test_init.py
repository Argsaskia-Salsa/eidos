from unittest.mock import AsyncMock, patch

import pytest

from reflexd import call
from reflexd.gateway_client import GatewayClient


class TestInitCall:
    @pytest.mark.asyncio
    async def test_call_raises_when_no_gateway_client(self):
        with patch("reflexd.get_gateway_client", return_value=None), pytest.raises(
            RuntimeError, match="Gateway client not connected"
        ):
            await call("set_avatar", {"face": "happy"})

    @pytest.mark.asyncio
    async def test_call_forwards_to_gateway_client(self):
        mock_client = AsyncMock(spec=GatewayClient)
        mock_client.call.return_value = {"result": "success"}

        with patch("reflexd.get_gateway_client", return_value=mock_client):
            result = await call("set_avatar", {"face": "happy"})

        assert result == {"result": "success"}
        mock_client.call.assert_called_once_with("set_avatar", {"face": "happy"})