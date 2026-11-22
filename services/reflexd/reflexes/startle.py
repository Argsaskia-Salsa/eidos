#!/usr/bin/env python3
"""
Startle Reflex — sudden sound/movement reaction.

Events:
- startle_sound: loud sound detected (via microphone/gateway)
- startle_motion: sudden motion in visual field (via camera/gateway)

Design:
- Highest priority (10) - immediate survival reaction
- Sharp head turn toward/away from stimulus
- Wide eyes (surprised) + LED flash
- Quick return to previous state
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from reflexd import call, on_cleanup, on_event


# =============================================================================
# Configuration
# =============================================================================


@dataclass(frozen=True)
class StartleConfig:
    """Startle reflex configuration."""

    # Neutral position
    center_yaw: int = 0
    center_pitch: int = 12
    move_speed: int = 150  # Fast!

    # Reaction parameters
    turn_angle: int = 45      # degrees to turn toward/away
    reaction_duration: float = 0.8  # brief!
    return_speed: int = 80

    # LED flash
    flash_color: tuple[int, int, int] = (255, 255, 255)  # white flash
    flash_duration: float = 0.15

    # Safety
    max_head_speed: int = 200
    pitch_min: int = 5
    pitch_max: int = 85
    yaw_min: int = -90
    yaw_max: int = 90


DEFAULT_CONFIG = StartleConfig()


# =============================================================================
# State
# =============================================================================


@dataclass
class StartleStateData:
    """Mutable state for startle reflex."""

    reaction_task: asyncio.Task | None = None
    previous_state: str | None = None  # what reflex was active


# =============================================================================
# Reflex Logic
# =============================================================================


class StartleReflex:
    """Startle reflex - immediate reaction to sudden stimuli."""

    def __init__(self, config: StartleConfig = DEFAULT_CONFIG):
        self.config = config
        self.state = StartleStateData()

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

    async def _startle(self, direction: int = 1, pitch_offset: int = 0):
        """Execute startle: sharp turn + surprised face + LED flash."""
        cfg = self.config

        # Cancel any ongoing startle
        if self.state.reaction_task and not self.state.reaction_task.done():
            self.state.reaction_task.cancel()
            try:
                await self.state.reaction_task
            except asyncio.CancelledError:
                pass

        # Sharp turn toward (direction=1) or away (direction=-1) from stimulus
        yaw = cfg.center_yaw + (direction * cfg.turn_angle)
        yaw = max(cfg.yaw_min, min(cfg.yaw_max, yaw))
        pitch = cfg.center_pitch + pitch_offset
        pitch = max(cfg.pitch_min, min(cfg.pitch_max, pitch))

        # Startle pose: surprised + fast turn + white flash
        await self._safe_call("set_avatar", face="surprised")
        await self._safe_call("move_head", yaw=yaw, pitch=pitch, speed=cfg.move_speed)

        # LED flash
        r, g, b = cfg.flash_color
        await self._safe_call("set_all_leds", r=r, g=g, b=b)
        await asyncio.sleep(cfg.flash_duration)
        await self._safe_call("clear_leds")

        # Hold startled pose briefly
        await asyncio.sleep(cfg.reaction_duration)

        # Return to center (or previous reflex will resume via cleanup)
        await self._safe_call("set_avatar", face="idle")
        await self._safe_call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch, speed=cfg.return_speed)

        self.state.reaction_task = None

    async def on_sound(self, event: dict):
        """Loud sound - turn toward source (if direction known) or random."""
        payload = event.get("payload", {})
        direction = payload.get("direction", 1)  # -1=left, 1=right, 0=front
        if direction == 0:
            direction = 1  # default right

        self.state.reaction_task = asyncio.create_task(self._startle(direction))

    async def on_motion(self, event: dict):
        """Sudden motion - turn toward movement."""
        payload = event.get("payload", {})
        direction = payload.get("direction", 1)
        if direction == 0:
            direction = -1  # default left for motion

        self.state.reaction_task = asyncio.create_task(self._startle(direction, pitch_offset=-5))


# =============================================================================
# Global Instance & Event Handlers
# =============================================================================

_startle_reflex = StartleReflex()


# Priority 10 - highest, preempts thinking (5) and everything else
@on_event("startle_sound", priority=10, conflict_group="head_movement")
async def on_startle_sound(event: dict):
    """Loud sound detected."""
    await _startle_reflex.on_sound(event)


@on_event("startle_motion", priority=10, conflict_group="head_movement")
async def on_startle_motion(event: dict):
    """Sudden motion detected."""
    await _startle_reflex.on_motion(event)


@on_cleanup("startle_sound")
@on_cleanup("startle_motion")
async def cleanup_startle():
    """Cleanup: ensure neutral (other reflexes will resume via their own handlers)."""
    cfg = DEFAULT_CONFIG
    await call("set_avatar", face="idle")
    await call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch, speed=cfg.return_speed)
    await call("clear_leds")