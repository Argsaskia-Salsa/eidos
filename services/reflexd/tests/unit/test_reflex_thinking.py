from unittest.mock import AsyncMock, patch

import pytest

from reflexd.registry import clear, get_handler


@pytest.fixture(autouse=True)
def reset_registry():
    clear()
    yield
    clear()


@pytest.mark.asyncio
async def test_thinking_start_sets_avatar():
    mock_call = AsyncMock()

    with patch("reflexd.call", mock_call):
        import importlib

        import reflexes.thinking as thinking_module

        importlib.reload(thinking_module)

        handler = get_handler("thinking_start")
        assert handler is not None
        await handler({})

        call_args = mock_call.call_args
        tool = call_args.args[0]
        params = call_args.kwargs
        assert tool == "set_avatar"
        assert params == {"face": "thinking"}


@pytest.mark.asyncio
async def test_thinking_end_sets_neutral():
    mock_call = AsyncMock()

    with patch("reflexd.call", mock_call):
        import importlib

        import reflexes.thinking as thinking_module

        importlib.reload(thinking_module)

        handler = get_handler("thinking_end")
        assert handler is not None
        await handler({})

        call_args = mock_call.call_args
        tool = call_args.args[0]
        params = call_args.kwargs
        assert tool == "set_avatar"
        assert params == {"face": "neutral"}
