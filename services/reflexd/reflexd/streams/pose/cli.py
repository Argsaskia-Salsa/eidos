"""
Pose CLI - extends StreamCLI with pose-specific args and MCP client.
"""

import argparse

from ..base.cli import StreamCLI
from .client import PoseStreamClient
from .config import CONFIG
from .registry import PoseRegistry


class PoseStreamCLI(StreamCLI):
    """Pose Stream CLI with pose-specific args and MCP integration."""

    element_name = "pose"
    intensity_name = "smoothing"
    registry = PoseRegistry
    http_url = CONFIG.HTTP_SERVER_URL

    def create_stream_client(self) -> PoseStreamClient:
        return PoseStreamClient()

    def add_element_args(self, parser: argparse.ArgumentParser) -> None:
        """Add pose-specific arguments."""
        parser.add_argument("--amplitude", type=float, help="Amplitude for wave/nod")
        parser.add_argument("--speed", type=float, help="Speed for wave/nod")
        parser.add_argument("--smooth", type=float, help="Smooth factor for track")
        parser.add_argument("--jitter", type=float, help="Jitter amount for idle")

    def _parse_element_args(self, args) -> dict:
        pattern_args = {}
        if args.amplitude is not None:
            pattern_args["amplitude"] = args.amplitude
        if args.speed is not None:
            pattern_args["speed"] = args.speed
        if args.smooth is not None:
            pattern_args["smooth"] = args.smooth
        if args.jitter is not None:
            pattern_args["jitter"] = args.jitter
        return pattern_args


def main(argv: list[str] | None = None) -> int:
    """Entry point for python -m reflexd.streams.pose"""
    cli = PoseStreamCLI()
    return cli.main(argv)


if __name__ == "__main__":
    import sys
    sys.exit(main())