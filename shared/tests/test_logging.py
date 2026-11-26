from eidos_shared.logging import setup_logging


def test_setup_logging_returns_logger() -> None:
    logger = setup_logging("test-service", log_level="INFO")

    assert logger is not None
    assert hasattr(logger, "info")
    assert hasattr(logger, "debug")
    assert hasattr(logger, "warning")
    assert hasattr(logger, "error")


def test_setup_logging_binds_service_name() -> None:
    logger = setup_logging("test-service", log_level="DEBUG")

    # Check that the logger has the service bound
    assert logger._context.get("service") == "test-service"


def test_setup_logging_configures_structlog() -> None:
    # Verify structlog is configured properly
    logger = setup_logging("test-service", log_level="INFO")

    # The logger should be a structlog wrapped logger
    assert hasattr(logger, "_logger")
    assert logger._logger.name == "test-service"


def test_setup_logging_json_output() -> None:
    logger = setup_logging("test-service", log_level="INFO")

    # Test that logging methods work without error
    logger.info("test_message", key="value", correlation_id="test-123")
    logger.debug("debug_message", extra_key="extra_value")
    logger.warning("warning_message")
    logger.error("error_message", error="test_error")

    # If we get here without exception, the logging works
    assert True
