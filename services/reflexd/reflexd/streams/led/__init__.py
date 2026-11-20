"""
LED Stream Package - unified API for LED streaming.
"""

from .server import LEDStreamServer
from .registry import LEDPatternRegistry, PATTERNS, PATTERN_SPECS, reload_all_patterns
from .client import LEDStreamClient
from .cli import main

__all__ = [
    "LEDStreamServer",
    "LEDPatternRegistry",
    "PATTERNS",
    "PATTERN_SPECS",
    "reload_all_patterns",
    "LEDStreamClient",
    "main",  # CLI entrypoint
]