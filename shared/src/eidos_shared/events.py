import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class EventType(Enum):
    USER_MESSAGE = "user_message"
    USER_RESPONSE = "user_response"
    THINKING_START = "thinking_start"
    THINKING_END = "thinking_end"
    TICK_START = "tick_start"
    TICK_END = "tick_end"
    PERCEIVE_RESULT = "perceive_result"
    PREDICT_RESULT = "predict_result"
    LEARN_RESULT = "learn_result"
    ACTIVATE_ACTION = "activate_action"
    GOVERNANCE_BLOCK = "governance_block"
    STRESS_DETECTED = "stress_detected"
    DESIRE_GENERATED = "desire_generated"
    GOAL_CREATED = "goal_created"
    DELIVERY_SCHEDULED = "delivery_scheduled"
    DELIVERY_EXECUTED = "delivery_executed"
    DREAM_CYCLE_START = "dream_cycle_start"
    DREAM_CYCLE_END = "dream_cycle_end"


@dataclass
class Event:
    event_type: EventType
    payload: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_service: str = ""
