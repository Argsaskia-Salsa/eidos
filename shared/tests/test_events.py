import uuid
from datetime import datetime

from eidos_shared.events import Event, EventType


def test_event_type_enum() -> None:
    assert EventType.USER_MESSAGE.value == "user_message"
    assert EventType.USER_RESPONSE.value == "user_response"
    assert EventType.THINKING_START.value == "thinking_start"
    assert EventType.THINKING_END.value == "thinking_end"
    assert EventType.TICK_START.value == "tick_start"
    assert EventType.TICK_END.value == "tick_end"
    assert EventType.PERCEIVE_RESULT.value == "perceive_result"
    assert EventType.PREDICT_RESULT.value == "predict_result"
    assert EventType.LEARN_RESULT.value == "learn_result"
    assert EventType.ACTIVATE_ACTION.value == "activate_action"
    assert EventType.GOVERNANCE_BLOCK.value == "governance_block"
    assert EventType.STRESS_DETECTED.value == "stress_detected"
    assert EventType.DESIRE_GENERATED.value == "desire_generated"
    assert EventType.GOAL_CREATED.value == "goal_created"
    assert EventType.DELIVERY_SCHEDULED.value == "delivery_scheduled"
    assert EventType.DELIVERY_EXECUTED.value == "delivery_executed"
    assert EventType.DREAM_CYCLE_START.value == "dream_cycle_start"
    assert EventType.DREAM_CYCLE_END.value == "dream_cycle_end"


def test_event_creation_defaults() -> None:
    event = Event(
        event_type=EventType.TICK_START,
        payload={"tick_count": 42},
        source_service="autonomy-core",
    )

    assert event.event_type == EventType.TICK_START
    assert event.payload == {"tick_count": 42}
    assert event.source_service == "autonomy-core"
    assert isinstance(event.timestamp, datetime)
    assert event.correlation_id is not None
    assert uuid.UUID(event.correlation_id)  # Valid UUID


def test_event_creation_with_custom_values() -> None:
    custom_id = "custom-correlation-id"
    custom_time = datetime(2026, 1, 1, 12, 0, 0)
    custom_meta = {"key": "value"}

    event = Event(
        event_type=EventType.USER_MESSAGE,
        payload={"message": "hello"},
        meta=custom_meta,
        timestamp=custom_time,
        correlation_id=custom_id,
        source_service="gateway",
    )

    assert event.correlation_id == custom_id
    assert event.timestamp == custom_time
    assert event.meta == custom_meta


def test_event_serialization() -> None:
    event = Event(
        event_type=EventType.GOAL_CREATED,
        payload={"goal_id": "goal_1"},
        source_service="autonomy-core",
    )

    # Test that event can be converted to dict (for JSON serialization)
    event_dict = {
        "event_type": event.event_type.value,
        "payload": event.payload,
        "meta": event.meta,
        "timestamp": event.timestamp.isoformat(),
        "correlation_id": event.correlation_id,
        "source_service": event.source_service,
    }

    assert event_dict["event_type"] == "goal_created"
    assert event_dict["payload"]["goal_id"] == "goal_1"  # type: ignore[index]
    assert event_dict["source_service"] == "autonomy-core"
