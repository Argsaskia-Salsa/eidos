"""
Example custom pattern: Pulse
All LEDs pulse together at a configurable frequency.
"""

from pattern_registry import PatternSpec


def pulse(num_leds: int = 12, color: list[int] = [255, 100, 0], freq: float = 1.0) -> list[list[list[int]]]:
    """Pulsing effect - all LEDs fade in and out together."""
    import math
    patterns = []
    frames = int(60 / freq)  # frames per cycle
    for frame in range(frames):
        # Sine wave: 0 to 1
        brightness = (math.sin(frame * 2 * math.pi / frames) + 1) / 2
        colors = []
        for _ in range(num_leds):
            colors.append([int(c * brightness) for c in color])
        patterns.append(colors)
    return patterns


# Export pattern specification
PATTERN = PatternSpec(
    name="pulse",
    func=pulse,
    args={"color": [255, 100, 0], "freq": 1.0},
    description="All LEDs pulse in/out at configurable frequency"
)