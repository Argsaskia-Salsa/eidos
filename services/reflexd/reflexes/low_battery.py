#!/usr/bin/env python3
"""
Low Battery Reflex — battery monitoring and distress signals.

Events:
- battery_low: battery < 20% (from gateway status)
- battery_critical: battery < 10%
- battery_charging: started charging
- battery_full: reached 100%

Design:
- Priority 2 (above idle/greeting, below thinking/touch)
- Progressive distress: sad face → slower movements → pulsing red LED
- Recovery: happy face + green LED when charging
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
class BatteryConfig:
    """Low battery reflex configuration."""

    # Thresholds
    low_threshold: int = 20      # %
    critical_threshold: int = 10  # %

    # Neutral position
    center_yaw: int = 0
    center_pitch: int = 12
    move_speed: int = 30  # Slow when tired

    # Reaction timing
    pulse_interval: float = 2.0  # seconds between LED pulses
    check_interval: float = 30.0  # seconds between battery checks (if polling)

    # Safety
    max_head_speed: int = 200
    pitch_min: int = 5
    pitch_max: int = 85
    yaw_min: int = -90
    yaw_max: int = 90


DEFAULT_CONFIG = BatteryConfig()


# =============================================================================
# State
# =============================================================================


@dataclass
class BatteryStateData:
    """Mutable state for battery reflex."""

    monitor_task: asyncio.Task | None = None
    pulse_task: asyncio.Task | None = None
    current_level: int = 100
    is_charging: bool = False
    state: str = "normal"  # normal, low, critical, charging, full


# =============================================================================
# Reflex Logic
# =============================================================================


class BatteryReflex:
    """Battery monitoring reflex with progressive distress signals."""

    def __init__(self, config: BatteryConfig = DEFAULT_CONFIG):
        self.config = config
        self.state = BatteryStateData()

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

    async def _pulse_led(self, color: tuple[int, int, int], face: str = "sad"):
        """Pulse LED with given color while in low/critical state."""
        cfg = self.config
        try:
            while self.state.state in ("low", "critical"):
                r, g, b = color
                await self._safe_call("set_all_leds", r=r, g=g, b=b)
                await self._safe_call("set_avatar", face=face)
                await asyncio.sleep(cfg.pulse_interval / 2)
                await self._safe_call("clear_leds")
                await asyncio.sleep(cfg.pulse_interval / 2)
        except asyncio.CancelledError:
            pass

    async def _set_state(self, new_state: str, level: int):
        """Transition to new battery state."""
        cfg = self.config
        self.state.current_level = level
        old_state = self.state.state
        self.state.state = new_state

        # Cancel existing tasks
        for task in [self.state.pulse_task, self.state.monitor_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if new_state == "normal":
            # Recovery: happy + green
            await self._safe_call("set_avatar", face="happy")
            await self._safe_call("set_all_leds", r=0, g=255, b=0)
            await asyncio.sleep(1.0)
            await self._safe_call("clear_leds")
            await self._safe_call("set_avatar", face="idle")

        elif new_state == "charging":
            # Charging: thinking face + blue pulse
            await self._safe_call("set_avatar", face="thinking")
            self.state.pulse_task = asyncio.create_task(self._pulse_led((0, 100, 255), "thinking"))

        elif new_state == "full":
            # Full: happy + green solid
            await self._safe_call("set_avatar", face="happy")
            await self._safe_call("set_all_leds", r=0, g=255, b=0)
            await asyncio.sleep(2.0)
            await self._safe_call("clear_leds")
            await self._safe_call("set_avatar", face="idle")
            self.state.state = "normal"

        elif new_state == "low":
            # Low: sad + red pulse, slow movements
            await self._safe_call("set_avatar", face="sad")
            await self._safe_call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch + 10, speed=cfg.move_speed)
            self.state.pulse_task = asyncio.create_task(self._pulse_led((255, 50, 50), "sad"))

        elif new_state == "critical":
            # Critical: sad + faster red pulse, very slow, head down
            await self._safe_call("set_avatar", face="sad")
            await self._safe_call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch + 15, speed=max(10, cfg.move_speed // 2))
            self.state.pulse_task = asyncio.create_task(self._pulse_led((255, 0, 0), "sad"))

    async def on_battery_low(self, event: dict):
        """Battery dropped below 20%."""
        level = event.get("payload", {}).get("level", 15)
        if self.state.state not in ("low", "critical"):
            await self._set_state("low", level)

    async def on_battery_critical(self, event: dict):
        """Battery dropped below 10%."""
        level = event.get("payload", {}).get("level", 5)
        if self.state.state != "critical":
            await self._set_state("critical", level)

    async def on_battery_charging(self, event: dict):
        """Started charging."""
        level = event.get("payload", {}).get("level", 0)
        self.state.is_charging = True
        await self._set_state("charging", level)

    async def on_battery_full(self, event: dict):
        """Battery reached 100%."""
        self.state.is_charging = False
        await self._set_state("full", 100)

    async def on_battery_update(self, event: dict):
        """Periodic battery level update (if polling)."""
        level = event.get("payload", {}).get("level", 100)
        charging = event.get("payload", {}).get("charging", False)

        self.state.current_level = level
        self.state.is_charging = charging

        # Auto-transition based on level
        if charging and self.state.state in ("low", "critical"):
            await self._set_state("charging", level)
        elif not charging:
            if level <= self.config.critical_threshold and self.state.state != "critical":
                await self._set_state("critical", level)
            elif level <= self.config.low_threshold and self.state.state not in ("low", "critical"):
                await self._set_state("low", level)
            elif level > self.config.low_threshold and self.state.state in ("low", "critical"):
                await self._set_state("normal", level)


# =============================================================================
# Global Instance & Event Handlers
# =============================================================================

_battery_reflex = BatteryReflex()


@on_event("battery_low", priority=2, conflict_group="head_movement")
async def on_battery_low(event: dict):
    """Battery below 20%."""
    await _battery_reflex.on_battery_low(event)


@on_event("battery_critical", priority=2, conflict_group="head_movement")
async def on_battery_critical(event: dict):
    """Battery below 10%."""
    await _battery_reflex.on_battery_critical(event)


@on_event("battery_charging", priority=2, conflict_group="head_movement")
async def on_battery_charging(event: dict):
    """Started charging."""
    await _battery_reflex.on_battery_charging(event)


@on_event("battery_full", priority=2, conflict_group="head_movement")
async def on_battery_full(event: dict):
    """Battery full."""
    await _battery_reflex.on_battery_full(event)


@on_event("battery_update", priority=2, conflict_group="head_movement")
async def on_battery_update(event: dict):
    """Periodic battery update."""
    await _battery_reflex.on_battery_update(event)


@on_cleanup("battery_low")
@on_cleanup("battery_critical")
@on_cleanup("battery_charging")
@on_cleanup("battery_full")
@on_cleanup("battery_update")
async def cleanup_battery():
    """Cleanup: ensure neutral."""
    cfg = DEFAULT_CONFIG
    await call("set_avatar", face="idle")
    await call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch, speed=cfg.move_speed)
    await call("clear_leds")