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
| `platform_event` | `gateway_platform_event` | Fired on platform events (Telegram reactions, edits, etc.). |
| `stream_start` | `on_stream_start` | Fired when streaming generation begins. |
| `stream_delta` | `on_stream_delta` | Fired for streaming text deltas (throttled by 80 chars or 100ms). |
| `stream_end` | `on_stream_end` | Fired when streaming generation ends. |
| `session_end` | `on_session_end` | Fired on per-turn session close. |
| `session_finalize` | `on_session_finalize` | Fired on session boundary cleanup. |
| `api_error` | `api_request_error` | Fired on LLM API failures. |

## Architecture

- **EventDispatcher class**: Encapsulates the worker loop and HTTP posting logic, providing a clean interface for scheduling events.
- **Async worker thread**: A dedicated background thread runs its own `asyncio` event loop, ensuring thread-safe fire-and-forget HTTP posts from both sync (middleware) and async (hook) contexts.
- **Platform filtering**: Events from `local`, `desktop`, or `cli` platforms are skipped to avoid noise from development/local sessions.
- **Graceful degradation**: If reflexd is unavailable, events are dropped with a warning log; Hermes continues unaffected.
- **Configuration via environment variables**: `REFLEXD_MCP_URL` and `REFLEXD_TIMEOUT_SECONDS` for flexible deployment.
- **Comprehensive test suite**: 58 tests (37 unit + 21 integration) with 98% code coverage.

## Installation

### Method 1: Hermes CLI (Recommended)

If you have the Hermes CLI installed, you can install and enable the plugin directly:

```bash
# Install from the plugin directory
hermes plugins install body_bridge
hermes plugins enable body_bridge
```

### Method 2: Manual Installation (Directory-based)

Since this is a directory-based Hermes plugin (not a pip package), copy the plugin folder to your Hermes plugins directory:

**Linux/macOS:**
```bash
# Copy the plugin directory to Hermes plugins folder
cp -r body_bridge ~/.hermes/plugins/

# Enable the plugin
hermes plugins enable body_bridge
```

**Windows (PowerShell):**
```powershell
# Copy the plugin directory to Hermes plugins folder
Copy-Item -Recurse body_bridge $env:USERPROFILE\.hermes\plugins\

# Enable the plugin
hermes plugins enable body_bridge
```

**Windows (Command Prompt):**
```cmd
REM Copy the plugin directory to Hermes plugins folder
xcopy /E /I body_bridge %USERPROFILE%\.hermes\plugins\body_bridge\

REM Enable the plugin
hermes plugins enable body_bridge
```

### Method 3: Custom Plugin Directory

If you use a custom plugin directory, set `HERMES_PLUGINS_DIR` and copy there:

```bash
export HERMES_PLUGINS_DIR=/path/to/your/plugins
cp -r body_bridge $HERMES_PLUGINS_DIR/
hermes plugins enable body_bridge
```

### Verify Installation

After installation, verify the plugin is loaded:

```bash
hermes plugins list
# Should show: body_bridge (enabled)
```

Or check Hermes startup logs for:
```
Body Bridge plugin loaded: registering hooks and middleware
Body Bridge hooks/middleware registered: post_gateway_admission, post_llm_call, ...
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
      "name": "user_message|user_response|thinking_start|thinking_end|platform_event|stream_start|stream_delta|stream_end|session_end|session_finalize|api_error",
      "data": {
        "text": "message content (empty for thinking/stream/session events)",
        "platform": "telegram|discord|slack|...|stream",
        "chat_id": "unique chat identifier (or session_id for stream events)",
        "user_id": "unique user identifier (empty for stream events)",
        "data": {
          "event_type": "platform event type (for platform_event)",
          "payload": "full platform event payload (for platform_event)"
        }
      },
      "type": "inject"
    }
  },
  "id": 1
}
```

**Event-specific notes:**
- `thinking_start`/`thinking_end`: `text` is empty string
- `stream_start`/`stream_delta`/`stream_end`: `platform` is "stream", `chat_id` is session_id, `user_id` is empty
- `platform_event`: includes additional `data` field with `event_type` and `payload`
- `session_end`/`session_finalize`: `text` is empty string
- `api_error`: `text` contains error message, `platform` is provider, `chat_id` is model

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

### `gateway_platform_event` (async hook)
- **Parameters**: `platform`, `event_type`, `payload`, `**kwargs`
- **Returns**: `None` (observer)
- **Fires**: `platform_event` event with `event_type` and full `payload` in data

### `on_stream_start` (async hook)
- **Parameters**: `session_id`, `turn_id`, `iteration`, `model`, `provider`, `surface`, `**kwargs`
- **Returns**: `None` (observer)
- **Fires**: `stream_start` event

### `on_stream_delta` (async hook)
- **Parameters**: `session_id`, `turn_id`, `iteration`, `model`, `provider`, `surface`, `delta`, `kind`, `**kwargs`
- **Returns**: `None` (observer)
- **Fires**: `stream_delta` event (throttled: flushes at 80 chars or 100ms)

### `on_stream_end` (async hook)
- **Parameters**: `session_id`, `turn_id`, `iteration`, `model`, `provider`, `surface`, `final_text`, `finished`, `error`, `**kwargs`
- **Returns**: `None` (observer)
- **Fires**: `stream_end` event with final text

### `pre_gateway_dispatch` (async hook)
- **Parameters**: `event`, `gateway`, `session_store`, `**kwargs`
- **Returns**: `dict | None` — returns `{"action": "skip", "reason": "empty message"}` for empty messages, `None` to continue
- **Fires**: No event (filtering hook)

### `on_session_end` (async hook)
- **Parameters**: `session_id`, `completed`, `interrupted`, `model`, `platform`, `reason`, `task_id`, `turn_id`, `api_request_id`, `**kwargs`
- **Returns**: `None` (observer)
- **Fires**: `session_end` event

### `on_session_finalize` (async hook)
- **Parameters**: `session_id`, `platform`, `reason`, `**kwargs`
- **Returns**: `None` (observer)
- **Fires**: `session_finalize` event

### `api_request_error` (async hook)
- **Parameters**: `task_id`, `turn_id`, `api_request_id`, `session_id`, `platform`, `model`, `provider`, `base_url`, `api_mode`, `api_call_count`, `api_duration`, `started_at`, `ended_at`, `status_code`, `retry_count`, `max_retries`, `retryable`, `reason`, `error`, `request`, `**kwargs`
- **Returns**: `None` (observer)
- **Fires**: `api_error` event with error message

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
├── test_unit.py            # 37 unit tests (isolated, fast)
├── test_integration.py     # 21 integration tests (protocol, worker, e2e)
├── test_hooks.py           # Hook-specific unit tests
└── test_hook_integration.py # Hook integration tests
```

Current coverage: **98%** (96% on main module)

## Requirements

- Python ≥ 3.11
- `httpx` (for async HTTP calls)

## License

MIT