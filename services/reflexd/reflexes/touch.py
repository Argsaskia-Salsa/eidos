#!/usr/bin/env python3
"""
Touch Reflex — gateway-side touch reactions (zones, gestures, combos).

Events (from gateway via touch_sensor events):
- touch_tap: single tap on zone
- touch_stroke: swipe across zones
- touch_hold: hold on zone
- touch_double_tap: double tap on zone
- touch_attention: 30s no interaction

Design:
- Zones: forehead(0), top(1), back(2), left(3), right(4), chin(5)
- Richer reactions than firmware: zone-specific, combos, LED feedback
- Priority 3 (below thinking=5, above greeting/idle=1)
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any

from reflexd import call, on_cleanup, on_event


# =============================================================================
# Configuration
# =============================================================================


@dataclass(frozen=True)
class TouchConfig:
    """Touch reflex configuration."""

    # Neutral position
    center_yaw: int = 0
    center_pitch: int = 12
    move_speed: int = 60

    # Reaction parameters
    reaction_duration: float = 1.5  # seconds to hold reaction pose
    return_speed: int = 40

    # Zone definitions (matching firmware Si12T pads)
    # 0=forehead, 1=top, 2=back, 3=left, 4=right, 5=chin
    zone_names: tuple[str, ...] = (
        "forehead", "top", "back", "left", "right", "chin"
    )

    # Safety
    max_head_speed: int = 200
    pitch_min: int = 5
    pitch_max: int = 85
    yaw_min: int = -90
    yaw_max: int = 90


DEFAULT_CONFIG = TouchConfig()


# =============================================================================
# Zone-specific reactions
# =============================================================================


ZONE_REACTIONS = {
    # zone: (face, yaw_offset, pitch_offset, led_color, description)
    0: ("surprised", 0, -10, (255, 255, 0), "forehead tap → surprised, tilt back"),      # forehead
    1: ("happy", 0, -5, (0, 255, 100), "top stroke → happy, lean back"),                   # top
    2: ("embarrassed", 0, 10, (255, 100, 100), "back stroke → embarrassed, bow"),         # back
    3: ("thinking", -20, 0, (100, 200, 255), "left tap → thinking, turn left"),           # left
    4: ("thinking", 20, 0, (100, 200, 255), "right tap → thinking, turn right"),          # right
    5: ("sad", 0, 15, (100, 100, 255), "chin tap → sad, tilt down"),                       # chin
}

STROKE_REACTIONS = {
    # (from_zone, to_zone): (face, yaw, pitch, led_color)
    (3, 4): ("happy", 0, 12, (0, 255, 0)),      # left→right stroke
    (4, 3): ("happy", 0, 12, (0, 255, 0)),      # right→left stroke
    (0, 2): ("thinking", 0, 12, (100, 200, 255)), # forehead→back
    (2, 0): ("surprised", 0, 5, (255, 255, 100)), # back→forehead
}


# =============================================================================
# State
# =============================================================================


@dataclass
class TouchStateData:
    """Mutable state for touch reflex."""

    reaction_task: asyncio.Task | None = None
    last_zone: int | None = None
    last_tap_time: float = 0.0


# =============================================================================
# Reflex Logic
# =============================================================================


class TouchReflex:
    """Touch reflex with zone-aware reactions."""

    def __init__(self, config: TouchConfig = DEFAULT_CONFIG):
        self.config = config
        self.state = TouchStateData()

    async def _safe_call(self, tool: str, **kwargs):
        """Safe call with clamped values."""
        cfg = self.config
        if "yaw" in kwargs:
            kwargs["yaw"] = max(cfg.yaw_min, min(cfg.yaw_max, kwargs["yaw"]))
        if "pitch" in kwargs:
            kwargs["pitch"] = max(cfg.pitch_min, min(cfg.pitch_max, kwargs["pitch"]))
        if "speed" in kwargs:
            kwargs["speed"] = min(cfg.max_head_speed, max(1, kwargs["speed"]))

        try:
            await call(tool, **kwargs)
        except Exception:  # noqa: BLE001, S110
            pass

    async def _react(self, face: str, yaw: int, pitch: int, led_rgb: tuple[int, int, int], hold: float | None = None):
        """Execute reaction: face + head + LED, then return to center."""
        cfg = self.config
        hold = hold or cfg.reaction_duration

        # Cancel any ongoing reaction
        if self.state.reaction_task and not self.state.reaction_task.done():
            self.state.reaction_task.cancel()
            try:
                await self.state.reaction_task
            except asyncio.CancelledError:
                pass

        # Reaction pose
        await self._safe_call("set_avatar", face=face)
        await self._safe_call("move_head", yaw=yaw, pitch=pitch, speed=cfg.move_speed)
        await self._safe_call("set_all_leds", r=led_rgb[0], g=led_rgb[1], b=led_rgb[2])

        # Hold
        await asyncio.sleep(hold)

        # Return to center
        await self._safe_call("set_avatar", face="idle")
        await self._safe_call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch, speed=cfg.return_speed)
        await self._safe_call("clear_leds")

        self.state.reaction_task = None

    async def on_tap(self, event: dict):
        """Single tap on zone."""
        zone = event.get("payload", {}).get("zone", 0)
        if zone not in ZONE_REACTIONS:
            return

        self.state.last_zone = zone
        self.state.last_tap_time = asyncio.get_event_loop().time()

        face, yaw_off, pitch_off, led_rgb, _ = ZONE_REACTIONS[zone]
        cfg = self.config
        await self._react(face, cfg.center_yaw + yaw_off, cfg.center_pitch + pitch_off, led_rgb)

    async def on_stroke(self, event: dict):
        """Stroke across zones."""
        payload = event.get("payload", {})
        from_zone = payload.get("from_zone", 0)
        to_zone = payload.get("to_zone", 0)

        key = (from_zone, to_zone)
        if key in STROKE_REACTIONS:
            face, yaw, pitch, led_rgb = STROKE_REACTIONS[key]
        else:
            # Default: happy sweep
            face, yaw, pitch, led_rgb = "happy", 0, 12, (0, 255, 0)

        await self._react(face, yaw, pitch, led_rgb)

    async def on_hold(self, event: dict):
        """Hold on zone - sustained reaction."""
        zone = event.get("payload", {}).get("zone", 0)
        duration = event.get("payload", {}).get("duration", 1.0)

        if zone in ZONE_REACTIONS:
            face, yaw_off, pitch_off, led_rgb, _ = ZONE_REACTIONS[zone]
        else:
            face, yaw_off, pitch_off, led_rgb = "thinking", 0, 0, (100, 200, 255)

        cfg = self.config
        await self._react(face, cfg.center_yaw + yaw_off, cfg.center_pitch + pitch_off, led_rgb, hold=max(duration, 2.0))

    async def on_double_tap(self, event: dict):
        """Double tap - excited reaction."""
        zone = event.get("payload", {}).get("zone", 0)

        # Double tap = excited happy + rainbow flash
        await self._safe_call("set_avatar", face="happy")
        await self._safe_call("move_head", yaw=0, pitch=5, speed=80)
        # Quick LED flash
        for _ in range(3):
            await self._safe_call("set_all_leds", r=255, g=100, b=255)
            await asyncio.sleep(0.15)
            await self._safe_call("set_all_leds", r=0, g=255, b=100)
            await asyncio.sleep(0.15)

        await asyncio.sleep(0.5)
        cfg = self.config
        await self._safe_call("set_avatar", face="idle")
        await self._safe_call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch, speed=cfg.return_speed)
        await self._safe_call("clear_leds")

    async def on_attention_seek(self, event: dict):
        """30s idle - playful attention seeking."""
        # Random playful look
        yaw = random.randint(-30, 30)
        pitch = random.randint(5, 20)
        faces = ["happy", "surprised", "thinking"]
        face = random.choice(faces)
        colors = [(255, 200, 0), (0, 255, 200), (200, 0, 255)]
        led_rgb = random.choice(colors)

        await self._react(face, yaw, pitch, led_rgb, hold=2.0)


# =============================================================================
# Global Instance & Event Handlers
# =============================================================================

_touch_reflex = TouchReflex()


@on_event("touch_tap", priority=3, conflict_group="head_movement")
async def on_touch_tap(event: dict):
    """Single tap on touch zone."""
    await _touch_reflex.on_tap(event)


@on_event("touch_stroke", priority=3, conflict_group="head_movement")
async def on_touch_stroke(event: dict):
    """Stroke across touch zones."""
    await _touch_reflex.on_stroke(event)


@on_event("touch_hold", priority=3, conflict_group="head_movement")
async def on_touch_hold(event: dict):
    """Hold on touch zone."""
    await _touch_reflex.on_hold(event)


@on_event("touch_double_tap", priority=3, conflict_group="head_movement")
async def on_touch_double_tap(event: dict):
    """Double tap on touch zone."""
    await _touch_reflex.on_double_tap(event)


@on_event("touch_attention", priority=1, conflict_group="head_movement")
async def on_touch_attention(event: dict):
    """Attention seeking after 30s idle."""
    await _touch_reflex.on_attention_seek(event)


@on_cleanup("touch_tap")
@on_cleanup("touch_stroke")
@on_cleanup("touch_hold")
@on_cleanup("touch_double_tap")
@on_cleanup("touch_attention")
async def cleanup_touch():
    """Cleanup: ensure neutral."""
    cfg = DEFAULT_CONFIG
    await call("set_avatar", face="idle")
    await call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch, speed=cfg.return_speed)
    await call("clear_leds")