import os
import subprocess
import sys


def test_shared_package_installs() -> None:
    """Test that the shared package can be installed in development mode."""
    # Run from the shared directory
    shared_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", "."],
        cwd=shared_dir,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"Install failed: {result.stderr}"


def test_shared_imports_work() -> None:
    """Test that all exports from eidos_shared can be imported."""
    # This test runs after the package is installed
    from eidos_shared import (
        AcceptorScore,
        ApperceptionMode,
        ApperceptionResult,
        BaseServiceSettings,
        CouplingType,
        DeliveryDecision,
        DeliveryPriority,
        Event,
        EventType,
        GoalPriority,
        GoalSpec,
        MCPClient,
        PreventivePlan,
        ScheduledDelivery,
        StressSignal,
        StressSource,
        SystemState,
        VoiceContract,
        create_mcp_server,
        setup_logging,
    )

    # Just verify they're all importable
    assert BaseServiceSettings is not None
    assert SystemState is not None
    assert GoalSpec is not None
    assert ScheduledDelivery is not None
    assert StressSignal is not None
    assert ApperceptionResult is not None
    assert PreventivePlan is not None
    assert AcceptorScore is not None
    assert DeliveryDecision is not None
    assert VoiceContract is not None
    assert DeliveryPriority is not None
    assert GoalPriority is not None
    assert CouplingType is not None
    assert StressSource is not None
    assert ApperceptionMode is not None
    assert MCPClient is not None
    assert create_mcp_server is not None
    assert Event is not None
    assert EventType is not None
    assert setup_logging is not None
