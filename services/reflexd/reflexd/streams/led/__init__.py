"""
LED Stream Package - unified API for LED streaming.
"""

from .cli import main
from .client import LEDStreamClient
from .registry import PATTERN_SPECS, PATTERNS, LEDPatternRegistry, reload_all_patterns
from .server import LEDStreamServer

__all__ = [
    "PATTERNS",
    "PATTERN_SPECS",
    "LEDPatternRegistry",
    "LEDStreamClient",
    "LEDStreamServer",
    "main",  # CLI entrypoint
    "reload_all_patterns",
]
