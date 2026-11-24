"""Retain logic: writer thread, queue management, and retain job creation."""

from __future__ import annotations

import atexit
import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from agent.memory_provider import spawn_context_thread

from .client import HindsightClientManager
from .settings import (
    _HINDSIGHT_GLYPH,
    _normalize_retain_tags,
)

logger = logging.getLogger(__name__)

_WRITER_SENTINEL = object()
_RETAIN_OP_POLL_INTERVAL_S = 0.5


class RetainManager:
    """Manages the retain writer thread and queue."""

    def __init__(
        self,
        client_manager: HindsightClientManager,
        get_session_id: Callable[[], str],
        get_parent_session_id: Callable[[], str],
        get_bank_id: Callable[[], str],
        get_retain_async: Callable[[], bool],
        get_retain_context: Callable[[], str],
        get_retain_tags: Callable[[], list[str]],
        get_observation_scopes: Callable[[], Any],
        get_retain_source: Callable[[], str],
        get_retain_user_prefix: Callable[[], str],
        get_retain_assistant_prefix: Callable[[], str],
        get_turn_index: Callable[[], int],
        get_session_turns: Callable[[], list[str]],
        get_metadata_attrs: Callable[[], list[tuple[str, str]]],
        get_event_timestamp: Callable[[], str],
        is_shutting_down: Callable[[], bool],
        status_callback: Callable[[str], None] | None = None,
        retain_indicator: bool = True,
        warning_callback: Callable[[str], None] | None = None,
        platform: str = "cli",
    ):
        self._client_manager = client_manager
        self._get_session_id = get_session_id
        self._get_parent_session_id = get_parent_session_id
        self._get_bank_id = get_bank_id
        self._get_retain_async = get_retain_async
        self._get_retain_context = get_retain_context
        self._get_retain_tags = get_retain_tags
        self._get_observation_scopes = get_observation_scopes
        self._get_retain_source = get_retain_source
        self._get_retain_user_prefix = get_retain_user_prefix
        self._get_retain_assistant_prefix = get_retain_assistant_prefix
        self._get_turn_index = get_turn_index
        self._get_session_turns = get_session_turns
        self._get_metadata_attrs = get_metadata_attrs
        self._get_event_timestamp = get_event_timestamp
        self._is_shutting_down = is_shutting_down
        self._status_callback = status_callback
        self._retain_indicator = retain_indicator
        self._warning_callback = warning_callback
        self._platform = platform

        self._retain_queue: queue.Queue = queue.Queue()
        self._writer_thread: threading.Thread | None = None
        self._sync_thread: threading.Thread | None = None
        self._shutting_down = threading.Event()
        self._atexit_registered = False
        self._pending_retain_ops: set[str] = set()
        self._pending_retain_ops_lock = threading.Lock()
        self._retain_ops_bank_id = ""
        self._last_retained_turn_count = 0

    @property
    def shutting_down_event(self) -> threading.Event:
        return self._shutting_down

    @property
    def last_retained_turn_count(self) -> int:
        return self._last_retained_turn_count

    @last_retained_turn_count.setter
    def last_retained_turn_count(self, value: int) -> None:
        self._last_retained_turn_count = value

    @property
    def retain_ops_bank_id(self) -> str:
        return self._retain_ops_bank_id

    @retain_ops_bank_id.setter
    def retain_ops_bank_id(self, value: str) -> None:
        self._retain_ops_bank_id = value

    def _ensure_writer(self) -> None:
        """Lazy-start the single retain-writer thread."""
        if (thread := self._writer_thread) is not None and thread.is_alive():
            return
        self._shutting_down.clear()
        thread = spawn_context_thread(self._writer_loop, name="hindsight-writer")
        self._writer_thread = self._sync_thread = thread
        thread.start()

    def _register_atexit(self) -> None:
        if not self._atexit_registered:
            self._atexit_registered = True
            atexit.register(self._atexit_shutdown)

    def _writer_loop(self) -> None:
        """Drain the retain queue serially until the sentinel."""
        while True:
            try:
                job = self._retain_queue.get(timeout=1.0)
            except queue.Empty:
                if self._shutting_down.is_set():
                    return
                continue
            try:
                if job is _WRITER_SENTINEL:
                    return
                job()
            except Exception as exc:
                logger.warning("Hindsight retain failed: %s", exc, exc_info=True)
            finally:
                self._retain_queue.task_done()

    def _atexit_shutdown(self) -> None:
        try:
            if not self._shutting_down.is_set():
                self.shutdown()
        except Exception as exc:
            logger.debug("Hindsight atexit shutdown failed: %s", exc)

    def track_retain_ops(self, retain_response: Any, bank_id: str) -> None:
        """Record async operation IDs from retain response."""
        raw_ids = [
            getattr(retain_response, "operation_id", None),
            *(getattr(retain_response, "operation_ids", None) or []),
        ]
        if ids := [str(op) for op in raw_ids if op]:
            self._retain_ops_bank_id = bank_id
            with self._pending_retain_ops_lock:
                self._pending_retain_ops.update(ids)

    def _is_retain_op_complete(self, bank_id: str, op_id: str) -> bool:
        from hindsight_client_api.exceptions import NotFoundException

        try:
            resp = self._client_manager.run_operation(
                lambda client: client.operations.get_operation_status(bank_id=bank_id, operation_id=op_id)
            )
        except NotFoundException:
            return True
        except Exception as exc:
            logger.debug("Prefetch: operation status check failed for %s: %s", op_id, exc)
            return False
        return str(getattr(resp, "status", "") or "").lower() in {"completed", "failed"}

    def wait_for_retains_drained(self, timeout: float) -> bool:
        """Block for queue drain + server-side op completion."""
        deadline = None if timeout <= 0 else time.monotonic() + timeout
        expired = lambda: deadline is not None and time.monotonic() >= deadline
        while self._retain_queue.unfinished_tasks > 0:
            if self._is_shutting_down():
                return False
            if expired():
                logger.debug(
                    "Prefetch: retain drain timed out after %.1fs (%d pending)",
                    timeout,
                    self._retain_queue.unfinished_tasks,
                )
                return False
            time.sleep(0.05)
        return self._wait_for_server_retain_ops(expired, timeout)

    def _wait_for_server_retain_ops(self, expired: Callable[[], bool], timeout: float) -> bool:
        while True:
            with self._pending_retain_ops_lock:
                bank_id = self._retain_ops_bank_id or self._get_bank_id()
                pending = list(self._pending_retain_ops)
            if not pending:
                return True
            if self._is_shutting_down():
                return False
            done: set[str] = set()
            for op_id in pending:
                if self._is_shutting_down():
                    return False
                if expired():
                    break
                if self._is_retain_op_complete(bank_id, op_id):
                    done.add(op_id)
            with self._pending_retain_ops_lock:
                self._pending_retain_ops.difference_update(done)
                if not self._pending_retain_ops:
                    return True
                dropped = len(self._pending_retain_ops) if expired() else 0
                if dropped:
                    self._pending_retain_ops.clear()
            if dropped:
                logger.warning(
                    "Prefetch: server retain visibility timed out after %.1fs; "
                    "dropping %d unresolved op(s) so later prefetches stay "
                    "bounded (recall may miss the just-completed turn)",
                    timeout,
                    dropped,
                )
                return False
            time.sleep(_RETAIN_OP_POLL_INTERVAL_S)

    def _build_metadata(self, message_count: int, turn_index: int) -> dict[str, str]:
        metadata: dict[str, str] = {
            "retained_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "message_count": str(message_count),
            "turn_index": str(turn_index),
        }
        if source := self._get_retain_source():
            metadata["source"] = source
        metadata.update({name: value for name, value in self._get_metadata_attrs() if value})
        return metadata

    def _build_retain_kwargs(
        self,
        content: str,
        *,
        context: str | None = None,
        metadata: dict[str, str] | None = None,
        tags: list[str] | None = None,
        occurred_at: str | None = None,
        update_mode: str | None = None,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "content": content,
            "metadata": metadata or self._build_metadata(message_count=1, turn_index=self._get_turn_index()),
            "timestamp": (occurred_at or "").strip() or self._get_event_timestamp(),
        }
        merged_tags = _normalize_retain_tags(list(self._get_retain_tags()) + _normalize_retain_tags(tags))
        item.update({k: v for k, v in (("context", context), ("update_mode", update_mode)) if v is not None})
        item.update({k: v for k, v in (("tags", merged_tags), ("observation_scopes", self._get_observation_scopes())) if v})
        return item

    def _retain_batch(
        self, item: dict, *, bank_id: str, document_id: str | None = None, retain_async: bool | None = None
    ) -> Any:
        kwargs: dict[str, Any] = {
            "bank_id": bank_id,
            "items": [item],
            "document_id": document_id,
            "retain_async": retain_async,
        }
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        return self._client_manager.run_operation(
            lambda client: client.aretain_batch(**kwargs), operation_name="retain"
        )

    def _make_turn_retain_job(
        self, turns: list[str], *, document_id: str, update_mode: str | None, label: str, track_ops: bool = True
    ) -> Callable[[], None]:
        content = "[" + ",".join(turns) + "]"
        metadata = self._build_metadata(message_count=len(turns) * 2, turn_index=self._get_turn_index())
        lineage = (("session", self._get_session_id()), ("parent", self._get_parent_session_id()))
        tags = [f"{kind}:{sid}" for kind, sid in lineage if sid] or None
        bank_id, retain_async, retain_context = self._get_bank_id(), self._get_retain_async(), self._get_retain_context()

        def _job() -> None:
            item = self._build_retain_kwargs(
                content, context=retain_context, metadata=metadata, tags=tags, update_mode=update_mode
            )
            logger.debug(
                "Hindsight %s: bank=%s, doc=%s, mode=%s, async=%s, content_len=%d, num_turns=%d",
                label,
                bank_id,
                document_id,
                update_mode,
                retain_async,
                len(content),
                len(turns),
            )
            resp = self._retain_batch(item, bank_id=bank_id, document_id=document_id, retain_async=retain_async)
            if retain_async and track_ops:
                self.track_retain_ops(resp, bank_id)
            logger.debug("Hindsight %s succeeded", label)

        return _job

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        retain_every_n_turns: int = 1,
        auto_retain: bool = True,
        turn_counter: list[int],
        last_retained_turn_count: list[int],
        session_turns: list[str],
        document_id: list[str],
    ) -> None:
        """Enqueue a retain for the current turn."""
        why = (
            "auto_retain disabled"
            if not auto_retain
            else "shutting down"
            if self._is_shutting_down()
            else None
        )
        if why:
            logger.debug("sync_turn: skipped (%s)", why)
            return
        if session_id:
            # Update session_id via provider
            pass  # Handled by caller

        now = self._get_event_timestamp()
        session_turns.append(
            json.dumps(
                [
                    {"role": "user", "content": f"{self._get_retain_user_prefix()}: {user_content}", "timestamp": now},
                    {"role": "assistant", "content": f"{self._get_retain_assistant_prefix()}: {assistant_content}", "timestamp": now},
                ],
                ensure_ascii=False,
            )
        )
        turn_counter[0] += 1
        if remainder := turn_counter[0] % retain_every_n_turns:
            logger.debug(
                "sync_turn: buffered turn %d (will retain at turn %d)",
                turn_counter[0],
                turn_counter[0] + (retain_every_n_turns - remainder),
            )
            return

        update_mode = None
        doc_id, update_mode = self._client_manager.check_update_mode_append(self._get_session_id())
        document_id[0] = doc_id
        start = last_retained_turn_count[0] if update_mode == "append" else 0
        turns_to_retain = session_turns[start:]
        if not turns_to_retain:
            logger.debug("sync_turn: skipped append retain; no new turns since last retain")
            return
        logger.debug(
            "sync_turn: retaining %d/%d turns, payload %d chars",
            len(turns_to_retain),
            len(session_turns),
            sum(len(t) for t in turns_to_retain),
        )

        job = self._make_turn_retain_job(
            turns_to_retain, document_id=document_id[0], update_mode=update_mode, label="retain"
        )
        if self._retain_indicator and self._status_callback is not None:
            try:
                self._status_callback(f"{_HINDSIGHT_GLYPH} Hindsight — saving to memory…")
            except Exception:
                logger.debug("Retain indicator emit failed (non-fatal)", exc_info=True)
        self._enqueue_retain(job)
        if update_mode == "append":
            session_turns.clear()
            last_retained_turn_count[0] = 0

    def _enqueue_retain(self, job: Callable[[], None]) -> None:
        self._ensure_writer()
        self._register_atexit()
        self._retain_queue.put(job)

    def flush_on_switch(self) -> None:
        """Flush buffered turns under old session identifiers."""
        session_turns = self._get_session_turns()
        if not session_turns:
            return
        old_document_id, old_update_mode = self._client_manager.check_update_mode_append(self._get_session_id())
        job = self._make_turn_retain_job(
            list(session_turns),
            document_id=old_document_id,
            update_mode=old_update_mode,
            label="flush-on-switch",
            track_ops=False,
        )

        def _flush() -> None:
            try:
                job()
            except Exception as e:
                logger.warning("Hindsight flush-on-switch failed: %s", e, exc_info=True)

        if not self._is_shutting_down():
            self._enqueue_retain(_flush)

    def shutdown(self) -> None:
        logger.debug("Hindsight shutdown: stopping writer + waiting for background threads")
        self._shutting_down.set()
        if (writer := self._writer_thread) is not None and writer.is_alive():
            # Wait for queued jobs to complete before sending sentinel
            deadline = time.monotonic() + 10.0
            while self._retain_queue.unfinished_tasks > 0:
                if time.monotonic() > deadline:
                    break
                time.sleep(0.05)
            self._retain_queue.put(_WRITER_SENTINEL)
            writer.join(timeout=10.0)
            if writer.is_alive():
                logger.warning(
                    "Hindsight writer did not stop within 10s; abandoning %d pending retain(s)",
                    self._retain_queue.qsize(),
                )