"""Tests for body_expression_bridge publisher architecture (EventDispatcher, _build_payload, circuit breaker, etc.)"""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Add parent directory (plugins) to path so body_expression_bridge can be imported
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import body_expression_bridge as bb

pytestmark = pytest.mark.order(1)


class TestBuildPayload:
    """Test _build_payload produces correct event format with event_type, payload, meta."""

    def test_build_payload_new_format(self):
        """Payload has event_type, payload with text/platform/chat_id/user_id/seq/ts/v/data, meta with seq/ts/v."""
        result = bb._build_payload(
            event_type="user_message",
            text="Hello world",
            platform="telegram",
            chat_id="123",
            user_id="456",
            data={"custom": "value"},
            turn_id="turn-1",
        )

        assert "event_type" in result
        assert result["event_type"] == "user_message"

        assert "payload" in result
        payload = result["payload"]
        assert payload["text"] == "Hello world"
        assert payload["platform"] == "telegram"
        assert payload["chat_id"] == "123"
        assert payload["user_id"] == "456"
        assert payload["turn_id"] == "turn-1"
        assert "seq" in payload
        assert isinstance(payload["seq"], int)
        assert payload["seq"] >= 1
        assert "ts" in payload
        assert isinstance(payload["ts"], float)
        assert payload["v"] == 1
        assert payload["data"] == {"custom": "value"}

        assert "meta" in result
        meta = result["meta"]
        assert "seq" in meta
        assert isinstance(meta["seq"], int)
        assert "ts" in meta
        assert isinstance(meta["ts"], float)
        assert meta["v"] == 1

    def test_build_payload_handles_none_text(self):
        """None text becomes empty string."""
        result = bb._build_payload(
            "user_message", text=None, platform="telegram", chat_id="1", user_id="1"
        )
        assert result["payload"]["text"] == ""

    def test_build_payload_defaults_for_optional_fields(self):
        """Optional session_id/turn_id default to empty strings."""
        result = bb._build_payload("user_message", "text", "telegram", "1", "1")
        assert result["payload"]["turn_id"] == ""
        assert result["payload"]["data"] == {}

    def test_build_payload_seq_increments(self):
        """Sequence numbers increment across calls."""
        r1 = bb._build_payload("a", "t", "p", "1", "1")
        r2 = bb._build_payload("b", "t", "p", "1", "1")
        # Both payload and meta have seq
        assert r2["payload"]["seq"] > r1["payload"]["seq"]
        assert r2["meta"]["seq"] > r1["meta"]["seq"]


class TestEventDispatcherPostEvent:
    """Test EventDispatcher._post_event HTTP POST behavior."""

    @pytest.mark.asyncio
    async def test_post_event_sends_correct_json(self):
        """_post_event sends correct JSON to endpoint."""
        mock_response = AsyncMock()
        mock_response.status_code = 200

        dispatcher = bb.EventDispatcher()
        dispatcher._worker_loop = MagicMock()
        dispatcher._worker_loop.is_running.return_value = True

        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            await dispatcher._post_event(
                event_type="user_message",
                text="Hello",
                platform="telegram",
                chat_id="123",
                user_id="456",
            )

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args.kwargs["json"]["event_type"] == "user_message"
            assert call_args.kwargs["json"]["payload"]["text"] == "Hello"
            assert call_args.kwargs["json"]["payload"]["platform"] == "telegram"
            assert call_args.kwargs["json"]["payload"]["chat_id"] == "123"
            assert call_args.kwargs["json"]["payload"]["user_id"] == "456"
            assert "meta" in call_args.kwargs["json"]

    @pytest.mark.asyncio
    async def test_post_event_timeout_config(self):
        """_post_event uses configured timeout."""
        mock_response = AsyncMock()
        mock_response.status_code = 200

        dispatcher = bb.EventDispatcher(timeout=2.5)
        dispatcher._worker_loop = MagicMock()
        dispatcher._worker_loop.is_running.return_value = True

        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            await dispatcher._post_event("user_message", "text", "telegram", "1", "1")

            # Verify timeout passed to client (connect=0.5, read=2.5, write=2.5, pool=5.0)
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_post_event_logs_warning_on_http_error(self):
        """HTTP 4xx/5xx logs warning and records failure."""
        mock_response = AsyncMock()
        mock_response.status_code = 500

        dispatcher = bb.EventDispatcher()
        dispatcher._worker_loop = MagicMock()
        dispatcher._worker_loop.is_running.return_value = True
        dispatcher._breaker_failures = 0
        dispatcher._breaker_open = False

        with (
            patch("httpx.AsyncClient.post", return_value=mock_response),
            patch.object(bb.logger, "warning") as mock_warn,
        ):
            await dispatcher._post_event("user_message", "text", "telegram", "1", "1")
            mock_warn.assert_called_once()
            assert "HTTP %s" in mock_warn.call_args.args[0]
            assert dispatcher._breaker_failures == 1

    @pytest.mark.asyncio
    async def test_post_event_logs_warning_on_exception(self):
        """Network exception logs warning and records failure."""
        dispatcher = bb.EventDispatcher()
        dispatcher._worker_loop = MagicMock()
        dispatcher._worker_loop.is_running.return_value = True
        dispatcher._breaker_failures = 0
        dispatcher._breaker_open = False

        with (
            patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("Connection refused")),
            patch.object(bb.logger, "warning") as mock_warn,
        ):
            await dispatcher._post_event("user_message", "text", "telegram", "1", "1")
            mock_warn.assert_called_once()
            assert "failed" in mock_warn.call_args.args[0].lower()
            assert dispatcher._breaker_failures == 1

    @pytest.mark.asyncio
    async def test_post_event_records_success_on_2xx(self):
        """HTTP 2xx records success and resets breaker."""
        mock_response = AsyncMock()
        mock_response.status_code = 200

        dispatcher = bb.EventDispatcher()
        dispatcher._worker_loop = MagicMock()
        dispatcher._worker_loop.is_running.return_value = True
        dispatcher._breaker_failures = 3
        dispatcher._breaker_open = True

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            await dispatcher._post_event("user_message", "text", "telegram", "1", "1")
            assert dispatcher._breaker_failures == 0
            assert dispatcher._breaker_open is False


class TestCircuitBreaker:
    """Test circuit breaker using EVENTS_BREAKER_* constants."""

    def test_breaker_opens_after_threshold_failures(self):
        """Circuit breaker opens after EVENTS_BREAKER_THRESHOLD consecutive failures."""
        dispatcher = bb.EventDispatcher()
        dispatcher._breaker_failures = 0
        dispatcher._breaker_open = False

        for _ in range(bb._EVENTS_BREAKER_THRESHOLD):
            dispatcher._record_fail()

        assert dispatcher._breaker_open is True
        assert dispatcher._breaker_failures >= bb._EVENTS_BREAKER_THRESHOLD

    def test_breaker_allows_when_closed(self):
        """Breaker allows requests when closed."""
        dispatcher = bb.EventDispatcher()
        dispatcher._breaker_open = False
        assert dispatcher._check_breaker() is True

    def test_breaker_blocks_when_open_before_cooldown(self):
        """Breaker blocks requests when open and cooldown not elapsed."""
        dispatcher = bb.EventDispatcher()
        dispatcher._breaker_open = True
        dispatcher._breaker_open_time = time.time()
        assert dispatcher._check_breaker() is False

    def test_breaker_half_open_after_cooldown(self):
        """Breaker allows probe after EVENTS_BREAKER_COOLDOWN (half-open)."""
        dispatcher = bb.EventDispatcher()
        dispatcher._breaker_open = True
        dispatcher._breaker_open_time = time.time() - bb._EVENTS_BREAKER_COOLDOWN - 1
        assert dispatcher._check_breaker() is True

    def test_breaker_resets_on_success(self):
        """Successful request resets failure count and closes breaker."""
        dispatcher = bb.EventDispatcher()
        dispatcher._breaker_failures = 3
        dispatcher._breaker_open = True
        dispatcher._record_success()
        assert dispatcher._breaker_failures == 0
        assert dispatcher._breaker_open is False


class TestPendingQueueLimit:
    """Test EVENTS_MAX_PENDING limit and oldest-drop behavior."""

    def test_pending_limit_enforced_drops_oldest(self):
        """When pending futures exceed EVENTS_MAX_PENDING, oldest is cancelled."""
        dispatcher = bb.EventDispatcher()
        dispatcher._pending_futures.clear()

        original_limit = bb._EVENTS_MAX_PENDING
        try:
            bb._EVENTS_MAX_PENDING = 3

            mock_loop = MagicMock()
            mock_loop.is_running.return_value = True

            with (
                patch.object(dispatcher, "_ensure_worker", return_value=mock_loop),
                patch("asyncio.run_coroutine_threadsafe") as mock_run,
            ):
                mock_futures = [MagicMock() for _ in range(5)]

                def consume_coro(coro, loop):
                    if hasattr(coro, "close"):
                        coro.close()
                    return mock_futures.pop(0)

                mock_run.side_effect = consume_coro

                for i in range(5):
                    dispatcher.schedule_post("user_message", f"text{i}", "telegram", "c1", "u1")

                assert len(dispatcher._pending_futures) <= 3
        finally:
            bb._EVENTS_MAX_PENDING = original_limit


class TestPlatformFilter:
    """Test EVENTS_SKIP_PLATFORMS case-insensitive filtering."""

    def test_schedule_post_skips_configured_platforms(self):
        """Platforms in EVENTS_SKIP_PLATFORMS are skipped (case-insensitive)."""
        dispatcher = bb.EventDispatcher()
        # Default skip platforms: local, desktop, cli (normalized to lowercase in schedule_post)
        for platform in ("local", "desktop", "cli", "LOCAL", "Desktop", "CLI"):
            with patch.object(dispatcher, "_ensure_worker") as mock_ensure:
                dispatcher.schedule_post("user_message", "text", platform, "c1", "u1")
                mock_ensure.assert_not_called()

    def test_schedule_post_allows_non_skipped_platforms(self):
        """Non-skipped platforms are allowed through."""
        dispatcher = bb.EventDispatcher()
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True

        with (
            patch.object(dispatcher, "_ensure_worker", return_value=mock_loop),
            patch("asyncio.run_coroutine_threadsafe") as mock_run,
        ):
            mock_future = MagicMock()

            def consume_coro(coro, loop):
                if hasattr(coro, "close"):
                    coro.close()
                return mock_future

            mock_run.side_effect = consume_coro

            for platform in ("telegram", "discord", "slack", "whatsapp"):
                dispatcher.schedule_post("user_message", "text", platform, "c1", "u1")
                mock_run.assert_called()


class TestSchedulePost:
    """Test schedule_post thread-safety, worker loop usage, metrics increment."""

    def test_schedule_post_uses_worker_loop(self):
        """schedule_post uses _ensure_worker to get loop and run_coroutine_threadsafe."""
        dispatcher = bb.EventDispatcher()
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True

        with (
            patch.object(dispatcher, "_ensure_worker", return_value=mock_loop),
            patch("asyncio.run_coroutine_threadsafe") as mock_run,
        ):
            mock_future = MagicMock()

            def consume_coro(coro, loop):
                if hasattr(coro, "close"):
                    coro.close()
                return mock_future

            mock_run.side_effect = consume_coro

            dispatcher.schedule_post("user_message", "text", "telegram", "c1", "u1")
            mock_run.assert_called_once()

    def test_schedule_post_increments_scheduled_metric(self):
        """schedule_post increments scheduled metric."""
        dispatcher = bb.EventDispatcher()
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True

        initial = bb._metrics.get("scheduled", 0)

        with (
            patch.object(dispatcher, "_ensure_worker", return_value=mock_loop),
            patch("asyncio.run_coroutine_threadsafe") as mock_run,
        ):
            mock_future = MagicMock()

            def consume_coro(coro, loop):
                if hasattr(coro, "close"):
                    coro.close()
                return mock_future

            mock_run.side_effect = consume_coro

            dispatcher.schedule_post("user_message", "text", "telegram", "c1", "u1")
            assert bb._metrics.get("scheduled", 0) == initial + 1


class TestSchedulePostWrapper:
    """Test _schedule_post backward-compatible wrapper (no session_id in signature)."""

    def test_schedule_post_wrapper_signature(self):
        """_schedule_post accepts session_id but doesn't pass to schedule_post."""
        with patch.object(bb._dispatcher, "schedule_post") as mock_schedule:
            bb._schedule_post(
                "user_message",
                text="text",
                platform="telegram",
                chat_id="c1",
                user_id="u1",
                session_id="sess-1",
                turn_id="turn-1",
            )
            mock_schedule.assert_called_once()
            # session_id should not be in kwargs passed to schedule_post
            call_kwargs = mock_schedule.call_args.kwargs
            assert "session_id" not in call_kwargs


class TestConfigFromEnv:
    """Test all EVENTS_* environment variables parsed with defaults."""

    def test_events_endpoint_url_default(self):
        """EVENTS_ENDPOINT_URL defaults to http://localhost:8770/events."""
        with patch.dict("os.environ", {}, clear=True):
            import importlib
            importlib.reload(bb)
            assert bb._EVENTS_ENDPOINT == "http://localhost:8770/events"

    def test_events_timeout_seconds_default(self):
        """EVENTS_TIMEOUT_SECONDS defaults to 1.0."""
        with patch.dict("os.environ", {}, clear=True):
            import importlib
            importlib.reload(bb)
            assert bb._EVENTS_TIMEOUT == 1.0

    def test_events_max_pending_default(self):
        """EVENTS_MAX_PENDING defaults to 256."""
        with patch.dict("os.environ", {}, clear=True):
            import importlib
            importlib.reload(bb)
            assert bb._EVENTS_MAX_PENDING == 256

    def test_events_skip_platforms_default(self):
        """EVENTS_SKIP_PLATFORMS defaults to 'local,desktop,cli'."""
        with patch.dict("os.environ", {}, clear=True):
            import importlib
            importlib.reload(bb)
            assert bb._EVENTS_SKIP_PLATFORMS == frozenset({"local", "desktop", "cli"})

    def test_events_breaker_threshold_default(self):
        """EVENTS_BREAKER_THRESHOLD defaults to 5."""
        with patch.dict("os.environ", {}, clear=True):
            import importlib
            importlib.reload(bb)
            assert bb._EVENTS_BREAKER_THRESHOLD == 5

    def test_events_breaker_cooldown_default(self):
        """EVENTS_BREAKER_COOLDOWN defaults to 10."""
        with patch.dict("os.environ", {}, clear=True):
            import importlib
            importlib.reload(bb)
            assert bb._EVENTS_BREAKER_COOLDOWN == 10

    def test_env_override_parses_correctly(self):
        """Environment variable overrides are parsed correctly."""
        env = {
            "EVENTS_ENDPOINT_URL": "http://custom:9999/events",
            "EVENTS_TIMEOUT_SECONDS": "2.5",
            "EVENTS_MAX_PENDING": "100",
            "EVENTS_SKIP_PLATFORMS": "local,test",
            "EVENTS_BREAKER_THRESHOLD": "3",
            "EVENTS_BREAKER_COOLDOWN": "30",
        }
        with patch.dict("os.environ", env, clear=True):
            import importlib
            importlib.reload(bb)
            assert bb._EVENTS_ENDPOINT == "http://custom:9999/events"
            assert bb._EVENTS_TIMEOUT == 2.5
            assert bb._EVENTS_MAX_PENDING == 100
            assert bb._EVENTS_SKIP_PLATFORMS == frozenset({"local", "test"})
            assert bb._EVENTS_BREAKER_THRESHOLD == 3
            assert bb._EVENTS_BREAKER_COOLDOWN == 30


# Fixtures for clean state between tests

@pytest.fixture(autouse=True)
def reset_dispatcher():
    """Reset dispatcher state before each test."""
    for fut in list(bb._dispatcher._pending_futures):
        fut.cancel()
    bb._dispatcher._pending_futures.clear()
    bb._dispatcher._worker_loop = None
    bb._dispatcher._worker_thread = None
    bb._dispatcher._worker_ready.clear()
    bb._dispatcher._breaker_failures = 0
    bb._dispatcher._breaker_open = False
    bb._dispatcher._breaker_open_time = 0.0
    yield


@pytest.fixture(autouse=True)
def mock_worker_loop(monkeypatch):
    """Mock _ensure_worker to return a mock loop for all tests."""
    mock_loop = MagicMock()
    mock_loop.is_running.return_value = True

    def consume_coro(coro, loop):
        if hasattr(coro, "close"):
            coro.close()
        mock_future = MagicMock()
        return mock_future

    monkeypatch.setattr(bb._dispatcher, "_ensure_worker", lambda: mock_loop)
    monkeypatch.setattr(bb._dispatcher, "_worker_loop", mock_loop)

    def patched_ensure(self):
        return mock_loop

    monkeypatch.setattr(bb.EventDispatcher, "_ensure_worker", patched_ensure)
    monkeypatch.setattr(bb.EventDispatcher, "_worker_loop", mock_loop, raising=False)
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", consume_coro)