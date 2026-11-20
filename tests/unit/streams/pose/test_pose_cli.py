"""Unit tests for PoseStreamCLI."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from reflexd.streams.pose.cli import PoseStreamCLI
from reflexd.streams.pose.client import PoseStreamClient
from reflexd.streams.pose.registry import PoseRegistry


class TestPoseStreamCLI:
    """Tests for PoseStreamCLI."""

    def test_cli_attributes(self):
        """Test CLI has correct attributes."""
        cli = PoseStreamCLI()
        
        assert cli.element_name == "pose"
        assert cli.intensity_name == "smoothing"
        assert cli.registry == PoseRegistry
        assert cli.http_url == "http://127.0.0.1:8775"

    def test_cli_parser_creation(self):
        """Test parser creates all expected subcommands."""
        cli = PoseStreamCLI()
        
        # Check subcommands exist
        subcommands = [action.dest for action in cli.parser._subparsers._actions 
                      if hasattr(action, '_name_parser_map')]
        
        # Get all subcommand names
        for action in cli.parser._subparsers._actions:
            if hasattr(action, '_name_parser_map'):
                assert "pose" in action._name_parser_map
                assert "fps" in action._name_parser_map
                assert "smoothing" in action._name_parser_map
                assert "list" in action._name_parser_map
                assert "reload" in action._name_parser_map
                assert "start" in action._name_parser_map
                assert "stop" in action._name_parser_map
                assert "status" in action._name_parser_map
                assert "monitor" in action._name_parser_map

    def test_add_element_args(self):
        """Test pose-specific args are added."""
        cli = PoseStreamCLI()
        
        parser = MagicMock()
        cli.add_element_args(parser)
        
        # Check that add_argument was called for pose-specific args
        arg_calls = [call[0][0] for call in parser.add_argument.call_args_list]
        assert "--amplitude" in arg_calls
        assert "--speed" in arg_calls
        assert "--smooth" in arg_calls
        assert "--jitter" in arg_calls

    def test_parse_element_args(self):
        """Test parsing element args from namespace."""
        cli = PoseStreamCLI()
        
        args = MagicMock()
        args.amplitude = 45.0
        args.speed = 2.0
        args.smooth = 0.5
        args.jitter = 3.0
        
        result = cli._parse_element_args(args)
        
        assert result == {
            "amplitude": 45.0,
            "speed": 2.0,
            "smooth": 0.5,
            "jitter": 3.0
        }

    def test_parse_element_args_none(self):
        """Test parsing element args when none provided."""
        cli = PoseStreamCLI()
        
        args = MagicMock()
        args.amplitude = None
        args.speed = None
        args.smooth = None
        args.jitter = None
        
        result = cli._parse_element_args(args)
        
        assert result == {}

    def test_create_stream_client(self):
        """Test create_stream_client returns PoseStreamClient."""
        cli = PoseStreamCLI()
        client = cli.create_stream_client()
        
        assert isinstance(client, PoseStreamClient)

    def test_create_http_client(self):
        """Test _create_http_client returns correct StreamClient."""
        cli = PoseStreamCLI()
        http_client = cli._create_http_client()
        
        from reflexd.streams.base.client import StreamClient
        assert isinstance(http_client, StreamClient)
        assert http_client.base_url == cli.http_url
        assert http_client.element_name == "pose"
        assert http_client.intensity_name == "smoothing"

    @pytest.mark.asyncio
    async def test_cmd_element_list(self):
        """Test _cmd_element lists elements when no name given."""
        cli = PoseStreamCLI()
        
        args = MagicMock()
        args.name = None
        
        mock_client = AsyncMock()
        mock_client.list_elements.return_value = {
            "poses": [
                {"name": "wave", "builtin": True},
                {"name": "nod", "builtin": True},
            ],
            "current": "wave",
            "args": {}
        }
        
        cli._create_http_client = MagicMock()
        cli._create_http_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        cli._create_http_client.return_value.__aexit__ = AsyncMock()
        
        with patch('builtins.print') as mock_print:
            result = await cli._cmd_element(args)
            
            assert result == 0
            mock_client.list_elements.assert_called_once()
            mock_print.assert_called()

    @pytest.mark.asyncio
    async def test_cmd_element_set(self):
        """Test _cmd_element sets element when name given."""
        cli = PoseStreamCLI()
        
        args = MagicMock()
        args.name = "wave"
        args.amplitude = 45.0
        args.speed = 2.0
        args.smooth = None
        args.jitter = None
        
        mock_client = AsyncMock()
        mock_client.set_element.return_value = {"ok": True, "status": {"pose": "wave"}}
        
        cli._create_http_client = MagicMock()
        cli._create_http_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        cli._create_http_client.return_value.__aexit__ = AsyncMock()
        
        with patch('builtins.print') as mock_print:
            result = await cli._cmd_element(args)
            
            assert result == 0
            mock_client.set_element.assert_called_once_with("wave", amplitude=45.0, speed=2.0)
            mock_print.assert_called()

    @pytest.mark.asyncio
    async def test_cmd_fps(self):
        """Test _cmd_fps calls set_fps."""
        cli = PoseStreamCLI()
        
        args = MagicMock()
        args.fps = 30.0
        
        mock_client = AsyncMock()
        mock_client.set_fps.return_value = {"ok": True, "status": {"fps": 30.0}}
        
        cli._create_http_client = MagicMock()
        cli._create_http_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        cli._create_http_client.return_value.__aexit__ = AsyncMock()
        
        with patch('builtins.print'):
            result = await cli._cmd_fps(args)
            
            assert result == 0
            mock_client.set_fps.assert_called_once_with(30.0)

    @pytest.mark.asyncio
    async def test_cmd_smoothing(self):
        """Test _cmd_intensity (smoothing) calls set_intensity."""
        cli = PoseStreamCLI()
        
        args = MagicMock()
        args.smoothing = 0.7
        
        mock_client = AsyncMock()
        mock_client.set_intensity.return_value = {"ok": True, "status": {"smoothing": 0.7}}
        
        cli._create_http_client = MagicMock()
        cli._create_http_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        cli._create_http_client.return_value.__aexit__ = AsyncMock()
        
        with patch('builtins.print'):
            result = await cli._cmd_intensity(args)
            
            assert result == 0
            mock_client.set_intensity.assert_called_once_with(0.7)

    @pytest.mark.asyncio
    async def test_cmd_list(self):
        """Test _cmd_list calls list_elements."""
        cli = PoseStreamCLI()
        
        args = MagicMock()
        
        mock_client = AsyncMock()
        mock_client.list_elements.return_value = {
            "poses": [
                {"name": "wave"},
                {"name": "nod"},
            ],
            "current": "wave"
        }
        
        cli._create_http_client = MagicMock()
        cli._create_http_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        cli._create_http_client.return_value.__aexit__ = AsyncMock()
        
        with patch('builtins.print'):
            result = await cli._cmd_list(args)
            
            assert result == 0
            mock_client.list_elements.assert_called_once()

    @pytest.mark.asyncio
    async def test_cmd_reload(self):
        """Test _cmd_reload calls reload_elements."""
        cli = PoseStreamCLI()
        
        args = MagicMock()
        
        mock_client = AsyncMock()
        mock_client.reload_elements.return_value = {"ok": True, "poses": ["wave", "nod"]}
        
        cli._create_http_client = MagicMock()
        cli._create_http_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        cli._create_http_client.return_value.__aexit__ = AsyncMock()
        
        with patch('builtins.print'):
            result = await cli._cmd_reload(args)
            
            assert result == 0
            mock_client.reload_elements.assert_called_once()

    @pytest.mark.asyncio
    async def test_cmd_start(self):
        """Test _cmd_start calls stream_start."""
        cli = PoseStreamCLI()
        
        args = MagicMock()
        
        mock_stream_client = AsyncMock()
        mock_stream_client.stream_start.return_value = {"result": "started"}
        mock_stream_client.__aenter__ = AsyncMock(return_value=mock_stream_client)
        mock_stream_client.__aexit__ = AsyncMock()
        
        cli.create_stream_client = MagicMock(return_value=mock_stream_client)
        
        with patch('builtins.print'):
            result = await cli._cmd_start(args)
            
            assert result == 0
            mock_stream_client.stream_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_cmd_stop(self):
        """Test _cmd_stop calls stream_stop."""
        cli = PoseStreamCLI()
        
        args = MagicMock()
        
        mock_stream_client = AsyncMock()
        mock_stream_client.stream_stop.return_value = {"result": "stopped"}
        mock_stream_client.__aenter__ = AsyncMock(return_value=mock_stream_client)
        mock_stream_client.__aexit__ = AsyncMock()
        
        cli.create_stream_client = MagicMock(return_value=mock_stream_client)
        
        with patch('builtins.print'):
            result = await cli._cmd_stop(args)
            
            assert result == 0
            mock_stream_client.stream_stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_cmd_status(self):
        """Test _cmd_status calls both stream_status and http status."""
        cli = PoseStreamCLI()
        
        args = MagicMock()
        
        mock_stream_client = AsyncMock()
        mock_stream_client.stream_status.return_value = {"result": {"connected": True}}
        mock_stream_client.__aenter__ = AsyncMock(return_value=mock_stream_client)
        mock_stream_client.__aexit__ = AsyncMock()
        
        mock_http_client = AsyncMock()
        mock_http_client.status.return_value = {"pose": "wave", "fps": 20.0, "smoothing": 0.5}
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock()
        
        cli.create_stream_client = MagicMock(return_value=mock_stream_client)
        cli._create_http_client = MagicMock(return_value=mock_http_client)
        
        with patch('builtins.print'):
            result = await cli._cmd_status(args)
            
            assert result == 0
            mock_stream_client.stream_status.assert_called_once()
            mock_http_client.status.assert_called_once()