import asyncio
import importlib
import json
import logging
import os
import signal
import sys
from pathlib import Path

from aiohttp import web

from .gateway_client import GatewayClient, set_gateway_client
from .registry import clear
from .server import _running, create_app

GATEWAY_MCP_URL = os.getenv("GATEWAY_MCP_URL", "http://127.0.0.1:8767/mcp")
STACKCHAN_TOKEN = os.getenv("STACKCHAN_TOKEN", "")
REFLEXD_MCP_PORT = int(os.getenv("REFLEXD_MCP_PORT", "8770"))
REFLEXES_DIR = os.getenv("REFLEXES_DIR", str(Path(__file__).parent.parent / "reflexes"))

_shutdown_event = asyncio.Event()

logger = logging.getLogger(__name__)


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


def setup_logging() -> None:
    log_format = os.getenv("LOG_FORMAT", "text")
    level = logging.INFO

    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )

    logging.basicConfig(level=level, handlers=[handler])


async def load_reflexes() -> None:
    clear()
    reflexes_path = Path(REFLEXES_DIR).expanduser()
    if not reflexes_path.exists():
        logger.warning("Reflexes directory not found: %s", REFLEXES_DIR)
        return

    sys.path.insert(0, str(reflexes_path.parent))

    for py_file in reflexes_path.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        module_name = py_file.stem
        full_module_name = f"reflexes.{module_name}"
        try:
            if full_module_name in sys.modules:
                del sys.modules[full_module_name]
            importlib.import_module(full_module_name)
            logger.info("Loaded reflex: %s", module_name)
        except (ImportError, ModuleNotFoundError, SyntaxError) as e:
            logger.error("Error loading reflex %s: %s", module_name, e)


async def shutdown(client: GatewayClient, runner: web.AppRunner) -> None:
    logger.info("Shutting down...")
    running_entries = list(_running.values())
    for entry in running_entries:
        entry["task"].cancel()
        try:
            await entry["task"]
        except asyncio.CancelledError:
            pass
    await client.close()
    await runner.cleanup()
    _shutdown_event.set()


async def main() -> None:
    setup_logging()

    if not STACKCHAN_TOKEN:
        logger.error("STACKCHAN_TOKEN not set in .env")
        sys.exit(1)

    await load_reflexes()

    client = GatewayClient(GATEWAY_MCP_URL, STACKCHAN_TOKEN)
    try:
        await client.connect_with_retry()
    except RuntimeError as e:
        logger.error("Failed to connect to gateway: %s", e)
        sys.exit(1)

    set_gateway_client(client)

    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(
        runner, "0.0.0.0", REFLEXD_MCP_PORT, reuse_address=True, reuse_port=True
    )
    await site.start()
    logger.info("Reflexd MCP server running on port %d", REFLEXD_MCP_PORT)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(
                sig, lambda: asyncio.create_task(shutdown(client, runner))
            )
        except NotImplementedError:
            pass

    try:
        await _shutdown_event.wait()
    except KeyboardInterrupt:
        await shutdown(client, runner)


if __name__ == "__main__":
    asyncio.run(main())
