"""
Built-in LED patterns.
Pure functions returning list[Frame] where Frame = list[list[int]] (LED x RGB).
"""

import math
from typing import Callable

from .config import CONFIG

NUM_LEDS = CONFIG.NUM_LEDS
Frame = list[list[int]]  # LEDs x RGB


def pattern_cycle(num_leds: int = NUM_LEDS) -> list[Frame]:
    """Sequential 1→2→...→N→1..."""
    patterns = []
    for i in range(num_leds):
        colors = [[0, 0, 0] for _ in range(num_leds)]
        colors[i] = [255, 255, 255]
        patterns.append(colors)
    return patterns


def pattern_rainbow(num_leds: int = NUM_LEDS) -> list[Frame]:
    """Flowing rainbow wave."""
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


def pattern_breathing(num_leds: int = NUM_LEDS, color: list[int] = [0, 180, 255]) -> list[Frame]:
    """All LEDs breathe together."""
    patterns = []
    frames = 60
    for frame in range(frames):
        brightness = (math.sin(frame * 2 * math.pi / frames) + 1) / 2  # 0..1
        colors = []
        for _ in range(num_leds):
            colors.append([int(c * brightness) for c in color])
        patterns.append(colors)
    return patterns


def pattern_kitt(num_leds: int = NUM_LEDS, color: list[int] = [255, 0, 0], tail: float = 0.5) -> list[Frame]:
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


def pattern_solid(num_leds: int = NUM_LEDS, color: list[int] = [255, 255, 255]) -> list[Frame]:
    """Single solid color frame."""
    return [[color[:] for _ in range(num_leds)]]


def pattern_off(num_leds: int = NUM_LEDS) -> list[Frame]:
    """All off."""
    return [[[0, 0, 0] for _ in range(num_leds)]]


# Pattern specifications for registry
from ..base.registry import SpecProtocol


class LEDPatternSpec:
    """Pattern specification with metadata for CLI generation."""
    def __init__(self, name: str, func: Callable, args: dict | None = None, description: str = ""):
        self.name = name
        self.func = func
        self.args = args or {}
        self.description = description


# Built-in pattern specs
BUILTIN_PATTERNS: dict[str, SpecProtocol] = {
    "cycle": LEDPatternSpec("cycle", pattern_cycle, {}, "Sequential 1→2→...→N"),
    "rainbow": LEDPatternSpec("rainbow", pattern_rainbow, {}, "Flowing rainbow wave"),
    "breathing": LEDPatternSpec("breathing", pattern_breathing, {"color": [0, 180, 255]}, "All LEDs breathe together"),
    "kitt": LEDPatternSpec("kitt", pattern_kitt, {"color": [255, 0, 0], "tail": 0.5}, "KITT scanner back and forth"),
    "solid": LEDPatternSpec("solid", pattern_solid, {"color": [255, 255, 255]}, "Single solid color"),
    "off": LEDPatternSpec("off", pattern_off, {}, "All LEDs off"),
}