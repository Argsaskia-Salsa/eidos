import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from reflexd.gateway_client import GatewayClient
from reflexd.registry import clear, on_event
from reflexd.server import (
    EventCancelledError,
    _running,
    cancel_reflex,
    handle_inject_event,
)


@pytest.fixture(autouse=True)
def reset_state():
    clear()
    _running.clear()
    yield
    clear()
    _running.clear()


class TestCancelReflex:
    @pytest.mark.asyncio
    async def test_cancel_reflex_success(self):
        mock_client = AsyncMock(spec=GatewayClient)

        cancelled = False

        async def handler(data):
            nonlocal cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled = True
                raise

        on_event("test_event")(handler)

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            task = asyncio.create_task(handle_inject_event("test_event", {}))
            await asyncio.sleep(0.01)

            success = await cancel_reflex("test_event")

            assert success is True
            assert cancelled is True

            try:
                await task
            except EventCancelledError:
                pass

    @pytest.mark.asyncio
    async def test_cancel_reflex_not_running(self):
        mock_client = AsyncMock(spec=GatewayClient)

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            success = await cancel_reflex("nonexistent")
            assert success is False

    @pytest.mark.asyncio
    async def test_cancel_reflex_runs_cleanup(self):
        mock_client = AsyncMock(spec=GatewayClient)

        cleanup_called = False

        async def cleanup_fn():
            nonlocal cleanup_called
            cleanup_called = True

        @on_event("test_cleanup_event", cleanup=cleanup_fn)
        async def handler(data):
            await asyncio.sleep(10)

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            task = asyncio.create_task(handle_inject_event("test_cleanup_event", {}))
            await asyncio.sleep(0.01)

            await cancel_reflex("test_cleanup_event")

            assert cleanup_called is True

            try:
                await task
            except EventCancelledError:
                pass


class TestPreemptLowerPriority:
    @pytest.mark.asyncio
    async def test_high_priority_preempts_low_priority(self):
        mock_client = AsyncMock(spec=GatewayClient)

        low_cancelled = False
        high_cancelled = False

        async def low_handler(data):
            nonlocal low_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                low_cancelled = True
                raise

        async def high_handler(data):
            nonlocal high_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                high_cancelled = True
                raise

        on_event("low_priority", priority=1)(low_handler)
        on_event("high_priority", priority=10)(high_handler)

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            low_task = asyncio.create_task(handle_inject_event("low_priority", {}))
            await asyncio.sleep(0.01)

            high_task = asyncio.create_task(handle_inject_event("high_priority", {}))
            await asyncio.sleep(0.01)

            assert low_cancelled is True
            assert high_cancelled is False

            try:
                await low_task
            except EventCancelledError:
                pass

            try:
                await high_task
            except EventCancelledError:
                pass

    @pytest.mark.asyncio
    async def test_same_conflict_group_preempts(self):
        mock_client = AsyncMock(spec=GatewayClient)

        first_cancelled = False
        second_cancelled = False

        async def first_handler(data):
            nonlocal first_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                first_cancelled = True
                raise

        async def second_handler(data):
            nonlocal second_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                second_cancelled = True
                raise

        on_event("first_event", priority=1, conflict_group="head_movement")(first_handler)
        on_event("second_event", priority=1, conflict_group="head_movement")(second_handler)

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            first_task = asyncio.create_task(handle_inject_event("first_event", {}))
            await asyncio.sleep(0.01)

            second_task = asyncio.create_task(handle_inject_event("second_event", {}))
            await asyncio.sleep(0.01)

            assert first_cancelled is True
            assert second_cancelled is False

            try:
                await first_task
            except EventCancelledError:
                pass

            try:
                await second_task
            except EventCancelledError:
                pass

    @pytest.mark.asyncio
    async def test_conflict_group_overrides_priority(self):
        mock_client = AsyncMock(spec=GatewayClient)

        high_priority_cancelled = False
        low_priority_same_group_cancelled = False

        async def high_handler(data):
            nonlocal high_priority_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                high_priority_cancelled = True
                raise

        async def low_handler(data):
            nonlocal low_priority_same_group_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                low_priority_same_group_cancelled = True
                raise

        on_event("high_priority", priority=10, conflict_group="group_a")(high_handler)
        on_event("low_priority", priority=1, conflict_group="group_a")(low_handler)

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            high_task = asyncio.create_task(handle_inject_event("high_priority", {}))
            await asyncio.sleep(0.01)

            low_task = asyncio.create_task(handle_inject_event("low_priority", {}))
            await asyncio.sleep(0.01)

            assert high_priority_cancelled is True
            assert low_priority_same_group_cancelled is False

            try:
                await high_task
            except EventCancelledError:
                pass

            try:
                await low_task
            except EventCancelledError:
                pass

    @pytest.mark.asyncio
    async def test_different_conflict_groups_higher_priority_preempts(self):
        mock_client = AsyncMock(spec=GatewayClient)

        first_cancelled = False
        second_cancelled = False

        async def first_handler(data):
            nonlocal first_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                first_cancelled = True
                raise

        async def second_handler(data):
            nonlocal second_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                second_cancelled = True
                raise

        on_event("first_event", priority=1, conflict_group="group_a")(first_handler)
        on_event("second_event", priority=10, conflict_group="group_b")(second_handler)

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            first_task = asyncio.create_task(handle_inject_event("first_event", {}))
            await asyncio.sleep(0.01)

            second_task = asyncio.create_task(handle_inject_event("second_event", {}))
            await asyncio.sleep(0.01)

            assert first_cancelled is True
            assert second_cancelled is False

            try:
                await first_task
            except EventCancelledError:
                pass

            try:
                await second_task
            except EventCancelledError:
                pass


class TestSameNameReplacement:
    @pytest.mark.asyncio
    async def test_same_name_replaces_previous(self):
        mock_client = AsyncMock(spec=GatewayClient)

        first_cancelled = False

        async def first_handler(data):
            nonlocal first_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                first_cancelled = True
                raise

        on_event("same_name", priority=5)(first_handler)

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            first_task = asyncio.create_task(handle_inject_event("same_name", {}))
            await asyncio.sleep(0.01)

            second_task = asyncio.create_task(handle_inject_event("same_name", {}))
            await asyncio.sleep(0.01)

            assert first_cancelled is True

            try:
                await first_task
            except EventCancelledError:
                pass

            result = await second_task
            assert result["status"] == "ok"