"""
LED Pattern Registry - extends StreamRegistry for LED patterns.
"""

from pathlib import Path
from typing import Callable

from ..base.registry import StreamRegistry, SpecProtocol
from .builtins import BUILTIN_PATTERNS, LEDPatternSpec
from .config import CONFIG


class LEDPatternRegistry(StreamRegistry):
    """Registry for LED patterns with builtins and hot-reloadable customs."""
    
    custom_dir = Path(__file__).parent / "patterns" / "custom"
    module_prefix = "reflexd.streams.led.patterns.custom"
    spec_attr = "PATTERN"
    
    # Type hints for LED-specific usage
    ELEMENTS: dict[str, Callable[..., list[list[list[int]]]]] = {}
    ELEMENT_SPECS: dict[str, SpecProtocol] = {}
    
    # Register built-in patterns
    _BUILTIN_SPECS: dict[str, SpecProtocol] = BUILTIN_PATTERNS


# Initialize registry on import
LEDPatternRegistry.reload_all()

# Public exports for backward compatibility
PATTERNS = LEDPatternRegistry.ELEMENTS
PATTERN_SPECS = LEDPatternRegistry.ELEMENT_SPECS
reload_all_patterns = LEDPatternRegistry.reload_all
NUM_LEDS = CONFIG.NUM_LEDS