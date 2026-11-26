from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class DeliveryPriority(Enum):
    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3


class GoalPriority(Enum):
    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3


class CouplingType(Enum):
    TACTICAL_A_W = "tactical_A_W"
    STRATEGIC_A_W = "strategic_A_W"
    ETHICAL_A_O = "ethical_A_O"
    PATTERN_A_W = "pattern_A_W"
    EXPLORATORY_A_W = "exploratory_A_W"
    INTRINSIC_A_A = "intrinsic_A_A"
    TACTICAL_A_A = "tactical_A_A"


class StressSource(Enum):
    A_W = "A_W"
    A_O = "A_O"
    A_A = "A_A"


class ApperceptionMode(Enum):
    NORMAL = "normal"
    HIGH_STRESS = "high_stress"
    COHERENCE_BREAK = "coherence_break"


@dataclass
class SystemState:
    timestamp: datetime
    valence: float
    arousal: float
    clarity: float
    stress_level: float
    stress_source: StressSource
    leading_motive: str
    coupling_formation_rate: float
    ethical_A_O_idle_hours: float
    valence_gradients: dict[str, float]
    prediction_error_rate: float
    prers_scores: dict[str, float]
    active_couplings: dict[str, float]


@dataclass
class GoalSpec:
    id: str
    letter_type: str
    description: str
    motive: str
    priority: GoalPriority
    valence: str
    prers_dimensions: list[str]
    estimated_effort: float
    success_criteria: str
    source: str
    trigger_hash: str
    created_at: datetime


@dataclass
class ScheduledDelivery:
    goal_spec_id: str
    letter_type: str
    delivery_time: datetime
    slot: str
    trigger_hash: str
    priority: DeliveryPriority
    cooldown_remaining_hours: float
    goal_spec: GoalSpec | None = None


@dataclass
class StressSignal:
    level: float
    source: StressSource
    coupling_type: CouplingType
    timestamp: datetime


@dataclass
class ApperceptionResult:
    mode: ApperceptionMode
    coherence: float
    synthesized_mass: dict[str, Any]
    stress_level: float


@dataclass
class PreventivePlan:
    action: dict[str, Any]
    predicted_state: dict[str, Any]
    acceptor_score: dict[str, Any]
    confidence: float


@dataclass
class AcceptorScore:
    viability: float
    valence: float
    arousal: float
    clarity: float


@dataclass
class DeliveryDecision:
    should_deliver: bool
    priority: DeliveryPriority
    reason: str
    letter_type: str
    goal_to_create: GoalSpec | None = None
    letter_id: str = ""
    trigger_hash: str = ""
    state: str = "PENDING"
    cooldown_remaining_hours: float = 0.0
    cooldown_hours: float = 0.0


@dataclass
class VoiceContract:
    voice_id: str
    role: str
    capabilities: list[str]
    constraints: list[str]
