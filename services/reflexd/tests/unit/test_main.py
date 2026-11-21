import asyncio
import json
import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reflexd.main import (
    JSONFormatter,
    _shutdown_event,
    load_reflexes,
    main,
    setup_logging,
)
from reflexd.registry import clear, list_handlers


@pytest.fixture(autouse=True)
def reset_registry_main():
    clear()
    yield
    clear()


class TestLoadReflexes:
    @pytest.mark.asyncio
    async def test_load_reflexes_loads_modules(self, tmp_path):
        reflexes_dir = tmp_path / "reflexes"
        reflexes_dir.mkdir()

        (reflexes_dir / "test_reflex.py").write_text(
            "from reflexd import on_event\n\n@on_event('test_event')\nasync def handler(data): pass\n"
        )

        # Clear any cached reflexes modules
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith("reflexes."):
                del sys.modules[mod_name]
        if "reflexes" in sys.modules:
            del sys.modules["reflexes"]

        with patch("reflexd.main.REFLEXES_DIR", str(reflexes_dir)):
            await load_reflexes()

        assert "test_event" in list_handlers()

    @pytest.mark.asyncio
    async def test_load_reflexes_skips_underscore(self, tmp_path):
        reflexes_dir = tmp_path / "reflexes"
        reflexes_dir.mkdir()

        (reflexes_dir / "_private.py").write_text(
            "from reflexd import on_event\n\n@on_event('private_event')\nasync def handler(data): pass\n"
        )
        (reflexes_dir / "public.py").write_text(
            "from reflexd import on_event\n\n@on_event('public_event')\nasync def handler(data): pass\n"
        )

        with patch("reflexd.main.REFLEXES_DIR", str(reflexes_dir)):
            await load_reflexes()

        assert "public_event" in list_handlers()
        assert "private_event" not in list_handlers()

    @pytest.mark.asyncio
    async def test_load_reflexes_handles_import_error(self, tmp_path, caplog):
        reflexes_dir = tmp_path / "reflexes"
        reflexes_dir.mkdir()

        (reflexes_dir / "bad_reflex.py").write_text("import nonexistent_module\n")

        with (
            patch("reflexd.main.REFLEXES_DIR", str(reflexes_dir)),
            caplog.at_level(logging.ERROR),
        ):
            await load_reflexes()

        assert "Error loading reflex bad_reflex" in caplog.text

    @pytest.mark.asyncio
    async def test_load_reflexes_warns_on_missing_directory(self, tmp_path, caplog):
        nonexistent_dir = tmp_path / "nonexistent"

        with (
            patch("reflexd.main.REFLEXES_DIR", str(nonexistent_dir)),
            caplog.at_level(logging.WARNING),
        ):
            await load_reflexes()

        assert "Reflexes directory not found" in caplog.text


class TestJSONFormatter:
    def test_format_basic_log_record(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        data = json.loads(result)
        assert data["level"] == "INFO"
        assert data["logger"] == "test_logger"
        assert data["message"] == "Test message"
        assert "timestamp" in data
        assert "exception" not in data

    def test_format_with_exception(self):
        formatter = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test_logger",
            level=logging.ERROR,
            pathname="test.py",
            lineno=10,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )
        result = formatter.format(record)
        data = json.loads(result)
        assert data["level"] == "ERROR"
        assert "exception" in data
        assert "ValueError: test error" in data["exception"]


class TestSetupLogging:
    def test_setup_logging_text_format(self):
        with (
            patch.dict("os.environ", {"LOG_FORMAT": "text"}, clear=True),
            patch("logging.basicConfig") as mock_basic,
        ):
            setup_logging()
            mock_basic.assert_called_once()
            _args, kwargs = mock_basic.call_args
            assert kwargs["level"] == logging.INFO
            assert len(kwargs["handlers"]) == 1

    def test_setup_logging_json_format(self):
        with (
            patch.dict("os.environ", {"LOG_FORMAT": "json"}, clear=True),
            patch("logging.basicConfig") as mock_basic,
        ):
            setup_logging()
            mock_basic.assert_called_once()
            _args, kwargs = mock_basic.call_args
            assert kwargs["level"] == logging.INFO
            assert len(kwargs["handlers"]) == 1
            handler = kwargs["handlers"][0]
            assert isinstance(handler.formatter, JSONFormatter)


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_cancels_running_tasks(self):
        from reflexd.main import shutdown
        from reflexd.server import _running

        mock_client = AsyncMock()
        mock_runner = AsyncMock()

        task1 = asyncio.create_task(asyncio.sleep(10))
        task2 = asyncio.create_task(asyncio.sleep(10))
        _running["task1"] = {"task": task1, "priority": 0, "conflict_group": None}
        _running["task2"] = {"task": task2, "priority": 0, "conflict_group": None}

        await shutdown(mock_client, mock_runner)

        mock_client.close.assert_called_once()
        mock_runner.cleanup.assert_called_once()
        assert _shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_main_signal_handler_not_implemented(self):
        mock_client = AsyncMock()
        mock_client.connect_with_retry = AsyncMock()
        mock_client.close = AsyncMock()

        mock_runner = AsyncMock()
        mock_runner.setup = AsyncMock()
        mock_runner.cleanup = AsyncMock()

        mock_site = AsyncMock()
        mock_site.start = AsyncMock()

        mock_app = MagicMock()

        with (
            patch("reflexd.main.STACKCHAN_TOKEN", "test-token"),
            patch("reflexd.main.GatewayClient", return_value=mock_client),
            patch("reflexd.main.create_app", return_value=mock_app),
            patch("aiohttp.web.AppRunner", return_value=mock_runner),
            patch("aiohttp.web.TCPSite", return_value=mock_site),
            patch("asyncio.get_running_loop") as mock_get_loop,
            patch.object(_shutdown_event, "wait", side_effect=KeyboardInterrupt()),
        ):
            mock_loop = MagicMock()
            mock_loop.add_signal_handler.side_effect = NotImplementedError()
            mock_get_loop.return_value = mock_loop

            await main()

            mock_client.connect_with_retry.assert_called_once()


class TestMain:
    @pytest.mark.asyncio
    async def test_main_exits_when_token_not_set(self, caplog):
        with patch("reflexd.main.STACKCHAN_TOKEN", ""):
            with pytest.raises(SystemExit) as exc_info:
                await main()
            assert exc_info.value.code == 1

        assert "STACKCHAN_TOKEN not set in .env" in caplog.text

    @pytest.mark.asyncio
    async def test_main_connects_gateway_and_starts_server(self):
        mock_client = AsyncMock()
        mock_client.connect_with_retry = AsyncMock()
        mock_client.close = AsyncMock()

        mock_runner = AsyncMock()
        mock_runner.setup = AsyncMock()
        mock_runner.cleanup = AsyncMock()

        mock_site = AsyncMock()
        mock_site.start = AsyncMock()

        mock_app = MagicMock()

        with (
            patch("reflexd.main.STACKCHAN_TOKEN", "test-token"),
            patch("reflexd.main.GatewayClient", return_value=mock_client),
            patch("reflexd.main.create_app", return_value=mock_app),
            patch("aiohttp.web.AppRunner", return_value=mock_runner),
            patch("aiohttp.web.TCPSite", return_value=mock_site),
            patch.object(_shutdown_event, "wait", side_effect=KeyboardInterrupt()),
        ):
            await main()

        mock_client.connect_with_retry.assert_called_once()
        mock_runner.setup.assert_called_once()
        mock_site.start.assert_called_once()
        mock_client.close.assert_called_once()
        mock_runner.cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_main_exits_on_gateway_connection_failure(self, caplog):
        mock_client = AsyncMock()
        mock_client.connect_with_retry.side_effect = RuntimeError(
            "Failed to connect to gateway after 5 attempts: Connection failed"
        )

        with (
            patch("reflexd.main.STACKCHAN_TOKEN", "test-token"),
            patch("reflexd.main.GatewayClient", return_value=mock_client),
            pytest.raises(SystemExit) as exc_info,
        ):
            await main()

        assert exc_info.value.code == 1
        assert "Failed to connect to gateway" in caplog.text
