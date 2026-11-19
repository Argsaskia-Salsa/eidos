"""
Pattern registry - shared state for LED Stream Server.
This module ensures patterns are loaded only once.
"""

import importlib
import os
import sys
from pathlib import Path
from typing import Callable, Optional


# Configuration
NUM_LEDS = 12


class PatternSpec:
    """Pattern specification with metadata for CLI generation."""
    def __init__(self, name: str, func: Callable, args: Optional[dict] = None, description: str = ""):
        self.name = name
        self.func = func
        self.args = args or {}
        self.description = description


# ===== BUILTIN PATTERNS =====

def pattern_cycle(num_leds: int = NUM_LEDS) -> list[list[list[int]]]:
    """Sequential 1→2→...→N→1..."""
    patterns = []
    for i in range(num_leds):
        colors = [[0, 0, 0] for _ in range(num_leds)]
        colors[i] = [255, 255, 255]
        patterns.append(colors)
    return patterns


def pattern_rainbow(num_leds: int = NUM_LEDS) -> list[list[list[int]]]:
    """Flowing rainbow wave."""
    import math
    patterns = []
    frames = 60
    for frame in range(frames):
        colors = []
        for i in range(num_leds):
            hue = (frame / frames + i / num_leds) % 1.0
            h = hue * 6.0
            x = int(h)
            f = h - x
            p = 1.0 * (1 - 1.0)
            q = 1.0 * (1 - 1.0 * f)
            t = 1.0 * (1 - 1.0 * (1 - f))
            if x == 0:
                r, g, b = 1.0, t, p
            elif x == 1:
                r, g, b = q, 1.0, p
            elif x == 2:
                r, g, b = p, 1.0, t
            elif x == 3:
                r, g, b = p, q, 1.0
            elif x == 4:
                r, g, b = t, p, 1.0
            else:
                r, g, b = 1.0, p, q
            colors.append([int(r * 255), int(g * 255), int(b * 255)])
        patterns.append(colors)
    return patterns


def pattern_breathing(num_leds: int = NUM_LEDS, color: list[int] = [0, 180, 255]) -> list[list[list[int]]]:
    """All LEDs breathe together."""
    import math
    patterns = []
    frames = 60
    for frame in range(frames):
        brightness = (math.sin(frame * 2 * math.pi / frames) + 1) / 2  # 0..1
        colors = []
        for _ in range(num_leds):
            colors.append([int(c * brightness) for c in color])
        patterns.append(colors)
    return patterns


def pattern_kitt(num_leds: int = NUM_LEDS, color: list[int] = [255, 0, 0], tail: float = 0.5) -> list[list[list[int]]]:
    """KITT scanner back and forth."""
    patterns = []
    # Forward
    for pos in range(num_leds):
        colors = [[0, 0, 0] for _ in range(num_leds)]
        for i in range(num_leds):
            dist = abs(i - pos)
            if dist == 0:
                colors[i] = color[:]
            elif dist <= 3:
                fade = tail ** dist
                colors[i] = [int(c * fade) for c in color]
        patterns.append(colors)
    # Backward
    for pos in range(num_leds - 2, 0, -1):
        colors = [[0, 0, 0] for _ in range(num_leds)]
        for i in range(num_leds):
            dist = abs(i - pos)
            if dist == 0:
                colors[i] = color[:]
            elif dist <= 3:
                fade = tail ** dist
                colors[i] = [int(c * fade) for c in color]
        patterns.append(colors)
    return patterns


def pattern_solid(num_leds: int = NUM_LEDS, color: list[int] = [255, 255, 255]) -> list[list[list[int]]]:
    """Single solid color frame."""
    return [[color[:] for _ in range(num_leds)]]


def pattern_off(num_leds: int = NUM_LEDS) -> list[list[list[int]]]:
    """All off."""
    return [[[0, 0, 0] for _ in range(num_leds)]]


# ===== PATTERN REGISTRY (Singleton) =====

# Built-in patterns with metadata
_BUILTIN_PATTERNS: dict[str, PatternSpec] = {
    "cycle": PatternSpec("cycle", pattern_cycle, {}, "Sequential 1→2→...→N"),
    "rainbow": PatternSpec("rainbow", pattern_rainbow, {}, "Flowing rainbow wave"),
    "breathing": PatternSpec("breathing", pattern_breathing, {"color": [0, 180, 255]}, "All LEDs breathe together"),
    "kitt": PatternSpec("kitt", pattern_kitt, {"color": [255, 0, 0], "tail": 0.5}, "KITT scanner back and forth"),
    "solid": PatternSpec("solid", pattern_solid, {"color": [255, 255, 255]}, "Single solid color"),
    "off": PatternSpec("off", pattern_off, {}, "All LEDs off"),
}

# Runtime pattern registry - THE SINGLE SOURCE OF TRUTH
PATTERNS: dict[str, Callable] = {}
PATTERN_SPECS: dict[str, PatternSpec] = {}


def _load_builtin_patterns():
    """Load built-in patterns into runtime registry."""
    global PATTERNS, PATTERN_SPECS
    PATTERNS = {name: spec.func for name, spec in _BUILTIN_PATTERNS.items()}
    PATTERN_SPECS = dict(_BUILTIN_PATTERNS)


def _load_custom_patterns() -> dict[str, PatternSpec]:
    """Load custom patterns from patterns/custom/ directory."""
    custom = {}
    PATTERNS_DIR = Path(__file__).parent / "patterns"
    CUSTOM_PATTERNS_DIR = PATTERNS_DIR / "custom"
    
    if not CUSTOM_PATTERNS_DIR.exists():
        return custom
    
    # Ensure patterns directory is in Python path
    if str(PATTERNS_DIR) not in sys.path:
        sys.path.insert(0, str(PATTERNS_DIR))
    
    for py_file in CUSTOM_PATTERNS_DIR.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        module_name = f"patterns.custom.{py_file.stem}"
        try:
            if module_name in sys.modules:
                importlib.reload(sys.modules[module_name])
            else:
                importlib.import_module(module_name)
            mod = sys.modules[module_name]
            
            # Check for PATTERN export (PatternSpec)
            if hasattr(mod, "PATTERN") and isinstance(mod.PATTERN, PatternSpec):
                custom[mod.PATTERN.name] = mod.PATTERN
                print(f"Loaded custom pattern: {mod.PATTERN.name}")
            # Backward compat: check for bare function
            elif hasattr(mod, "pattern_func"):
                name = getattr(mod, "PATTERN_NAME", py_file.stem)
                custom[name] = PatternSpec(name, mod.pattern_func, 
                                         getattr(mod, "PATTERN_ARGS", {}),
                                         getattr(mod, "PATTERN_DESC", ""))
                print(f"Loaded custom pattern (legacy): {name}")
        except Exception as e:
            print(f"Failed to load custom pattern {py_file}: {e}")
    
    return custom


def reload_all_patterns():
    """Reload all patterns (builtins + custom). Callable at runtime."""
    global PATTERNS, PATTERN_SPECS
    
    _load_builtin_patterns()
    custom = _load_custom_patterns()
    
    # Merge custom (override builtins if same name)
    for name, spec in custom.items():
        PATTERNS[name] = spec.func
        PATTERN_SPECS[name] = spec
    
    print(f"Patterns reloaded: {list(PATTERNS.keys())}")
    return list(PATTERNS.keys())


# Initial load - happens once when module is first imported
reload_all_patterns()