#!/usr/bin/env python3
"""
Idle Reflex — gentle swaying during inactivity.

Events:
- idle_tick: 60s of no user interaction → start gentle swaying
- cleanup: return to neutral pose

Design:
- Runs only when no other reflex is active (conflict_group="head_movement")
- Gentle, slow movements — breathing-like sway
- Returns to center on any interrupt
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
class IdleConfig:
    """Idle reflex configuration."""

    # Neutral position
    center_yaw: int = 0
    center_pitch: int = 12
    move_speed: int = 40

    # Sway parameters
    sway_interval: tuple[float, float] = (3.0, 6.0)  # random interval between sways
    sway_yaw_range: tuple[int, int] = (-20, 20)
    sway_pitch_range: tuple[int, int] = (-5, 5)
    sway_speed: int = 20

    # Safety
    max_head_speed: int = 200
    pitch_min: int = 5
    pitch_max: int = 85
    yaw_min: int = -90
    yaw_max: int = 90


DEFAULT_CONFIG = IdleConfig()


# =============================================================================
# State
# =============================================================================


@dataclass
class IdleStateData:
    """Mutable state for idle reflex."""

    sway_task: asyncio.Task | None = None
    is_active: bool = False


# =============================================================================
# Reflex Logic
# =============================================================================


class IdleReflex:
    """Idle reflex with gentle swaying."""

    def __init__(self, config: IdleConfig = DEFAULT_CONFIG):
        self.config = config
        self.state = IdleStateData()

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

    async def _sway_loop(self):
        """Background gentle swaying loop."""
        cfg = self.config

        try:
            while self.state.is_active:
                # Random interval
                interval = random.uniform(*cfg.sway_interval)
                await asyncio.sleep(interval)

                if not self.state.is_active:
                    break

                # Gentle sway
                yaw = random.randint(*cfg.sway_yaw_range)
                pitch = cfg.center_pitch + random.randint(*cfg.sway_pitch_range)
                yaw = max(cfg.yaw_min, min(cfg.yaw_max, yaw))
                pitch = max(cfg.pitch_min, min(cfg.pitch_max, pitch))

                await self._safe_call("move_head", yaw=yaw, pitch=pitch, speed=cfg.sway_speed)

        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001, S110
            pass

    async def start_swaying(self):
        """Start idle swaying."""
        if self.state.is_active:
            return

        cfg = self.config
        self.state.is_active = True

        # Start from center
        await self._safe_call(
            "move_head",
            yaw=cfg.center_yaw,
            pitch=cfg.center_pitch,
            speed=cfg.move_speed,
        )

        # Start sway loop
        self.state.sway_task = asyncio.create_task(self._sway_loop())

    async def stop_swaying(self):
        """Stop swaying and return to center."""
        if not self.state.is_active:
            return

        self.state.is_active = False

        if self.state.sway_task and not self.state.sway_task.done():
            self.state.sway_task.cancel()
            try:
                await self.state.sway_task
            except asyncio.CancelledError:
                pass

        # Return to center
        cfg = self.config
        await self._safe_call(
            "move_head",
            yaw=cfg.center_yaw,
            pitch=cfg.center_pitch,
            speed=cfg.move_speed,
        )
        self.state.sway_task = None


# =============================================================================
# Global Instance & Event Handlers
# =============================================================================

_idle_reflex = IdleReflex()


@on_event("idle_tick", priority=1, conflict_group="head_movement")
async def on_idle_tick(event: dict):
    """idle_tick: 60s inactivity → start gentle swaying."""
    await _idle_reflex.start_swaying()


@on_cleanup("idle_tick")
async def cleanup_idle_tick():
    """Cleanup: stop swaying, return to center."""
    await _idle_reflex.stop_swaying()