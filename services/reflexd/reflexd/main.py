import asyncio
import importlib
import os
import sys
from pathlib import Path

import aiohttp
from aiohttp import web

from .gateway_client import GatewayClient, set_gateway_client
from .registry import clear
from .server import create_app

GATEWAY_MCP_URL = os.getenv("GATEWAY_MCP_URL", "http://127.0.0.1:8767/mcp")
STACKCHAN_TOKEN = os.getenv("STACKCHAN_TOKEN", "")
REFLEXD_MCP_PORT = int(os.getenv("REFLEXD_MCP_PORT", "8770"))
REFLEXES_DIR = os.getenv("REFLEXES_DIR", str(Path(__file__).parent.parent / "reflexes"))


async def load_reflexes() -> None:
    clear()
    reflexes_path = Path(REFLEXES_DIR).expanduser()
    if not reflexes_path.exists():
        print(f"Warning: Reflexes directory not found: {REFLEXES_DIR}")
        return

    sys.path.insert(0, str(reflexes_path.parent))

    for py_file in reflexes_path.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        module_name = py_file.stem
        try:
            importlib.import_module(f"reflexes.{module_name}")
            print(f"Loaded reflex: {module_name}")
        except (ImportError, ModuleNotFoundError, SyntaxError) as e:
            print(f"Error loading reflex {module_name}: {e}")


async def main() -> None:
    if not STACKCHAN_TOKEN:
        print("Error: STACKCHAN_TOKEN not set in .env")
        sys.exit(1)

    await load_reflexes()

    client = GatewayClient(GATEWAY_MCP_URL, STACKCHAN_TOKEN)
    try:
        await client.connect()
        print(f"Connected to gateway at {GATEWAY_MCP_URL}")
    except (aiohttp.ClientError, OSError, TimeoutError) as e:
        print(f"Failed to connect to gateway: {e}")
        sys.exit(1)

    set_gateway_client(client)

    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", REFLEXD_MCP_PORT)
    await site.start()
    print(f"Reflexd MCP server running on port {REFLEXD_MCP_PORT}")

    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        await client.close()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
