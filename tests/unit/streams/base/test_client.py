"""
Unit tests for StreamClient base class (HTTP method signatures).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from reflexd.streams.base.client import StreamClient


@pytest.fixture
def mock_session():
    """Create a mock aiohttp ClientSession."""
    session = AsyncMock(spec=aiohttp.ClientSession)
    return session


@pytest.fixture
def client(mock_session):
    """Create a StreamClient with mocked session."""
    client = StreamClient("http://localhost:8773", element_name="pattern", intensity_name="brightness")
    client._session = mock_session
    return client


def test_client_initialization():
    """Test client initializes with correct attributes."""
    client = StreamClient("http://test:1234", element_name="pose", intensity_name="smoothing")
    
    assert client.base_url == "http://test:1234"
    assert client.element_name == "pose"
    assert client.intensity_name == "smoothing"
    assert client._session is None


@pytest.mark.asyncio
async def test_client_context_manager():
    """Test async context manager creates and closes session."""
    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_session = AsyncMock()
        mock_session_class.return_value = mock_session
        
        async with StreamClient("http://test:1234") as client:
            assert client._session is mock_session
        
        mock_session.close.assert_called_once()


@pytest.mark.asyncio
async def test_client_requires_session():
    """Test methods raise RuntimeError without session."""
    client = StreamClient("http://test:1234")
    
    with pytest.raises(RuntimeError, match="not initialized"):
        await client.status()


@pytest.mark.asyncio
async def test_status_method(client, mock_session):
    """Test status() calls GET /status."""
    mock_response = AsyncMock()
    mock_response.json = AsyncMock(return_value={"pattern": "test", "fps": 20})
    mock_session.get.return_value.__aenter__.return_value = mock_response
    
    result = await client.status()
    
    mock_session.get.assert_called_once_with("http://localhost:8773/status")
    assert result == {"pattern": "test", "fps": 20}


@pytest.mark.asyncio
async def test_set_element_method(client, mock_session):
    """Test set_element() calls POST /{element_name}."""
    mock_response = AsyncMock()
    mock_response.json = AsyncMock(return_value={"ok": True})
    mock_session.post.return_value.__aenter__.return_value = mock_response
    
    result = await client.set_element("rainbow", color=[255, 0, 0])
    
    mock_session.post.assert_called_once_with(
        "http://localhost:8773/pattern",
        json={"pattern": "rainbow", "args": {"color": [255, 0, 0]}}
    )
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_set_fps_method(client, mock_session):
    """Test set_fps() calls POST /fps."""
    mock_response = AsyncMock()
    mock_response.json = AsyncMock(return_value={"ok": True})
    mock_session.post.return_value.__aenter__.return_value = mock_response
    
    result = await client.set_fps(30.0)
    
    mock_session.post.assert_called_once_with(
        "http://localhost:8773/fps",
        json={"fps": 30.0}
    )
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_set_intensity_method(client, mock_session):
    """Test set_intensity() calls POST /{intensity_name}."""
    mock_response = AsyncMock()
    mock_response.json = AsyncMock(return_value={"ok": True})
    mock_session.post.return_value.__aenter__.return_value = mock_response
    
    result = await client.set_intensity(0.5)
    
    mock_session.post.assert_called_once_with(
        "http://localhost:8773/brightness",
        json={"brightness": 0.5}
    )
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_list_elements_method(client, mock_session):
    """Test list_elements() calls GET /{element_name}s."""
    mock_response = AsyncMock()
    mock_response.json = AsyncMock(return_value={"patterns": [{"name": "rainbow"}]})
    mock_session.get.return_value.__aenter__.return_value = mock_response
    
    result = await client.list_elements()
    
    mock_session.get.assert_called_once_with("http://localhost:8773/patterns")
    assert result == {"patterns": [{"name": "rainbow"}]}


@pytest.mark.asyncio
async def test_reload_elements_method(client, mock_session):
    """Test reload_elements() calls POST /{element_name}s/reload."""
    mock_response = AsyncMock()
    mock_response.json = AsyncMock(return_value={"ok": True, "patterns": ["rainbow"]})
    mock_session.post.return_value.__aenter__.return_value = mock_response
    
    result = await client.reload_elements()
    
    mock_session.post.assert_called_once_with(
        "http://localhost:8773/patterns/reload",
        json={}
    )
    assert result == {"ok": True, "patterns": ["rainbow"]}


@pytest.mark.asyncio
async def test_custom_element_and_intensity_names():
    """Test client works with custom element/intensity names."""
    from unittest.mock import Mock
    
    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        mock_session.close = AsyncMock()
        
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={"ok": True})
        
        # Create async context manager mock objects
        mock_post_cm = AsyncMock()
        mock_post_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post_cm.__aexit__ = AsyncMock(return_value=None)
        
        mock_get_cm = AsyncMock()
        mock_get_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_get_cm.__aexit__ = AsyncMock(return_value=None)
        
        # Configure post/get to return the context manager directly
        mock_session.post.return_value = mock_post_cm
        mock_session.get.return_value = mock_get_cm
        
        async with StreamClient("http://test:1234", element_name="pose", intensity_name="smoothing") as client:
            await client.set_element("wave")
            mock_session.post.assert_called_with(
                "http://test:1234/pose",
                json={"pose": "wave", "args": {}}
            )
            
            await client.set_intensity(0.8)
            mock_session.post.assert_called_with(
                "http://test:1234/smoothing",
                json={"smoothing": 0.8}
            )
            
            await client.list_elements()
            mock_session.get.assert_called_with("http://test:1234/poses")
            
            await client.reload_elements()
            mock_session.post.assert_called_with(
                "http://test:1234/poses/reload",
                json={}
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])