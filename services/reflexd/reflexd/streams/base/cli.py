"""
Base StreamCLI - auto-generates subcommands from registry specs.
Subclasses provide stream-specific client for lifecycle commands.
"""

import argparse
import asyncio
import json
import sys
from abc import ABC, abstractmethod
from typing import Any

from .client import StreamClient
from .registry import StreamRegistry, SpecProtocol


class StreamCLI(ABC):
    """
    Template Method CLI base class.
    
    Auto-generates subcommands from registry.specs():
    - element <name> [--args] - set element
    - fps <value> - set FPS
    - intensity <value> - set intensity
    - list - list available elements
    - reload - hot-reload custom elements
    - start/stop/status/monitor - stream lifecycle (delegates to stream-specific client)
    
    Subclass implements:
    - element_name, intensity_name
    - registry
    - http_url
    - create_stream_client() -> stream-specific client with start/stop/status/monitor
    - add_element_args(parser) - add element-specific args (e.g. --color, --tail)
    """
    
    element_name: str = "pattern"
    intensity_name: str = "brightness"
    registry: type[StreamRegistry]
    http_url: str = "http://localhost:8773"
    
    def __init__(self):
        self.parser = self._create_parser()
    
    @abstractmethod
    def create_stream_client(self) -> Any:
        """Create stream-specific client with start/stop/status/monitor methods."""
        pass
    
    def add_element_args(self, parser: argparse.ArgumentParser) -> None:
        """Add element-specific arguments. Override in subclass."""
        pass
    
    def _create_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description=f"{self.element_name.capitalize()} Stream Controller")
        sub = parser.add_subparsers(dest="command", required=True)
        
        # Element command (list or set)
        p_elem = sub.add_parser(self.element_name, help=f"List or set {self.element_name}")
        p_elem.add_argument("name", nargs="?", help=f"{self.element_name.capitalize()} name")
        self.add_element_args(p_elem)
        
        # FPS command
        p_fps = sub.add_parser("fps", help="Set FPS (1-60)")
        p_fps.add_argument("fps", type=float)
        
        # Intensity command
        p_int = sub.add_parser(self.intensity_name, help=f"Set {self.intensity_name} (0.0-1.0)")
        p_int.add_argument(self.intensity_name, type=float)
        
        # List command
        sub.add_parser("list", help=f"List available {self.element_name}s")
        
        # Reload command
        sub.add_parser("reload", help=f"Hot-reload custom {self.element_name}s")
        
        # Lifecycle commands (delegated to stream-specific client)
        sub.add_parser("start", help="Start stream via gateway")
        sub.add_parser("stop", help="Stop stream")
        sub.add_parser("status", help="Show stream and server status")
        p_mon = sub.add_parser("monitor", help="Monitor stream status continuously")
        p_mon.add_argument("-i", "--interval", type=float, default=2.0, help="Poll interval (sec)")
        
        return parser
    
    def _create_http_client(self) -> StreamClient:
        return StreamClient(
            self.http_url,
            element_name=self.element_name,
            intensity_name=self.intensity_name
        )
    
    def _parse_element_args(self, args) -> dict:
        """Parse element-specific args from parsed namespace. Override in subclass."""
        return {}
    
    async def _cmd_element(self, args):
        async with self._create_http_client() as client:
            if not args.name:
                # List elements
                result = await client.list_elements()
                print(f"Available {self.element_name}s:")
                elements_key = f"{self.element_name}s"
                for elem in result.get(elements_key, []):
                    marker = " *" if elem["name"] == result["current"] else ""
                    print(f"  {elem['name']}{marker}")
                print(f"\nCurrent: {result['current']} {result['args']}")
                return 0
            
            # Set element
            element_args = self._parse_element_args(args)
            result = await client.set_element(args.name, **element_args)
            print(f"{self.element_name.capitalize()} set: {json.dumps(result, indent=2)}")
            return 0
    
    async def _cmd_fps(self, args):
        async with self._create_http_client() as client:
            result = await client.set_fps(args.fps)
            print(f"FPS set: {json.dumps(result, indent=2)}")
            return 0
    
    async def _cmd_intensity(self, args):
        async with self._create_http_client() as client:
            intensity_val = getattr(args, self.intensity_name)
            result = await client.set_intensity(intensity_val)
            print(f"{self.intensity_name.capitalize()} set: {json.dumps(result, indent=2)}")
            return 0
    
    async def _cmd_list(self, args):
        async with self._create_http_client() as client:
            result = await client.list_elements()
            elements_key = f"{self.element_name}s"
            for elem in result.get(elements_key, []):
                marker = " *" if elem["name"] == result["current"] else ""
                print(f"  {elem['name']}{marker}")
            return 0
    
    async def _cmd_reload(self, args):
        async with self._create_http_client() as client:
            result = await client.reload_elements()
            print(f"Reloaded: {json.dumps(result, indent=2)}")
            return 0
    
    async def _cmd_start(self, args):
        stream_client = self.create_stream_client()
        async with stream_client:
            result = await stream_client.stream_start()
            print(f"Stream started: {json.dumps(result, indent=2)}")
            return 0
    
    async def _cmd_stop(self, args):
        stream_client = self.create_stream_client()
        async with stream_client:
            result = await stream_client.stream_stop()
            print(f"Stream stopped: {json.dumps(result, indent=2)}")
            return 0
    
    async def _cmd_status(self, args):
        stream_client = self.create_stream_client()
        async with stream_client:
            # Gateway stream status
            result = await stream_client.stream_status()
            print("=== Gateway Stream Status ===")
            print(json.dumps(result, indent=2))
            
            # Server status
            try:
                http_client = self._create_http_client()
                async with http_client:
                    srv = await http_client.status()
                    print(f"\n=== Server Status ===")
                    print(json.dumps(srv, indent=2))
            except Exception as e:
                print(f"\nServer: unavailable ({e})")
            return 0
    
    async def _cmd_monitor(self, args):
        stream_client = self.create_stream_client()
        async with stream_client:
            print("Monitoring... Ctrl+C to stop\n")
            try:
                while True:
                    result = await stream_client.stream_status()
                    try:
                        # Parse nested JSON from MCP response
                        content = result.get("content", [{}])[0].get("text", "{}")
                        status = json.loads(content)
                        
                        print(f"\rConnected: {status.get('connected')} | "
                              f"State: {status.get('connect_state')} | "
                              f"Sent: {status.get('frames_sent')} | "
                              f"Dropped: {status.get('frames_dropped')} | "
                              f"Error: {status.get('last_error') or 'none'}", end="")
                    except:
                        print(f"\rStatus: {result}", end="")
                    
                    await asyncio.sleep(args.interval)
            except KeyboardInterrupt:
                print("\nStopped.")
            return 0
    
    def main(self, argv: list[str] | None = None) -> int:
        """Main entry point."""
        args = self.parser.parse_args(argv)
        
        cmd_map = {
            self.element_name: self._cmd_element,
            "fps": self._cmd_fps,
            self.intensity_name: self._cmd_intensity,
            "list": self._cmd_list,
            "reload": self._cmd_reload,
            "start": self._cmd_start,
            "stop": self._cmd_stop,
            "status": self._cmd_status,
            "monitor": self._cmd_monitor,
        }
        
        cmd_func = cmd_map.get(args.command)
        if not cmd_func:
            self.parser.print_help()
            return 1
        
        try:
            return asyncio.run(cmd_func(args))
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1


def run_cli(cli_class: type[StreamCLI], argv: list[str] | None = None) -> int:
    """Helper to run a CLI class."""
    cli = cli_class()
    return cli.main(argv)