#!/usr/bin/env python3
"""
Thinking Reflex — head/face/LED animation for request/response cycle.

Events (from body_expression_bridge via reflexd MCP):
- thinking_start: LLM generation starts → thinking pose + LED rainbow + micro-movements
- thinking_end: LLM generation ends → happy pose + green LED, stop micro-movements
- user_message: fallback for thinking_start (if LLM middleware not used)
- user_response: fallback for thinking_end (if LLM middleware not used)

Architecture:
- Uses LED Stream Server (HTTP API) for LED patterns via stackchan_follow_led_stream
- Direct MCP calls for head movement and face via reflexd.call()
- State machine for clear lifecycle management
- Reflexes are additional layer over behavior patterns (not base)
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from enum import Enum
from typing import Any

from reflexd import call, on_cleanup, on_event

# =============================================================================


# =============================================================================
# Configuration (dataclass, in code — easy to find and modify)
# =============================================================================


@dataclass(frozen=True)
class ThinkingConfig:
    """Thinking reflex configuration. All values in code — easy to find and modify."""

    # Head movement
    thinking_yaw: int = 30
    thinking_pitch: int = 15
    center_yaw: int = 0
    center_pitch: int = 12
    move_speed: int = 80

    # Micro-movements during thinking (prevent idle timeout)
    micro_move_interval: float = 1.5  # seconds between micro-moves (was 3.0)
    micro_yaw_range: tuple[int, int] = (-8, 8)      # was (-15, 15)
    micro_pitch_range: tuple[int, int] = (-3, 5)    # was (-5, 10)
    micro_speed: int = 20       # was 30, slower for smoothness

    # Return to neutral after thinking_end
    return_to_neutral_delay: float = 60.0  # seconds before neutral
    return_steps: int = 10
    return_delay: float = 3.5

    # LED pattern via LED Stream Server
    led_pattern: str = "rainbow"  # pattern name in LED Stream Server
    led_fps: int = 20
    led_brightness: float = 1.0
    led_cycle_interval: float = 2.0  # seconds per frame for rainbow

    # LED Stream Server connection
    led_stream_server_url: str = "http://127.0.0.1:8773"

    # Safety
    max_head_speed: int = 200
    pitch_min: int = 5
    pitch_max: int = 85
    yaw_min: int = -90
    yaw_max: int = 90
    led_brightness: float = 1.0
    led_cycle_interval: float = 2.0  # seconds per frame for rainbow

    # LED Stream Server connection
    led_stream_server_url: str = "http://127.0.0.1:8773"

    # Safety
    max_head_speed: int = 200
    pitch_min: int = 5
    pitch_max: int = 85
    yaw_min: int = -90
    yaw_max: int = 90


# Default config instance (can be overridden for tests)
DEFAULT_CONFIG = ThinkingConfig()


# =============================================================================
# State Machine
# =============================================================================


class ThinkingState(Enum):
    """Thinking reflex states."""

    IDLE = "idle"
    THINKING = "thinking"
    RETURNING = "returning"


@dataclass
class ThinkingStateData:
    """Mutable state data for the reflex."""

    state: ThinkingState = ThinkingState.IDLE
    thinking_task: asyncio.Task | None = None
    led_task: asyncio.Task | None = None
    return_task: asyncio.Task | None = None
    current_yaw: float = 0.0
    current_pitch: float = 12.0  # DEFAULT_PITCH


# =============================================================================
# LED Stream Server Client (HTTP API for stackchan_follow_led_stream)
# =============================================================================


class LEDStreamClient:
    """HTTP client for LED Stream Server (stackchan_follow_led_stream)."""

    def __init__(self, base_url: str = "http://127.0.0.1:8773"):
        self.base_url = base_url.rstrip("/")
        self._session: Any | None = None  # httpx.AsyncClient

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
        """Set LED pattern (cycle, rainbow, breathing, kitt, solid, off)."""
        return await self._post("/pattern", {"pattern": pattern})

    async def set_fps(self, fps: float) -> dict:
        """Set FPS (1-30)."""
        return await self._post("/fps", {"fps": fps})

    async def set_brightness(self, brightness: float) -> dict:
        """Set brightness (0.0-1.0)."""
        return await self._post("/brightness", {"brightness": brightness})

    async def reload_patterns(self) -> dict:
        """Reload custom patterns from custom/ directory."""
        return await self._post("/patterns/reload", {})

    async def status(self) -> dict:
        """Get server status."""
        return await self._get("/status")

    async def list_patterns(self) -> dict:
        """List available patterns."""
        return await self._get("/patterns")

    async def _get(self, path: str) -> dict:
        session = self._require_session()
        resp = await session.get(f"{self.base_url}{path}")
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, data: dict) -> dict:
        session = self._require_session()
        resp = await session.post(f"{self.base_url}{path}", json=data)
        resp.raise_for_status()
        return resp.json()


# =============================================================================
# State Machine Logic
# =============================================================================


class ThinkingReflex:
    """Thinking reflex with state machine."""

    def __init__(self, config: ThinkingConfig = DEFAULT_CONFIG):
        self.config = config
        self.state_data = ThinkingStateData()
        self._led_client: LEDStreamClient | None = None
        self._led_client_cm: Any | None = None  # context manager

    async def _get_led_client(self) -> LEDStreamClient:
        """Get or create LED Stream Client."""
        if self._led_client is None:
            self._led_client = LEDStreamClient(self.config.led_stream_server_url)
            self._led_client_cm = self._led_client.__aenter__()
            await self._led_client_cm
        return self._led_client

    async def _cleanup_led_client(self):
        if self._led_client_cm:
            await self._led_client_cm.__aexit__(None, None, None)
            self._led_client_cm = None
            self._led_client = None

    # ----- State transitions -----

    async def _enter_thinking(self):
        """Enter THINKING state: pose + LED + micro-movements.
        
        Uses direct MCP for head movement (Pose Stream Server has gateway integration bug).
        Uses LED Stream Server for LED patterns.
        """
        cfg = self.config
        sd = self.state_data

        # Cancel any existing tasks
        await self._cancel_all_tasks()

        # Head pose via direct MCP (reliable)
        await self._safe_call(
            "move_head",
            yaw=cfg.thinking_yaw,
            pitch=cfg.thinking_pitch,
            speed=cfg.move_speed,
        )

        # Face expression
        await self._safe_call("set_avatar", face="thinking")

        # LED: start rainbow pattern via LED Stream Server
        try:
            async with LEDStreamClient(self.config.led_stream_server_url) as client:
                await client.set_pattern(self.config.led_pattern)
                await client.set_fps(self.config.led_fps)
                await client.set_brightness(self.config.led_brightness)
        except Exception:  # noqa: BLE001
            # Fallback to direct MCP if LED Stream Server unavailable
            await self._safe_call("set_all_leds", r=255, g=0, b=0)

        # Start micro-movements task
        sd.thinking_task = asyncio.create_task(self._micro_movements_loop())
        sd.state = ThinkingState.THINKING

    async def _enter_returning(self):
        """Enter RETURNING state: green LED, happy face, return to neutral after delay."""
        sd = self.state_data

        # Cancel thinking tasks
        if sd.thinking_task and not sd.thinking_task.done():
            sd.thinking_task.cancel()
            try:
                await sd.thinking_task
            except asyncio.CancelledError:
                pass

        # Return to neutral pose via direct MCP
        await self._safe_call(
            "move_head",
            yaw=self.config.center_yaw,
            pitch=self.config.center_pitch,
            speed=self.config.move_speed,
        )

        # Happy pose + green LED
        await self._safe_call("set_all_leds", r=0, g=255, b=0)
        await self._safe_call("set_avatar", face="happy")

        # LED: switch to cycle pattern via LED Stream Server
        try:
            async with LEDStreamClient(self.config.led_stream_server_url) as client:
                await client.set_pattern("cycle")
        except Exception:  # noqa: BLE001, S110
            pass

        # Schedule return to neutral (face/LED only, pose already set)
        sd.return_task = asyncio.create_task(self._return_to_neutral_after_delay())
        sd.state = ThinkingState.RETURNING

    async def _enter_idle(self):
        """Enter IDLE state: neutral pose, LEDs off."""
        await self._cancel_all_tasks()
        # Neutral pose via direct MCP
        await self._safe_call(
            "move_head",
            yaw=self.config.center_yaw,
            pitch=self.config.center_pitch,
            speed=self.config.move_speed,
        )
        # LED: turn off via LED Stream Server
        try:
            async with LEDStreamClient(self.config.led_stream_server_url) as client:
                await client.set_pattern("off")
        except Exception:  # noqa: BLE001
            pass
        await self._safe_call("clear_leds")
        await self._safe_call("set_avatar", face="neutral")
        self.state_data.state = ThinkingState.IDLE

    async def _cancel_all_tasks(self):
        """Cancel all running tasks."""
        sd = self.state_data
        for task in [sd.thinking_task, None]:  # LED task handled via HTTP, no task
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.state_data.thinking_task = None

    # ----- Background tasks -----

    async def _micro_movements_loop(self):
        """Background micro-movements during thinking to prevent idle timeout.
        
        Uses direct MCP with small random movements around thinking pose.
        """
        cfg = self.config
        sd = self.state_data

        try:
            while sd.state == ThinkingState.THINKING:
                await asyncio.sleep(cfg.micro_move_interval)

                if sd.state != ThinkingState.THINKING:
                    break

                # Small random movement around thinking pose via direct MCP
                yaw = cfg.thinking_yaw + random.randint(*cfg.micro_yaw_range)
                pitch = cfg.thinking_pitch + random.randint(*cfg.micro_pitch_range)
                yaw = max(cfg.yaw_min, min(cfg.yaw_max, yaw))
                pitch = max(cfg.pitch_min, min(cfg.pitch_max, pitch))

                await self._safe_call("move_head", yaw=yaw, pitch=pitch, speed=cfg.micro_speed)

        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001, S110
            pass

    async def _return_to_neutral_after_delay(self):
        """Return to neutral after delay."""
        try:
            await asyncio.sleep(self.config.return_to_neutral_delay)
            await self._enter_idle()
        except asyncio.CancelledError:
            pass

    # ----- Helpers -----

    async def _safe_call(self, tool: str, **kwargs):
        """Safe call with clamped values."""
        cfg = self.config
        # Clamp head values
        if "yaw" in kwargs:
            kwargs["yaw"] = max(cfg.yaw_min, min(cfg.yaw_max, kwargs["yaw"]))
        if "pitch" in kwargs:
            kwargs["pitch"] = max(cfg.pitch_min, min(cfg.pitch_max, kwargs["pitch"]))
        if "speed" in kwargs:
            kwargs["speed"] = min(cfg.max_head_speed, max(1, kwargs["speed"]))

        try:
            await call(tool, **kwargs)
        except Exception:  # noqa: BLE001, S110
            # Log but don't crash reflex
            pass

    # ----- Event Handlers -----

    async def on_thinking_start(self, event: dict):
        """thinking_start: LLM generation started."""
        await self._enter_thinking()

    async def on_thinking_end(self, event: dict):
        """thinking_end: LLM generation finished."""
        await self._enter_returning()

    async def on_user_message(self, event: dict):
        """user_message: fallback for thinking_start."""
        if self.state_data.state == ThinkingState.IDLE:
            await self._enter_thinking()

    async def on_user_response(self, event: dict):
        """user_response: fallback for thinking_end."""
        if self.state_data.state == ThinkingState.THINKING:
            await self._enter_returning()

    # Cleanup handlers
    async def cleanup_thinking(self):
        await self._enter_idle()

    async def cleanup_thinking_end(self):
        if self.state_data.return_task and not self.state_data.return_task.done():
            self.state_data.return_task.cancel()
            try:
                await self.state_data.return_task
            except asyncio.CancelledError:
                pass
        await self._cleanup_led_client()


# =============================================================================
# Reflex Registration
# =============================================================================

# Global instance
_thinking_reflex = ThinkingReflex()


@on_event("thinking_start", priority=10, conflict_group="head_movement")
async def on_thinking_start(event: dict):
    """thinking_start: LLM generation started."""
    await _thinking_reflex.on_thinking_start(event)


@on_event("thinking_end", priority=10, conflict_group="head_movement")
async def on_thinking_end(event: dict):
    """thinking_end: LLM generation finished."""
    await _thinking_reflex.on_thinking_end(event)


@on_event("user_message", priority=5, conflict_group="head_movement")
async def on_user_message(event: dict):
    """user_message: fallback for thinking_start."""
    await _thinking_reflex.on_user_message(event)


@on_event("user_response", priority=5, conflict_group="head_movement")
async def on_user_response(event: dict):
    """user_response: fallback for thinking_end."""
    await _thinking_reflex.on_user_response(event)


@on_cleanup("thinking_start")
async def cleanup_thinking_start():
    await _thinking_reflex.cleanup_thinking()


@on_cleanup("thinking_end")
async def cleanup_thinking_end():
    await _thinking_reflex.cleanup_thinking_end()
