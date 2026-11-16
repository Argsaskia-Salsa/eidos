import pytest
from unittest.mock import AsyncMock, patch

from reflexd.registry import clear, get_handler


@pytest.fixture(autouse=True)
def reset_registry():
    clear()
    yield
    clear()


@pytest.mark.asyncio
async def test_user_message_happy_yaw10():
    mock_call = AsyncMock()
    
    with patch("reflexd.call", mock_call):
        import importlib
        import reflexes.greeting as greeting_module
        importlib.reload(greeting_module)

        handler = get_handler("user_message")
        assert handler is not None
        await handler({})

        assert mock_call.call_count == 2
        
        avatar_call = None
        move_call = None
        for call_args in mock_call.call_args_list:
            tool = call_args.args[0]
            params = call_args.kwargs
            if tool == "set_avatar":
                avatar_call = params
            elif tool == "move_head":
                move_call = params
        
        assert avatar_call is not None
        assert avatar_call == {"face": "happy"}
        
        assert move_call is not None
        assert move_call["yaw"] == 10
        assert move_call["pitch"] == 60


@pytest.mark.asyncio
async def test_user_response_neutral_yaw0():
    mock_call = AsyncMock()
    
    with patch("reflexd.call", mock_call):
        import importlib
        import reflexes.greeting as greeting_module
        importlib.reload(greeting_module)

        handler = get_handler("user_response")
        assert handler is not None
        await handler({})

        assert mock_call.call_count == 2
        
        avatar_call = None
        move_call = None
        for call_args in mock_call.call_args_list:
            tool = call_args.args[0]
            params = call_args.kwargs
            if tool == "set_avatar":
                avatar_call = params
            elif tool == "move_head":
                move_call = params
        
        assert avatar_call is not None
        assert avatar_call == {"face": "neutral"}
        
        assert move_call is not None
        assert move_call["yaw"] == 0
        assert move_call["pitch"] == 60