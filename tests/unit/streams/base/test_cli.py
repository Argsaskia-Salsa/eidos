"""
Unit tests for StreamCLI base class (auto-generates subcommands from registry).
"""

import argparse
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reflexd.streams.base.registry import StreamRegistry, SpecProtocol
from reflexd.streams.base.cli import StreamCLI, run_cli
from reflexd.streams.base.client import StreamClient


class DummySpec:
    def __init__(self, name: str, func, args: dict = None, description: str = ""):
        self.name = name
        self.func = func
        self.args = args or {}
        self.description = description


def dummy_func(): return []


class TestRegistry(StreamRegistry):
    custom_dir = __import__("pathlib").Path("/nonexistent")
    module_prefix = "test"
    spec_attr = "PATTERN"
    _BUILTIN_SPECS = {
        "pattern_a": DummySpec("pattern_a", dummy_func, {}, "Pattern A"),
        "pattern_b": DummySpec("pattern_b", dummy_func, {"param": 1}, "Pattern B"),
    }


class MockStreamClient:
    """Mock stream client with lifecycle methods."""
    def __init__(self):
        self.start_called = False
        self.stop_called = False
        self.status_called = False
        self.monitor_called = False
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        pass
    
    async def stream_start(self):
        self.start_called = True
        return {"started": True}
    
    async def stream_stop(self):
        self.stop_called = True
        return {"stopped": True}
    
    async def stream_status(self):
        self.status_called = True
        self.monitor_called = True
        return {"connected": True, "state": "running"}


class TestCLI(StreamCLI):
    element_name = "pattern"
    intensity_name = "brightness"
    registry = TestRegistry
    http_url = "http://localhost:8773"
    
    def create_stream_client(self):
        return MockStreamClient()
    
    def add_element_args(self, parser):
        parser.add_argument("--color", nargs=3, type=int, metavar=("R", "G", "B"))
    
    def _parse_element_args(self, args):
        pattern_args = {}
        if args.color:
            pattern_args["color"] = args.color
        return pattern_args


@pytest.fixture
def cli():
    TestRegistry.reload_all()
    return TestCLI()


def test_cli_creates_parser_with_all_subcommands(cli):
    """Test parser has all expected subcommands."""
    subcommands = [action.dest for action in cli.parser._subparsers._group_actions[0]._choices_actions]
    
    expected = ["pattern", "fps", "brightness", "list", "reload", "start", "stop", "status", "monitor"]
    for cmd in expected:
        assert cmd in subcommands, f"Missing subcommand: {cmd}"


def test_cli_pattern_subcommand_has_name_arg(cli):
    """Test pattern subcommand has optional name argument."""
    pattern_parser = cli.parser._subparsers._group_actions[0]._choices_actions[0]
    assert pattern_parser is not None
    
    # Find the pattern subparser
    choices = cli.parser._subparsers._group_actions[0].choices
    pattern_parser = choices["pattern"]
    
    # Check it has 'name' as optional positional
    args = [a for a in pattern_parser._actions if a.dest == "name"]
    assert len(args) == 1
    assert args[0].nargs == "?"


def test_cli_pattern_subcommand_has_custom_args(cli):
    """Test pattern subcommand has custom args (--color)."""
    choices = cli.parser._subparsers._group_actions[0].choices
    pattern_parser = choices["pattern"]
    
    color_arg = [a for a in pattern_parser._actions if a.dest == "color"]
    assert len(color_arg) == 1
    assert color_arg[0].nargs == 3
    assert color_arg[0].type == int


def test_cli_fps_subcommand(cli):
    """Test fps subcommand has fps argument."""
    choices = cli.parser._subparsers._group_actions[0].choices
    fps_parser = choices["fps"]
    
    fps_arg = [a for a in fps_parser._actions if a.dest == "fps"]
    assert len(fps_arg) == 1
    assert fps_arg[0].type == float


def test_cli_intensity_subcommand(cli):
    """Test intensity subcommand has intensity argument."""
    choices = cli.parser._subparsers._group_actions[0].choices
    int_parser = choices["brightness"]
    
    int_arg = [a for a in int_parser._actions if a.dest == "brightness"]
    assert len(int_arg) == 1
    assert int_arg[0].type == float


def test_cli_list_and_reload_subcommands(cli):
    """Test list and reload subcommands exist."""
    choices = cli.parser._subparsers._group_actions[0].choices
    assert "list" in choices
    assert "reload" in choices


def test_cli_lifecycle_subcommands(cli):
    """Test start, stop, status, monitor subcommands exist."""
    choices = cli.parser._subparsers._group_actions[0].choices
    assert "start" in choices
    assert "stop" in choices
    assert "status" in choices
    assert "monitor" in choices


def test_cli_monitor_has_interval_arg(cli):
    """Test monitor subcommand has --interval argument."""
    choices = cli.parser._subparsers._group_actions[0].choices
    monitor_parser = choices["monitor"]
    
    interval_arg = [a for a in monitor_parser._actions if a.dest == "interval"]
    assert len(interval_arg) == 1
    assert interval_arg[0].type == float
    assert interval_arg[0].default == 2.0


@pytest.mark.asyncio
async def test_cmd_element_list(cli):
    """Test pattern command without name lists patterns."""
    with patch.object(StreamClient, '__aenter__', new_callable=AsyncMock) as mock_enter:
        mock_client = AsyncMock()
        mock_client.list_elements = AsyncMock(return_value={
            "patterns": [
                {"name": "pattern_a", "args": {}, "description": "Pattern A", "builtin": True},
                {"name": "pattern_b", "args": {"param": 1}, "description": "Pattern B", "builtin": True},
            ],
            "current": "pattern_a",
            "args": {}
        })
        mock_enter.return_value = mock_client
        
        # Capture print output
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            await cli._cmd_element(argparse.Namespace(name=None))
        
        output = f.getvalue()
        assert "pattern_a" in output
        assert "pattern_b" in output
        assert "Current: pattern_a" in output


@pytest.mark.asyncio
async def test_cmd_element_set(cli):
    """Test pattern command with name sets pattern."""
    with patch.object(StreamClient, '__aenter__', new_callable=AsyncMock) as mock_enter:
        mock_client = AsyncMock()
        mock_client.set_element = AsyncMock(return_value={"ok": True, "status": {"pattern": "pattern_b"}})
        mock_enter.return_value = mock_client
        
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            await cli._cmd_element(argparse.Namespace(name="pattern_b", color=None))
        
        mock_client.set_element.assert_called_once_with("pattern_b", **{})
        output = f.getvalue()
        assert "Pattern set" in output


@pytest.mark.asyncio
async def test_cmd_element_set_with_args(cli):
    """Test pattern command with custom args."""
    with patch.object(StreamClient, '__aenter__', new_callable=AsyncMock) as mock_enter:
        mock_client = AsyncMock()
        mock_client.set_element = AsyncMock(return_value={"ok": True})
        mock_enter.return_value = mock_client
        
        args = argparse.Namespace(name="pattern_b", color=[255, 0, 0])
        await cli._cmd_element(args)
        
        mock_client.set_element.assert_called_once_with("pattern_b", color=[255, 0, 0])


@pytest.mark.asyncio
async def test_cmd_fps(cli):
    """Test fps command."""
    with patch.object(StreamClient, '__aenter__', new_callable=AsyncMock) as mock_enter:
        mock_client = AsyncMock()
        mock_client.set_fps = AsyncMock(return_value={"ok": True})
        mock_enter.return_value = mock_client
        
        await cli._cmd_fps(argparse.Namespace(fps=30.0))
        
        mock_client.set_fps.assert_called_once_with(30.0)


@pytest.mark.asyncio
async def test_cmd_intensity(cli):
    """Test intensity command."""
    with patch.object(StreamClient, '__aenter__', new_callable=AsyncMock) as mock_enter:
        mock_client = AsyncMock()
        mock_client.set_intensity = AsyncMock(return_value={"ok": True})
        mock_enter.return_value = mock_client
        
        await cli._cmd_intensity(argparse.Namespace(brightness=0.5))
        
        mock_client.set_intensity.assert_called_once_with(0.5)


@pytest.mark.asyncio
async def test_cmd_list(cli):
    """Test list command."""
    with patch.object(StreamClient, '__aenter__', new_callable=AsyncMock) as mock_enter:
        mock_client = AsyncMock()
        mock_client.list_elements = AsyncMock(return_value={
            "patterns": [
                {"name": "pattern_a"},
                {"name": "pattern_b"},
            ],
            "current": "pattern_a"
        })
        mock_enter.return_value = mock_client
        
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            await cli._cmd_list(argparse.Namespace())
        
        output = f.getvalue()
        assert "pattern_a" in output
        assert "pattern_b" in output


@pytest.mark.asyncio
async def test_cmd_reload(cli):
    """Test reload command."""
    with patch.object(StreamClient, '__aenter__', new_callable=AsyncMock) as mock_enter:
        mock_client = AsyncMock()
        mock_client.reload_elements = AsyncMock(return_value={"ok": True, "patterns": ["pattern_a", "pattern_b"]})
        mock_enter.return_value = mock_client
        
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            await cli._cmd_reload(argparse.Namespace())
        
        output = f.getvalue()
        assert "Reloaded" in output


@pytest.mark.asyncio
async def test_cmd_start(cli):
    """Test start command delegates to stream client."""
    mock_stream = MockStreamClient()
    cli.create_stream_client = MagicMock(return_value=mock_stream)
    
    with patch.object(StreamClient, '__aenter__', new_callable=AsyncMock):
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            await cli._cmd_start(argparse.Namespace())
    
    assert mock_stream.start_called


@pytest.mark.asyncio
async def test_cmd_stop(cli):
    """Test stop command delegates to stream client."""
    mock_stream = MockStreamClient()
    cli.create_stream_client = MagicMock(return_value=mock_stream)
    
    with patch.object(StreamClient, '__aenter__', new_callable=AsyncMock):
        await cli._cmd_stop(argparse.Namespace())
    
    assert mock_stream.stop_called


@pytest.mark.asyncio
async def test_cmd_status(cli):
    """Test status command shows both gateway and server status."""
    mock_stream = MockStreamClient()
    cli.create_stream_client = MagicMock(return_value=mock_stream)
    
    # Mock _create_http_client to return a mocked StreamClient
    mock_http_client = AsyncMock()
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=None)
    mock_http_client.status = AsyncMock(return_value={"pattern": "pattern_a", "fps": 20})
    
    cli._create_http_client = MagicMock(return_value=mock_http_client)
    
    import io
    from contextlib import redirect_stdout
    f = io.StringIO()
    with redirect_stdout(f):
        await cli._cmd_status(argparse.Namespace())
    
    output = f.getvalue()
    assert "Gateway Stream Status" in output
    assert "Server Status" in output
    assert mock_stream.status_called


@pytest.mark.asyncio
async def test_cmd_monitor(cli):
    """Test monitor command polls status."""
    mock_stream = MockStreamClient()
    cli.create_stream_client = MagicMock(return_value=mock_stream)
    
    # Mock asyncio.sleep to stop after one iteration
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.side_effect = [None, KeyboardInterrupt()]
        
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            try:
                await cli._cmd_monitor(argparse.Namespace(interval=1.0))
            except KeyboardInterrupt:
                pass
        
        assert mock_stream.monitor_called
        mock_sleep.assert_called()


def test_run_cli_helper():
    """Test run_cli helper function."""
    with patch.object(TestCLI, 'main', return_value=0) as mock_main:
        result = run_cli(TestCLI, ["pattern", "pattern_a"])
        assert result == 0
        mock_main.assert_called_once_with(["pattern", "pattern_a"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])