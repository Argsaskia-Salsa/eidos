"""Recall and prefetch logic for Hindsight."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from agent.memory_provider import RecallStatus, spawn_context_thread

from .client import HindsightClientManager
from .settings import _HINDSIGHT_GLYPH

logger = logging.getLogger(__name__)


class RecallManager:
    """Manages recall operations and background prefetch."""

    def __init__(
        self,
        client_manager: HindsightClientManager,
        get_bank_id: Callable[[], str],
        get_budget: Callable[[], str],
        get_recall_max_tokens: Callable[[], int],
        get_recall_tags: Callable[[], list[str] | None],
        get_recall_tags_match: Callable[[], str],
        get_recall_types: Callable[[], list[str]],
        get_recall_prompt_preamble: Callable[[], str],
        get_prefetch_method: Callable[[], str],
        get_auto_recall: Callable[[], bool],
        get_recall_sync: Callable[[], bool],
        get_recall_max_input_chars: Callable[[], int],
        get_memory_mode: Callable[[], str],
        get_recall_indicator: Callable[[], bool],
        is_shutting_down: Callable[[], bool],
        retain_manager: Any,  # RetainManager - for wait_for_retains_drained
        get_prefetch_waits_for_retain: Callable[[], bool],
        get_prefetch_retain_drain_timeout: Callable[[], float],
    ):
        self._client_manager = client_manager
        self._get_bank_id = get_bank_id
        self._get_budget = get_budget
        self._get_recall_max_tokens = get_recall_max_tokens
        self._get_recall_tags = get_recall_tags
        self._get_recall_tags_match = get_recall_tags_match
        self._get_recall_types = get_recall_types
        self._get_recall_prompt_preamble = get_recall_prompt_preamble
        self._get_prefetch_method = get_prefetch_method
        self._get_auto_recall = get_auto_recall
        self._get_recall_sync = get_recall_sync
        self._get_recall_max_input_chars = get_recall_max_input_chars
        self._get_memory_mode = get_memory_mode
        self._get_recall_indicator = get_recall_indicator
        self._is_shutting_down = is_shutting_down
        self._retain_manager = retain_manager
        self._get_prefetch_waits_for_retain = get_prefetch_waits_for_retain
        self._get_prefetch_retain_drain_timeout = get_prefetch_retain_drain_timeout

        self._prefetch_result = ""
        self._prefetch_count = 0
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: threading.Thread | None = None
        self._last_recall_returned = False
        self._last_recall_count = 0

    def _recall_disabled(self) -> bool:
        why = (
            "tools-only mode"
            if self._get_memory_mode() == "tools"
            else "auto_recall disabled"
            if not self._get_auto_recall()
            else "shutting down"
            if self._is_shutting_down()
            else None
        )
        if why:
            logger.debug("Prefetch: skipped (%s)", why)
        return why is not None

    def _recall(self, query: str) -> list:
        kwargs: dict = {
            "bank_id": self._get_bank_id(),
            "query": query,
            "budget": self._get_budget(),
            "max_tokens": self._get_recall_max_tokens(),
        }
        if tags := self._get_recall_tags():
            kwargs.update(tags=tags, tags_match=self._get_recall_tags_match())
        if types := self._get_recall_types():
            kwargs["types"] = types
        resp = self._client_manager.run_operation(
            lambda client: client.arecall(**kwargs), operation_name="recall"
        )
        return resp.results or []

    def _reflect(self, query: str) -> str | None:
        resp = self._client_manager.run_operation(
            lambda client: client.areflect(bank_id=self._get_bank_id(), query=query, budget=self._get_budget()),
            operation_name="reflect",
        )
        return resp.text

    def _do_recall(self, query: str) -> tuple[str, int]:
        if self._get_recall_max_input_chars():
            query = query[: self._get_recall_max_input_chars()]
        try:
            if self._get_prefetch_method() == "reflect":
                logger.debug("Recall: calling reflect (bank=%s, query_len=%d)", self._get_bank_id(), len(query))
                return self._reflect(query) or "", 0
            logger.debug(
                "Recall: calling recall (bank=%s, query_len=%d, budget=%s)",
                self._get_bank_id(),
                len(query),
                self._get_budget(),
            )
            results = self._recall(query)
            logger.debug("Recall: returned %d results", len(results))
            formatted = []
            for r in results:
                if not r.text:
                    continue
                occurred = getattr(r, "occurred_start", None) or getattr(r, "occurred_end", None) or getattr(r, "mentioned_at", None)
                if occurred:
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
                        date_str = dt.strftime("%Y-%m-%d")
                        formatted.append(f"[{date_str}] {r.text}")
                    except Exception:
                        formatted.append(f"[{occurred[:10]}] {r.text}")
                else:
                    formatted.append(f"[date unknown] {r.text}")
            return "\n".join(formatted), len(results)
        except Exception as e:
            logger.debug("Hindsight recall failed: %s", e, exc_info=True)
            return "", 0

    def _finish_prefetch(self, result: str, count: int) -> str:
        self._last_recall_returned, self._last_recall_count = bool(result), count if result else 0
        if not result:
            logger.debug("Prefetch: no results available")
            return ""
        logger.debug("Prefetch: returning %d chars of context", len(result))
        header = self._get_recall_prompt_preamble() or (
            "# Hindsight Memory (persistent cross-session context)\n"
            "The following memories are from the past — each marked with its date.\n"
            "Use this to answer questions about the user and prior sessions. "
            "Do not call tools to look up information that is already present here.\n"
            "IMPORTANT: These are MEMORIES FROM THE PAST, not current facts. "
            "Each line is prefixed with its date [YYYY-MM-DD]. Treat them as historical context."
        )
        return f"{header}\n\n{result}"

    def _join_prefetch(self, timeout: float, *, log: bool = False) -> None:
        if not (self._prefetch_thread and self._prefetch_thread.is_alive()):
            return
        if log:
            logger.debug("Prefetch: waiting for background thread to complete")
        self._prefetch_thread.join(timeout=timeout)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._get_recall_sync():
            return self._finish_prefetch(*(("", 0) if self._recall_disabled() else self._do_recall(query)))
        self._join_prefetch(3.0, log=True)
        with self._prefetch_lock:
            result, count = self._prefetch_result, self._prefetch_count
            self._prefetch_result, self._prefetch_count = "", 0
        return self._finish_prefetch(result, count)

    def recall_status(self) -> RecallStatus | None:
        if not self._get_recall_indicator() or not self._last_recall_returned:
            return None
        return RecallStatus(provider_label="Hindsight", count=self._last_recall_count, glyph=_HINDSIGHT_GLYPH)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if self._get_recall_sync() or self._recall_disabled():
            return

        def _run() -> None:
            if self._get_prefetch_waits_for_retain():
                self._retain_manager.wait_for_retains_drained(self._get_prefetch_retain_drain_timeout())
            text, count = self._do_recall(query)
            if text:
                with self._prefetch_lock:
                    self._prefetch_result, self._prefetch_count = text, count

        self._prefetch_thread = spawn_context_thread(_run, name="hindsight-prefetch")
        self._prefetch_thread.start()

    def tool_recall(self, args: dict) -> str:
        query = args["query"]
        logger.debug("Tool hindsight_recall: bank=%s, query_len=%d, budget=%s", self._get_bank_id(), len(query), self._get_budget())
        results = self._recall(query)
        logger.debug("Tool hindsight_recall: %d results", len(results))
        formatted = []
        for r in results:
            if not r.text:
                continue
            occurred = getattr(r, "occurred_start", None) or getattr(r, "occurred_end", None) or getattr(r, "mentioned_at", None)
            if occurred:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
                    date_str = dt.strftime("%Y-%m-%d")
                    formatted.append(f"[{date_str}] {r.text}")
                except Exception:
                    formatted.append(f"[{occurred[:10]}] {r.text}")
            else:
                formatted.append(f"[date unknown] {r.text}")
        return "\n".join(formatted) or "No relevant memories found."

    def tool_reflect(self, args: dict) -> str:
        query = args["query"]
        logger.debug(
            "Tool hindsight_reflect: bank=%s, query_len=%d, budget=%s", self._get_bank_id(), len(query), self._get_budget()
        )
        text = self._reflect(query) or ""
        logger.debug("Tool hindsight_reflect: response_len=%d", len(text))
        return text or "No relevant memories found."

    def join_prefetch_on_switch(self, timeout: float = 3.0) -> None:
        """Join and clear prefetch state on session switch."""
        self._join_prefetch(timeout)
        with self._prefetch_lock:
            self._prefetch_result = ""