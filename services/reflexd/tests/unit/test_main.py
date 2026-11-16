import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reflexd.main import load_reflexes, main
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
    async def test_load_reflexes_handles_import_error(self, tmp_path, capsys):
        reflexes_dir = tmp_path / "reflexes"
        reflexes_dir.mkdir()

        (reflexes_dir / "bad_reflex.py").write_text("import nonexistent_module\n")

        with patch("reflexd.main.REFLEXES_DIR", str(reflexes_dir)):
            await load_reflexes()

        captured = capsys.readouterr()
        assert "Error loading reflex bad_reflex" in captured.out

    @pytest.mark.asyncio
    async def test_load_reflexes_warns_on_missing_directory(self, tmp_path, capsys):
        nonexistent_dir = tmp_path / "nonexistent"

        with patch("reflexd.main.REFLEXES_DIR", str(nonexistent_dir)):
            await load_reflexes()

        captured = capsys.readouterr()
        assert "Warning: Reflexes directory not found" in captured.out


class TestMain:
    @pytest.mark.asyncio
    async def test_main_exits_when_token_not_set(self, capsys):
        with patch("reflexd.main.STACKCHAN_TOKEN", ""):
            with pytest.raises(SystemExit) as exc_info:
                await main()
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "Error: STACKCHAN_TOKEN not set in .env" in captured.out

    @pytest.mark.asyncio
    async def test_main_connects_gateway_and_starts_server(self):
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.close = AsyncMock()

        mock_runner = AsyncMock()
        mock_runner.setup = AsyncMock()
        mock_runner.cleanup = AsyncMock()

        mock_site = AsyncMock()
        mock_site.start = AsyncMock()

        mock_app = MagicMock()

        with patch("reflexd.main.STACKCHAN_TOKEN", "test-token"), \
             patch("reflexd.main.GatewayClient", return_value=mock_client), \
             patch("reflexd.main.create_app", return_value=mock_app), \
             patch("aiohttp.web.AppRunner", return_value=mock_runner), \
             patch("aiohttp.web.TCPSite", return_value=mock_site), \
             patch("asyncio.Event.wait", side_effect=KeyboardInterrupt()):

            await main()

        mock_client.connect.assert_called_once()
        mock_runner.setup.assert_called_once()
        mock_site.start.assert_called_once()
        mock_client.close.assert_called_once()
        mock_runner.cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_main_exits_on_gateway_connection_failure(self, capsys):
        mock_client = AsyncMock()
        mock_client.connect.side_effect = OSError("Connection failed")

        with patch("reflexd.main.STACKCHAN_TOKEN", "test-token"), \
             patch("reflexd.main.GatewayClient", return_value=mock_client), \
             pytest.raises(SystemExit) as exc_info:
            await main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Failed to connect to gateway: Connection failed" in captured.out