#!/usr/bin/env python3
"""
Greeting Reflex — brief acknowledgment for user messages/responses.

Events:
- user_message: quick nod + smile (when not in thinking mode)
- user_response: return to neutral

Design:
- Lower priority (1) than thinking reflex (5) — thinking takes precedence
- Quick, single movement — not a sustained state
- Auto-returns to neutral after brief delay
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
class GreetingConfig:
    """Greeting reflex configuration."""

    # Neutral position
    center_yaw: int = 0
    center_pitch: int = 12
    move_speed: int = 60

    # Greeting pose
    greet_yaw: int = 10
    greet_pitch: int = 12
    hold_duration: float = 1.5  # seconds to hold greeting pose

    # Safety
    max_head_speed: int = 200
    pitch_min: int = 5
    pitch_max: int = 85
    yaw_min: int = -90
    yaw_max: int = 90


DEFAULT_CONFIG = GreetingConfig()


# =============================================================================
# State
# =============================================================================


@dataclass
class GreetingStateData:
    """Mutable state for greeting reflex."""

    greet_task: asyncio.Task | None = None


# =============================================================================
# Reflex Logic
# =============================================================================


class GreetingReflex:
    """Greeting reflex with quick nod/smile."""

    def __init__(self, config: GreetingConfig = DEFAULT_CONFIG):
        self.config = config
        self.state = GreetingStateData()

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

    async def _greet_and_return(self, face: str = "happy"):
        """Brief greeting pose then return to neutral."""
        cfg = self.config

        # Cancel any existing greeting
        if self.state.greet_task and not self.state.greet_task.done():
            self.state.greet_task.cancel()
            try:
                await self.state.greet_task
            except asyncio.CancelledError:
                pass

        # Greeting pose
        await self._safe_call("set_avatar", face=face)
        await self._safe_call(
            "move_head",
            yaw=cfg.greet_yaw,
            pitch=cfg.greet_pitch,
            speed=cfg.move_speed,
        )

        # Hold briefly
        await asyncio.sleep(cfg.hold_duration)

        # Return to neutral
        await self._safe_call("set_avatar", face="idle")
        await self._safe_call(
            "move_head",
            yaw=cfg.center_yaw,
            pitch=cfg.center_pitch,
            speed=cfg.move_speed,
        )

    async def on_user_message(self):
        """Quick nod + smile for user message."""
        self.state.greet_task = asyncio.create_task(self._greet_and_return("happy"))

    async def on_user_response(self):
        """Return to neutral (already handled by thinking, but safe fallback)."""
        cfg = self.config
        await self._safe_call("set_avatar", face="idle")
        await self._safe_call(
            "move_head",
            yaw=cfg.center_yaw,
            pitch=cfg.center_pitch,
            speed=cfg.move_speed,
        )


# =============================================================================
# Global Instance & Event Handlers
# =============================================================================

_greeting_reflex = GreetingReflex()


# Lower priority (1) so thinking reflex (priority 5) takes precedence
@on_event("user_message", priority=1, conflict_group="head_movement")
async def on_user_message_greeting(event: dict):
    """user_message: brief greeting nod (if thinking reflex doesn't handle it)."""
    await _greeting_reflex.on_user_message()


@on_cleanup("user_message")
async def cleanup_user_message_greeting():
    """Cleanup: ensure neutral."""
    cfg = DEFAULT_CONFIG
    await call("set_avatar", face="idle")
    await call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch, speed=cfg.move_speed)


@on_event("user_response", priority=1, conflict_group="head_movement")
async def on_user_response_greeting(event: dict):
    """user_response: return to neutral."""
    await _greeting_reflex.on_user_response()


@on_cleanup("user_response")
async def cleanup_user_response_greeting():
    """Cleanup: ensure neutral."""
    cfg = DEFAULT_CONFIG
    await call("set_avatar", face="idle")
    await call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch, speed=cfg.move_speed)