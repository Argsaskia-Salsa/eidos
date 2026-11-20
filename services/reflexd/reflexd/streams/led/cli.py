"""
LED CLI - extends StreamCLI with LED-specific args and MCP client.
"""

import argparse
import json
from typing import Any

from ..base.cli import StreamCLI, run_cli
from ..base.registry import SpecProtocol
from .client import LEDStreamClient
from .registry import LEDPatternRegistry
from .config import CONFIG


class LEDStreamCLI(StreamCLI):
    """LED Stream CLI with pattern-specific args and MCP integration."""
    
    element_name = "pattern"
    intensity_name = "brightness"
    registry = LEDPatternRegistry
    http_url = CONFIG.HTTP_SERVER_URL
    
    def create_stream_client(self) -> LEDStreamClient:
        return LEDStreamClient()
    
    def add_element_args(self, parser: argparse.ArgumentParser) -> None:
        """Add LED pattern-specific arguments."""
        parser.add_argument("--color", nargs=3, type=int, metavar=("R", "G", "B"), help="Color for solid/kitt/breathing")
        parser.add_argument("--tail", type=float, help="Tail fade for kitt")
        parser.add_argument("--freq", type=float, help="Frequency for pulse pattern")
    
    def _parse_element_args(self, args) -> dict:
        pattern_args = {}
        if args.color:
            pattern_args["color"] = args.color
        if args.tail is not None:
            pattern_args["tail"] = args.tail
        if args.freq is not None:
            pattern_args["freq"] = args.freq
        return pattern_args


def main(argv: list[str] | None = None) -> int:
    """Entry point for python -m reflexd.streams.led"""
    cli = LEDStreamCLI()
    return cli.main(argv)


if __name__ == "__main__":
    import sys
    sys.exit(main())