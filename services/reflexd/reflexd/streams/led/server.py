"""
LED Stream Server - extends StreamServer for LED patterns.
WS format matches stackchan_follow_led_stream expectations.
"""

import time
from typing import Any

from ..base.server import StreamServer, ServerConfig
from ..base.registry import SpecProtocol
from .registry import LEDPatternRegistry, LEDPatternSpec
from .config import CONFIG


class LEDServerConfig(ServerConfig):
    WS_HOST = CONFIG.WS_HOST
    WS_PORT = CONFIG.WS_PORT
    HTTP_PORT = CONFIG.HTTP_PORT
    DEFAULT_FPS = CONFIG.DEFAULT_FPS
    DEFAULT_INTENSITY = CONFIG.DEFAULT_BRIGHTNESS


class LEDStreamServer(StreamServer):
    """LED Stream Server with WebSocket (gateway) + HTTP (control)."""
    
    element_name = "pattern"
    intensity_name = "brightness"
    registry = LEDPatternRegistry
    
    def get_config(self) -> ServerConfig:
        return LEDServerConfig()
    
    def make_frame(self, frame_colors: list[list[int]], brightness: float) -> dict:
        """Serialize to WS JSON format expected by stackchan_follow_led_stream."""
        return {
            "ts": time.time(),
            "kind": "continuous",
            "colors": frame_colors
        }
    
    def generate_frames(self, pattern_func: callable, **args) -> list:
        """Call pattern function with num_leds and args."""
        return pattern_func(CONFIG.NUM_LEDS, **args)
    
    def _create_state(self):
        return LEDState(self)


class LEDState(StreamServer.StreamState):
    """LED-specific state with brightness handling."""
    
    def apply_intensity(self, frames: list) -> list:
        """Apply brightness to frames."""
        if self.intensity >= 1.0:
            return frames
        return [
            [[int(c * self.intensity) for c in led] for led in frame]
            for frame in frames
        ]