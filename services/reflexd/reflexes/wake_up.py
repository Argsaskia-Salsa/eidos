#!/usr/bin/env python3
"""
Wake Up Reflex — screen_on after sleep/standby.

Events:
- screen_on: display woke up (from gateway)
- screen_off: display went to sleep

Design:
- Priority 4 (above battery/touch, below startle/thinking)
- Stretch sequence: yaw sweep + pitch up + happy face + rainbow LED
- Returns to idle after sequence
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
class WakeUpConfig:
    """Wake up reflex configuration."""

    # Neutral position
    center_yaw: int = 0
    center_pitch: int = 12
    move_speed: int = 60

    # Stretch sequence
    stretch_yaw_range: tuple[int, int] = (-40, 40)
    stretch_pitch_up: int = -10   # look up
    stretch_pitch_down: int = 20  # look down
    stretch_speed: int = 50
    hold_duration: float = 0.8

    # LED
    wake_pattern: str = "rainbow"
    wake_fps: int = 20
    led_brightness: float = 1.0

    # Sleep
    sleep_face: str = "idle"
    sleep_led: str = "off"

    # Safety
    max_head_speed: int = 200
    pitch_min: int = 5
    pitch_max: int = 85
    yaw_min: int = -90
    yaw_max: int = 90


DEFAULT_CONFIG = WakeUpConfig()


# =============================================================================
# State
# =============================================================================


@dataclass
class WakeUpStateData:
    """Mutable state for wake up reflex."""

    wake_task: asyncio.Task | None = None
    is_awake: bool = True


# =============================================================================
# LED Stream Client (reuse pattern from thinking.py)
# =============================================================================


class LEDStreamClient:
    """HTTP client for LED Stream Server."""

    def __init__(self, base_url: str = "http://127.0.0.1:8773"):
        self.base_url = base_url.rstrip("/")
        self._session: Any | None = None

    async def __aenter__(self):
        import httpx
        self._session = httpx.AsyncClient(timeout=5.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.aclose()
            self._session = None

    def _require_session(self):
        if self._session is None:
            raise RuntimeError("Use async with LEDStreamClient() as client:")
        return self._session

    async def set_pattern(self, pattern: str) -> dict:
        return await self._post("/pattern", {"pattern": pattern})

    async def set_fps(self, fps: float) -> dict:
        return await self._post("/fps", {"fps": fps})

    async def set_brightness(self, brightness: float) -> dict:
        return await self._post("/brightness", {"brightness": brightness})

    async def _post(self, path: str, data: dict) -> dict:
        session = self._require_session()
        resp = await session.post(f"{self.base_url}{path}", json=data)
        resp.raise_for_status()
        return resp.json()


# =============================================================================
# Reflex Logic
# =============================================================================


class WakeUpReflex:
    """Wake up / sleep reflex with stretch sequence."""

    def __init__(self, config: WakeUpConfig = DEFAULT_CONFIG):
        self.config = config
        self.state = WakeUpStateData()

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

    async def _wake_sequence(self):
        """Full wake up stretch sequence."""
        cfg = self.config

        # Cancel any existing
        if self.state.wake_task and not self.state.wake_task.done():
            self.state.wake_task.cancel()
            try:
                await self.state.wake_task
            except asyncio.CancelledError:
                pass

        self.state.is_awake = True

        # 1. Start LED rainbow
        try:
            async with LEDStreamClient() as client:
                await client.set_pattern(cfg.wake_pattern)
                await client.set_fps(cfg.wake_fps)
                await client.set_brightness(cfg.led_brightness)
        except Exception:  # noqa: BLE001
            await self._safe_call("set_all_leds", r=255, g=0, b=0)

        # 2. Happy face
        await self._safe_call("set_avatar", face="happy")

        # 3. Stretch sequence: yaw sweep left→right
        await self._safe_call("move_head", yaw=cfg.stretch_yaw_range[0], pitch=cfg.center_pitch, speed=cfg.stretch_speed)
        await asyncio.sleep(cfg.hold_duration)

        await self._safe_call("move_head", yaw=cfg.stretch_yaw_range[1], pitch=cfg.center_pitch, speed=cfg.stretch_speed)
        await asyncio.sleep(cfg.hold_duration)

        # 4. Look up (stretch neck)
        await self._safe_call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch + cfg.stretch_pitch_up, speed=cfg.stretch_speed)
        await asyncio.sleep(cfg.hold_duration)

        # 5. Look down
        await self._safe_call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch + cfg.stretch_pitch_down, speed=cfg.stretch_speed)
        await asyncio.sleep(cfg.hold_duration)

        # 6. Return to center
        await self._safe_call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch, speed=cfg.move_speed)

        # 7. Keep rainbow for a bit, then cycle
        await asyncio.sleep(2.0)
        try:
            async with LEDStreamClient() as client:
                await client.set_pattern("cycle")
        except Exception:  # noqa: BLE001
            await self._safe_call("set_all_leds", r=0, g=255, b=0)

        # 8. Back to idle face
        await self._safe_call("set_avatar", face="idle")

        self.state.wake_task = None

    async def _sleep_sequence(self):
        """Sleep sequence: gentle fade."""
        cfg = self.config

        if self.state.wake_task and not self.state.wake_task.done():
            self.state.wake_task.cancel()
            try:
                await self.state.wake_task
            except asyncio.CancelledError:
                pass

        self.state.is_awake = False

        # Neutral face
        await self._safe_call("set_avatar", face=cfg.sleep_face)

        # Turn off LEDs
        try:
            async with LEDStreamClient() as client:
                await client.set_pattern(cfg.sleep_led)
        except Exception:  # noqa: BLE001
            await self._safe_call("clear_leds")

        # Center head
        await self._safe_call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch, speed=cfg.move_speed)

    async def on_screen_on(self, event: dict):
        """Screen woke up - stretch sequence."""
        if not self.state.is_awake:
            self.state.wake_task = asyncio.create_task(self._wake_sequence())

    async def on_screen_off(self, event: dict):
        """Screen going to sleep."""
        if self.state.is_awake:
            self.state.wake_task = asyncio.create_task(self._sleep_sequence())


# =============================================================================
# Global Instance & Event Handlers
# =============================================================================

_wakeup_reflex = WakeUpReflex()


@on_event("screen_on", priority=4, conflict_group="head_movement")
async def on_screen_on(event: dict):
    """Display woke up."""
    await _wakeup_reflex.on_screen_on(event)


@on_event("screen_off", priority=4, conflict_group="head_movement")
async def on_screen_off(event: dict):
    """Display going to sleep."""
    await _wakeup_reflex.on_screen_off(event)


@on_cleanup("screen_on")
@on_cleanup("screen_off")
async def cleanup_wakeup():
    """Cleanup: ensure neutral."""
    cfg = DEFAULT_CONFIG
    await call("set_avatar", face="idle")
    await call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch, speed=cfg.move_speed)
    await call("clear_leds")