from datetime import datetime

from eidos_shared.types import (
    AcceptorScore,
    ApperceptionMode,
    ApperceptionResult,
    CouplingType,
    DeliveryDecision,
    DeliveryPriority,
    GoalPriority,
    GoalSpec,
    PreventivePlan,
    ScheduledDelivery,
    StressSignal,
    StressSource,
    SystemState,
    VoiceContract,
)


def test_delivery_priority_enum() -> None:
    assert DeliveryPriority.CRITICAL.value == 0
    assert DeliveryPriority.HIGH.value == 1
    assert DeliveryPriority.MEDIUM.value == 2
    assert DeliveryPriority.LOW.value == 3


def test_goal_priority_enum() -> None:
    assert GoalPriority.CRITICAL.value == 0
    assert GoalPriority.HIGH.value == 1
    assert GoalPriority.MEDIUM.value == 2
    assert GoalPriority.LOW.value == 3


def test_coupling_type_enum() -> None:
    assert CouplingType.TACTICAL_A_W.value == "tactical_A_W"
    assert CouplingType.STRATEGIC_A_W.value == "strategic_A_W"
    assert CouplingType.ETHICAL_A_O.value == "ethical_A_O"
    assert CouplingType.PATTERN_A_W.value == "pattern_A_W"
    assert CouplingType.EXPLORATORY_A_W.value == "exploratory_A_W"
    assert CouplingType.INTRINSIC_A_A.value == "intrinsic_A_A"
    assert CouplingType.TACTICAL_A_A.value == "tactical_A_A"


def test_stress_source_enum() -> None:
    assert StressSource.A_W.value == "A_W"
    assert StressSource.A_O.value == "A_O"
    assert StressSource.A_A.value == "A_A"


def test_apperception_mode_enum() -> None:
    assert ApperceptionMode.NORMAL.value == "normal"
    assert ApperceptionMode.HIGH_STRESS.value == "high_stress"
    assert ApperceptionMode.COHERENCE_BREAK.value == "coherence_break"


def test_system_state_creation() -> None:
    state = SystemState(
        timestamp=datetime.now(),
        valence=0.5,
        arousal=0.3,
        clarity=0.8,
        stress_level=0.2,
        stress_source=StressSource.A_W,
        leading_motive="test_motive",
        coupling_formation_rate=0.1,
        ethical_A_O_idle_hours=5.0,
        valence_gradients={"test": 0.1},
        prediction_error_rate=0.05,
        prers_scores={"dim1": 0.8},
        active_couplings={"c1": 0.5},
    )
    assert state.valence == 0.5
    assert state.stress_source == StressSource.A_W


def test_goal_spec_creation() -> None:
    goal = GoalSpec(
        id="goal_1",
        letter_type="test_type",
        description="Test goal",
        motive="test_motive",
        priority=GoalPriority.HIGH,
        valence="positive",
        prers_dimensions=["dim1", "dim2"],
        estimated_effort=1.5,
        success_criteria="success",
        source="test",
        trigger_hash="hash123",
        created_at=datetime.now(),
    )
    assert goal.id == "goal_1"
    assert goal.priority == GoalPriority.HIGH


def test_scheduled_delivery_creation() -> None:
    delivery = ScheduledDelivery(
        goal_spec_id="goal_1",
        letter_type="test_type",
        delivery_time=datetime.now(),
        slot="morning",
        trigger_hash="hash123",
        priority=DeliveryPriority.HIGH,
        cooldown_remaining_hours=0.0,
    )
    assert delivery.goal_spec_id == "goal_1"
    assert delivery.priority == DeliveryPriority.HIGH


def test_stress_signal_creation() -> None:
    signal = StressSignal(
        level=0.7,
        source=StressSource.A_O,
        coupling_type=CouplingType.ETHICAL_A_O,
        timestamp=datetime.now(),
    )
    assert signal.level == 0.7
    assert signal.source == StressSource.A_O
    assert signal.coupling_type == CouplingType.ETHICAL_A_O


def test_apperception_result_creation() -> None:
    result = ApperceptionResult(
        mode=ApperceptionMode.NORMAL,
        coherence=0.85,
        synthesized_mass={"key": "value"},
        stress_level=0.1,
    )
    assert result.mode == ApperceptionMode.NORMAL
    assert result.coherence == 0.85


def test_preventive_plan_creation() -> None:
    plan = PreventivePlan(
        action={"type": "test"},
        predicted_state={"valence": 0.5},
        acceptor_score={"viability": 0.9},
        confidence=0.8,
    )
    assert plan.confidence == 0.8


def test_acceptor_score_creation() -> None:
    score = AcceptorScore(
        viability=0.9,
        valence=0.5,
        arousal=0.3,
        clarity=0.8,
    )
    assert score.viability == 0.9


def test_delivery_decision_creation() -> None:
    decision = DeliveryDecision(
        should_deliver=True,
        priority=DeliveryPriority.CRITICAL,
        reason="Test reason",
        letter_type="test_type",
    )
    assert decision.should_deliver is True
    assert decision.priority == DeliveryPriority.CRITICAL


def test_voice_contract_creation() -> None:
    contract = VoiceContract(
        voice_id="tatsuya",
        role="Strategic_A_W",
        capabilities=["planning", "analysis"],
        constraints=["no_harm"],
    )
    assert contract.voice_id == "tatsuya"
    assert "planning" in contract.capabilities
