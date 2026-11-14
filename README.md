# body_bridge

Hermes plugin that forwards agent events to **reflexd** via MCP for real-time coupling-body Agent Channel consumption.

## Overview

The plugin bridges Hermes internal events to reflexd's Agent Channel by posting JSON-RPC calls to the reflexd MCP endpoint (`http://localhost:8770/mcp` by default). This enables reflexd to track and visualize the agent's thought process and communication flow in real time.

## Events Forwarded

| Event | Trigger | Description |
|-------|---------|-------------|
| `user_message` | `post_gateway_admission` | Fired after authentication/admission, inside the session slot. Captures the validated user message. |
| `user_response` | `post_llm_call` | Fired after LLM completes its turn, before response delivery. Captures the assistant's response. |
| `thinking_start` | `llm_execution` middleware (pre) | Fired immediately before LLM generation begins. |
| `thinking_end` | `llm_execution` middleware (post) | Fired immediately after LLM generation completes. |

## Architecture

- **EventDispatcher class**: Encapsulates the worker loop and HTTP posting logic, providing a clean interface for scheduling events.
- **Async worker thread**: A dedicated background thread runs its own `asyncio` event loop, ensuring thread-safe fire-and-forget HTTP posts from both sync (middleware) and async (hook) contexts.
- **Platform filtering**: Events from `local`, `desktop`, or `cli` platforms are skipped to avoid noise from development/local sessions.
- **Graceful degradation**: If reflexd is unavailable, events are dropped with a warning log; Hermes continues unaffected.
- **Configuration via environment variables**: `REFLEXD_MCP_URL` and `REFLEXD_TIMEOUT_SECONDS` for flexible deployment.
- **Comprehensive test suite**: 58 tests (37 unit + 21 integration) with 98% code coverage.

## Installation

```bash
# From Hermes plugin directory
hermes plugins install body_bridge
hermes plugins enable body_bridge
```

Or manually:

```bash
cp -r body_bridge ~/.hermes/plugins/
hermes plugins enable body_bridge
```

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `REFLEXD_MCP_URL` | `http://localhost:8770/mcp` | reflexd MCP endpoint URL |
| `REFLEXD_TIMEOUT_SECONDS` | `1.0` | HTTP request timeout in seconds |

Example:

```bash
export REFLEXD_MCP_URL=http://reflexd:8770/mcp
export REFLEXD_TIMEOUT_SECONDS=2.0
hermes plugins enable body_bridge
```

Ensure reflexd is running with MCP enabled:

```bash
reflexd --mcp-port 8770
```

## Payload Format

All events are sent via JSON-RPC `tools/call` to the `inject_event` tool:

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "inject_event",
    "arguments": {
      "name": "user_message|user_response|thinking_start|thinking_end",
      "data": {
        "text": "message content (empty for thinking events)",
        "platform": "telegram|discord|slack|...",
        "chat_id": "unique chat identifier",
        "user_id": "unique user identifier"
      },
      "type": "inject"
    }
  },
  "id": 1
}
```

## Hooks & Middleware

### `post_gateway_admission` (async hook)
- **Parameters**: `session_key`, `platform`, `source`, `message_id`, `text`
- **Returns**: `(False, None)` — allows message to continue processing
- **Fires**: `user_message` event

### `post_llm_call` (async hook)
- **Parameters**: `session_id`, `task_id`, `turn_id`, `user_message`, `assistant_response`, `conversation_history`, `model`, `platform`, `side_question_token`
- **Returns**: `None` (observer)
- **Fires**: `user_response` event
- **Note**: Extracts `chat_id`/`user_id` from the last user message in conversation history

### `llm_execution` (sync middleware)
- **Parameters**: `request`, `next_call`, `session_id`, `platform`, `chat_id`, `user_id`
- **Returns**: Result of `next_call(request)`
- **Fires**: `thinking_start` before `next_call`, `thinking_end` after (in `finally` block)

## Development

### Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests (unit first, then integration)
pytest tests/ -v

# Run unit tests only
pytest tests/test_unit.py -v

# Run integration tests only
pytest tests/test_integration.py -v

# Coverage report
pytest tests/ --cov=body_bridge --cov-report=term-missing
```

### Linting

```bash
# Check code style
ruff check .

# Auto-format
ruff format .
```

### Test Structure

```
tests/
├── test_unit.py        # 37 unit tests (isolated, fast)
└── test_integration.py # 21 integration tests (protocol, worker, e2e)
```

Current coverage: **98%** (96% on main module)

## Requirements

- Python ≥ 3.11
- `httpx` (for async HTTP calls)

## License

MIT