"""Tool schemas and handlers for Hindsight."""

from __future__ import annotations

import json
import logging
from typing import Any

from tools.registry import tool_error

from .client import HindsightClientManager
from .recall import RecallManager
from .retain import RetainManager

logger = logging.getLogger(__name__)

RETAIN_SCHEMA = {
    "name": "hindsight_retain",
    "description": (
        "Store information to long-term memory. Hindsight automatically "
        "extracts structured facts, resolves entities, and indexes for retrieval."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The information to store."},
            "context": {"type": "string", "description": "Short label (e.g. 'user preference', 'project decision')."},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional per-call tags to merge with configured default retain tags.",
            },
            "occurred_at": {
                "type": "string",
                "description": (
                    "When the remembered event actually happened, as an ISO-8601 date "
                    "or datetime (e.g. '2026-08-20' or '2026-08-20T14:30:00+02:00'). "
                    "Pass this whenever the memory references a specific event time "
                    "('yesterday', 'last Tuesday', 'on March 3rd') so Hindsight can "
                    "anchor it on the timeline. Omit for timeless facts/preferences."
                ),
            },
        },
        "required": ["content"],
    },
}

RECALL_SCHEMA = {
    "name": "hindsight_recall",
    "description": (
        "Search long-term memory. Returns memories ranked by relevance using "
        "semantic search, keyword matching, entity graph traversal, and reranking."
    ),
    "parameters": {
        "type": "object",
        "required": ["query"],
        "properties": {"query": {"type": "string", "description": "What to search for."}},
    },
}

REFLECT_SCHEMA = {
    "name": "hindsight_reflect",
    "description": (
        "Synthesize a reasoned answer from long-term memories. Unlike recall, "
        "this reasons across all stored memories to produce a coherent response."
    ),
    "parameters": {
        "type": "object",
        "required": ["query"],
        "properties": {"query": {"type": "string", "description": "The question to reflect on."}},
    },
}

APPLY_TEMPLATE_SCHEMA = {
    "name": "hindsight_apply_template",
    "description": (
        "Apply a starter memory template to the current bank. Fetches the "
        "Hermes-tagged template catalog and applies the selected template via "
        "the bank import endpoint. Useful for initializing a new bank with "
        "pre-configured mental models and directives."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "template_id": {
                "type": "string",
                "description": "Optional specific template ID to apply (skips interactive selection).",
            },
            "list_only": {
                "type": "boolean",
                "description": "If true, only list available templates without applying.",
                "default": False,
            },
        },
    },
}


class ToolHandler:
    """Handles tool calls for Hindsight provider."""

    def __init__(
        self,
        client_manager: HindsightClientManager,
        retain_manager: RetainManager,
        recall_manager: RecallManager,
        get_mode: Any,
        get_bank_id: Any,
        get_api_key: Any,
        get_api_url: Any,
        get_config: Any,
    ):
        self._client_manager = client_manager
        self._retain_manager = retain_manager
        self._recall_manager = recall_manager
        self._get_mode = get_mode
        self._get_bank_id = get_bank_id
        self._get_api_key = get_api_key
        self._get_api_url = get_api_url
        self._get_config = get_config

    def get_schemas(self) -> list[dict[str, Any]]:
        if self._get_memory_mode() == "context":
            return []
        schemas = [RETAIN_SCHEMA, RECALL_SCHEMA, REFLECT_SCHEMA]
        if self._get_mode() in ("cloud", "local_external"):
            schemas.append(APPLY_TEMPLATE_SCHEMA)
        return schemas

    def _get_memory_mode(self) -> str:
        # This will be set by the provider
        return getattr(self, "_memory_mode", "hybrid")

    def set_memory_mode(self, mode: str) -> None:
        self._memory_mode = mode

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        handlers = {
            "hindsight_retain": self._tool_retain,
            "hindsight_recall": self._tool_recall,
            "hindsight_reflect": self._tool_reflect,
            "hindsight_apply_template": self._tool_apply_template,
        }
        if tool_name not in handlers:
            return tool_error(f"Unknown tool: {tool_name}")
        try:
            return json.dumps({"result": handlers[tool_name](args)})
        except Exception as e:
            logger.warning("%s failed: %s", tool_name, e, exc_info=True)
            return tool_error(f"Failed: {e}")

    def _tool_retain(self, args: dict) -> str:
        content, context = args["content"], args.get("context")
        # Delegate to retain manager's internal method
        # For now, use the client manager directly
        bank_id = self._get_bank_id()
        item = {
            "content": content,
            "metadata": self._retain_manager._build_metadata(message_count=1, turn_index=0),
            "timestamp": self._retain_manager._get_event_timestamp(),
        }
        merged_tags = self._retain_manager._get_retain_tags() + self._normalize_tags(args.get("tags"))
        if merged_tags:
            item["tags"] = merged_tags
        if context:
            item["context"] = context
        if args.get("occurred_at"):
            item["timestamp"] = args["occurred_at"]
        self._retain_manager._retain_batch(item, bank_id=bank_id)
        logger.debug("Tool hindsight_retain: success")
        return "Memory stored successfully."

    def _normalize_tags(self, tags: Any) -> list[str]:
        from .settings import _normalize_retain_tags
        return _normalize_retain_tags(tags)

    def _tool_recall(self, args: dict) -> str:
        return self._recall_manager.tool_recall(args)

    def _tool_reflect(self, args: dict) -> str:
        return self._recall_manager.tool_reflect(args)

    def _tool_apply_template(self, args: dict) -> str:
        template_id = args.get("template_id")
        list_only = args.get("list_only", False)

        if self._get_mode() not in ("cloud", "local_external"):
            return "Template application is only available in cloud or local_external mode (API must be reachable)."

        if list_only or template_id is None:
            try:
                from .templates import fetch_hermes_templates
                entries = fetch_hermes_templates(self._get_config())
            except Exception as e:
                logger.warning("Failed to fetch templates: %s", e)
                return f"Failed to fetch template catalog: {e}"

            if not entries:
                return "No Hermes-tagged templates found in catalog."

            if list_only:
                lines = ["Available templates:"]
                for e in entries:
                    lines.append(f"  - {e['id']}: {e.get('name', e['id'])} — {e.get('description', '')[:80]}")
                return "\n".join(lines)

            return "Use 'template_id' parameter to apply a specific template, or 'list_only: true' to list available templates."

        try:
            from .templates import (
                apply_template,
                fetch_hermes_templates,
                fetch_manifest,
            )
            entries = fetch_hermes_templates(self._get_config())
        except Exception as e:
            logger.warning("Failed to fetch templates: %s", e)
            return f"Failed to fetch template catalog: {e}"

        entry = next((e for e in entries if e["id"] == template_id), None)
        if not entry:
            return f"Template '{template_id}' not found in catalog."

        try:
            manifest = fetch_manifest(entry, self._get_config())
            apply_template(self._get_api_url(), self._get_bank_id(), self._get_api_key(), manifest)
            logger.info("Applied template %s to bank %s", template_id, self._get_bank_id())
            return f"Successfully applied template '{entry.get('name', template_id)}' to bank '{self._get_bank_id()}'."
        except Exception as e:
            logger.warning("Failed to apply template %s: %s", template_id, e)
            return f"Failed to apply template: {e}"