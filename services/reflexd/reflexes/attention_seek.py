#!/usr/bin/env python3
"""
Attention Seek Reflex — playful idle behaviors to get user attention.

Events:
- attention_idle: no interaction for 5 minutes (configurable)
- user_approached: proximity sensor triggered (optional)
- user_left: proximity lost (optional)

Design:
- Priority 1 (same as idle/greeting, conflict_group=head_movement)
- Playful behaviors: head tilts, "looking around", LED patterns
- Escalates: subtle → obvious → insistent
- Stops immediately on any user interaction
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
class AttentionConfig:
    """Attention seek reflex configuration."""

    # Neutral position
    center_yaw: int = 0
    center_pitch: int = 12
    move_speed: int = 40

    # Timing
    idle_threshold: float = 300.0      # 5 minutes before first attention seek
    escalation_interval: float = 60.0  # escalate every minute
    behavior_duration: float = 3.0     # each behavior lasts 3s
    cooldown: float = 10.0             # min time between behaviors

    # Escalation levels
    # Level 1: subtle (small tilt, soft LED)
    # Level 2: obvious (larger movement, colored LED)
    # Level 3: insistent (repeated, bright LED)
    max_escalation_level: int = 3

    # Safety
    max_head_speed: int = 200
    pitch_min: int = 5
    pitch_max: int = 85
    yaw_min: int = -90
    yaw_max: int = 90


DEFAULT_CONFIG = AttentionConfig()


# =============================================================================
# Behaviors by escalation level
# =============================================================================


BEHAVIORS = {
    1: [  # Subtle
        {"name": "curious_tilt", "face": "thinking", "yaw_range": (-15, 15), "pitch_offset": (-5, 5), "led": (100, 200, 255), "speed": 20},
        {"name": "side_glance", "face": "surprised", "yaw_range": (-25, 25), "pitch_offset": (0, 0), "led": (200, 255, 100), "speed": 25},
        {"name": "ear_perk", "face": "happy", "yaw_range": (-10, 10), "pitch_offset": (-10, 0), "led": (150, 255, 150), "speed": 15},
    ],
    2: [  # Obvious
        {"name": "look_around", "face": "surprised", "yaw_range": (-40, 40), "pitch_offset": (-5, 10), "led": (255, 200, 0), "speed": 35},
        {"name": "pleading", "face": "sad", "yaw_range": (-20, 20), "pitch_offset": (10, 20), "led": (255, 100, 150), "speed": 20},
        {"name": "excited_bounce", "face": "happy", "yaw_range": (-30, 30), "pitch_offset": (-5, 15), "led": (0, 255, 200), "speed": 40},
    ],
    3: [  # Insistent
        {"name": "desperate_scan", "face": "surprised", "yaw_range": (-60, 60), "pitch_offset": (-10, 10), "led": (255, 50, 50), "speed": 50},
        {"name": "puppy_eyes", "face": "sad", "yaw_range": (-15, 15), "pitch_offset": (15, 25), "led": (255, 150, 200), "speed": 15},
        {"name": "jump_around", "face": "happy", "yaw_range": (-45, 45), "pitch_offset": (-10, 20), "led": (255, 255, 100), "speed": 45},
    ],
}


# =============================================================================
# State
# =============================================================================


@dataclass
class AttentionStateData:
    """Mutable state for attention seek reflex."""

    behavior_task: asyncio.Task | None = None
    escalation_task: asyncio.Task | None = None
    current_level: int = 0
    last_interaction_time: float = 0.0
    is_active: bool = False


# =============================================================================
# Reflex Logic
# =============================================================================


class AttentionReflex:
    """Attention seeking reflex with escalating behaviors."""

    def __init__(self, config: AttentionConfig = DEFAULT_CONFIG):
        self.config = config
        self.state = AttentionStateData()

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

    async def _run_behavior(self, behavior: dict):
        """Execute a single attention behavior."""
        cfg = self.config

        yaw = random.randint(*behavior["yaw_range"])
        pitch = cfg.center_pitch + random.randint(*behavior["pitch_offset"])
        yaw = max(cfg.yaw_min, min(cfg.yaw_max, yaw))
        pitch = max(cfg.pitch_min, min(cfg.pitch_max, pitch))

        r, g, b = behavior["led"]
        await self._safe_call("set_avatar", face=behavior["face"])
        await self._safe_call("move_head", yaw=yaw, pitch=pitch, speed=behavior["speed"])
        await self._safe_call("set_all_leds", r=r, g=g, b=b)

        await asyncio.sleep(cfg.behavior_duration)

        # Return to center
        await self._safe_call("set_avatar", face="idle")
        await self._safe_call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch, speed=cfg.move_speed)
        await self._safe_call("clear_leds")

    async def _behavior_loop(self):
        """Main behavior loop with escalation."""
        cfg = self.config
        self.state.is_active = True
        self.state.current_level = 1

        try:
            # Initial wait
            await asyncio.sleep(cfg.idle_threshold)

            while self.state.is_active:
                # Check if we should escalate
                elapsed = asyncio.get_event_loop().time() - self.state.last_interaction_time
                target_level = min(cfg.max_escalation_level, 1 + int(elapsed / cfg.escalation_interval))

                if target_level > self.state.current_level:
                    self.state.current_level = target_level

                # Pick random behavior from current level
                behaviors = BEHAVIORS.get(self.state.current_level, BEHAVIORS[1])
                behavior = random.choice(behaviors)

                await self._run_behavior(behavior)

                # Cooldown before next behavior
                await asyncio.sleep(cfg.cooldown)

        except asyncio.CancelledError:
            pass
        finally:
            self.state.is_active = False

    async def _escalation_monitor(self):
        """Monitor for escalation (runs in parallel)."""
        cfg = self.config
        try:
            while self.state.is_active:
                await asyncio.sleep(cfg.escalation_interval)
                if self.state.current_level < cfg.max_escalation_level:
                    self.state.current_level += 1
        except asyncio.CancelledError:
            pass

    async def start_seeking(self):
        """Start attention seeking (called on attention_idle event)."""
        if self.state.is_active:
            return

        self.state.last_interaction_time = asyncio.get_event_loop().time()
        self.state.current_level = 1

        self.state.behavior_task = asyncio.create_task(self._behavior_loop())
        self.state.escalation_task = asyncio.create_task(self._escalation_monitor())

    async def stop_seeking(self):
        """Stop attention seeking (called on any user interaction)."""
        if not self.state.is_active:
            return

        self.state.is_active = False

        for task in [self.state.behavior_task, self.state.escalation_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Return to neutral
        cfg = self.config
        await self._safe_call("set_avatar", face="idle")
        await self._safe_call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch, speed=cfg.move_speed)
        await self._safe_call("clear_leds")

        self.state.behavior_task = None
        self.state.escalation_task = None

    async def on_user_interaction(self, event: dict):
        """Any user interaction resets attention seeking."""
        self.state.last_interaction_time = asyncio.get_event_loop().time()
        if self.state.is_active and self.state.current_level > 1:
            # De-escalate
            self.state.current_level = max(1, self.state.current_level - 1)

    async def on_attention_idle(self, event: dict):
        """Triggered after idle_threshold seconds of no interaction."""
        await self.start_seeking()

    async def on_user_approached(self, event: dict):
        """User came close - happy reaction."""
        if self.state.is_active:
            await self.stop_seeking()

        # Happy greeting
        await self._safe_call("set_avatar", face="happy")
        await self._safe_call("move_head", yaw=10, pitch=10, speed=60)
        await self._safe_call("set_all_leds", r=0, g=255, b=100)
        await asyncio.sleep(1.5)
        cfg = self.config
        await self._safe_call("set_avatar", face="idle")
        await self._safe_call("move_head", yaw=cfg.center_yaw, pitch=cfg.center_pitch, speed=cfg.move_speed)
        await self._safe_call("clear_leds")

    async def on_user_left(self, event: dict):
        """User walked away - reset timer."""
        self.state.last_interaction_time = asyncio.get_event_loop().time()


# =============================================================================
# Global Instance & Event Handlers
# =============================================================================

_attention_reflex = AttentionReflex()


@on_event("attention_idle", priority=1, conflict_group="head_movement")
async def on_attention_idle(event: dict):
    """No interaction for 5 minutes."""
    await _attention_reflex.on_attention_idle(event)


@on_event("user_interaction", priority=1, conflict_group="head_movement")
async def on_user_interaction(event: dict):
    """Any user interaction (message, touch, proximity)."""
    await _attention_reflex.on_user_interaction(event)


@on_event("user_approached", priority=1, conflict_group="head_movement")
async def on_user_approached(event: dict):
    """User approached (proximity sensor)."""
    await _attention_reflex.on_user_approached(event)


@on_event("user_left", priority=1, conflict_group="head_movement")
async def on_user_left(event: dict):
    """User left proximity."""
    await _attention_reflex.on_user_left(event)


@on_cleanup("attention_idle")
@on_cleanup("user_interaction")
@on_cleanup("user_approached")
@on_cleanup("user_left")
async def cleanup_attention():
    """Cleanup: ensure neutral."""
    await _attention_reflex.stop_seeking()