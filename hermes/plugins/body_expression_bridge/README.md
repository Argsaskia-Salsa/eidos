# body_expression_bridge

Hermes plugin that forwards agent dialogue lifecycle events to an **events endpoint** (HTTP POST) for real-time expressive-body reactions (animations, speech-gesture sync, attention cues).

## Overview

The plugin bridges Hermes internal events to an external events endpoint by posting JSON payloads via HTTP POST (default: `http://localhost:8770/events`). This enables downstream consumers (e.g., reflexd) to track and visualize the agent's thought process and communication flow in real time.

**Outbound only**: This plugin does NOT register tools, does NOT read sensors, and does NOT touch motor control. For the inbound L0/L1 path see the separate `body_sensor_bridge` plugin.

## Events Forwarded

| Event              | Trigger                           | Description                                                                                         |
|--------------------|-----------------------------------|-----------------------------------------------------------------------------------------------------|
| `user_message`     | `post_gateway_admission`          | Fired after authentication/admission, inside the session slot. Captures the validated user message. |
| `user_response`    | `post_llm_call`                   | Fired after LLM completes its turn, before response delivery. Captures the assistant's response.    |
| `thinking_start`   | `llm_execution` middleware (pre)  | Fired immediately before LLM generation begins.                                                     |
| `thinking_end`     | `llm_execution` middleware (post) | Fired immediately after LLM generation completes.                                                   |
| `platform_event`   | `gateway_platform_event`          | Fired on platform events (Telegram reactions, edits, etc.).                                         |
| `stream_start`     | `on_stream_start`                 | Fired when streaming generation begins.                                                             |
| `stream_delta`     | `on_stream_delta`                 | Fired for streaming text deltas (throttled by 80 chars or 100ms).                                   |
| `stream_end`       | `on_stream_end`                   | Fired when streaming generation ends.                                                               |
| `session_end`      | `on_session_end`                  | Fired on per-turn session close.                                                                    |
| `session_finalize` | `on_session_finalize`             | Fired on session boundary cleanup.                                                                  |
| `api_error`        | `api_request_error`               | Fired on LLM API failures.                                                                          |

## Architecture

- **EventDispatcher class**: Encapsulates the worker loop and HTTP posting logic, providing a clean interface for scheduling events.
- **Async worker thread**: A dedicated background thread runs its own `asyncio` event loop, ensuring thread-safe fire-and-forget HTTP posts from both sync (middleware) and async (hook) contexts.
- **Platform filtering**: Events from `local`, `desktop`, or `cli` platforms are skipped to avoid noise from development/local sessions.
- **Circuit breaker**: Automatic failure detection with configurable threshold and cooldown; drops events when open to prevent cascade failures.
- **Bounded pending queue**: Limits in-flight requests; drops oldest when full with metrics logging.
- **Graceful degradation**: If the events endpoint is unavailable, events are dropped with a warning log; Hermes continues unaffected.
- **Configuration via environment variables**: Flexible deployment without code changes.
- **Comprehensive test suite**: 178 tests across 5 test files covering unit, integration, hook, and publisher logic.

## Installation

### Method 1: Hermes CLI (Recommended)

If you have the Hermes CLI installed, you can install and enable the plugin directly:

```bash
# Install from the plugin directory
hermes plugins install body_expression_bridge
hermes plugins enable body_expression_bridge
```

### Method 2: Manual Installation (Directory-based)

Since this is a directory-based Hermes plugin (not a pip package), copy or symlink the plugin folder to your Hermes plugins directory:

**Linux/macOS (copy):**
```bash
# Copy the plugin directory to Hermes plugins folder
cp -r body_expression_bridge ~/.hermes/plugins/

# Enable the plugin
hermes plugins enable body_expression_bridge
```

**Linux/macOS (symlink — recommended for development):**
```bash
# Symlink the plugin directory (changes are live, no need to re-copy)
ln -sf /path/to/body_expression_bridge ~/.hermes/plugins/body_expression_bridge

# Enable the plugin
hermes plugins enable body_expression_bridge
```

**Windows (PowerShell):**
```powershell
# Copy the plugin directory to Hermes plugins folder
Copy-Item -Recurse body_expression_bridge $env:USERPROFILE\.hermes\plugins\

# Enable the plugin
hermes plugins enable body_expression_bridge
```

**Windows (Command Prompt):**
```cmd
REM Copy the plugin directory to Hermes plugins folder
xcopy /E /I body_expression_bridge %USERPROFILE%\.hermes\plugins\body_expression_bridge\

REM Enable the plugin
hermes plugins enable body_expression_bridge
```

### Method 3: Custom Plugin Directory

If you use a custom plugin directory, set `HERMES_PLUGINS_DIR` and copy there:

```bash
export HERMES_PLUGINS_DIR=/path/to/your/plugins
cp -r body_expression_bridge $HERMES_PLUGINS_DIR/
hermes plugins enable body_expression_bridge
```

### Verify Installation

After installation, verify the plugin is loaded:

```bash
hermes plugins list
# Should show: body_expression_bridge (enabled)
```

Or check Hermes startup logs for:
```
Body Expression Bridge plugin loaded: registering hooks and middleware
Body Expression Bridge hooks/middleware registered: post_gateway_admission, post_llm_call, ...
```

## Configuration

| Environment Variable       | Default                        | Description                         |
|----------------------------|--------------------------------|-------------------------------------|
| `EVENTS_ENDPOINT_URL`      | `http://localhost:8770/events` | Events endpoint URL (HTTP POST)     |
| `EVENTS_TIMEOUT_SECONDS`   | `1.0`                          | HTTP request timeout in seconds     |
| `EVENTS_MAX_PENDING`       | `256`                          | Maximum pending events in queue     |
| `EVENTS_SKIP_PLATFORMS`    | `local,desktop,cli`            | Comma-separated platforms to skip   |
| `EVENTS_BREAKER_THRESHOLD` | `5`                            | Circuit breaker failure threshold   |
| `EVENTS_BREAKER_COOLDOWN`  | `10`                           | Circuit breaker cooldown in seconds |

Example:

```bash
export EVENTS_ENDPOINT_URL=http://reflexd:8770/events
export EVENTS_TIMEOUT_SECONDS=2.0
export EVENTS_BREAKER_THRESHOLD=10
hermes plugins enable body_expression_bridge
```

Ensure the events endpoint is running and accepts HTTP POST at the configured URL.

## Payload Format

All events are sent as direct HTTP POST with JSON body:

```json
{
  "event_type": "user_message|user_response|thinking_start|thinking_end|platform_event|stream_start|stream_delta|stream_end|session_end|session_finalize|api_error",
  "payload": {
    "text": "message content (empty for thinking/stream/session events)",
    "platform": "telegram|discord|slack|...|stream",
    "chat_id": "unique chat identifier (or session_id for stream events)",
    "user_id": "unique user identifier (empty for stream events)",
    "turn_id": "turn identifier (empty for stream events)",
    "seq": 1,
    "ts": 1700000000.123,
    "v": 1,
    "data": {
      "event_type": "platform event type (for platform_event)",
      "payload": "full platform event payload (for platform_event)"
    }
  },
  "meta": {
    "seq": 2,
    "ts": 1700000000.123,
    "v": 1
  }
}
```

**Field descriptions:**
- `text`: Event text content (empty for thinking/stream/session events)
- `platform`: Source platform (telegram, discord, slack, etc. or "stream")
- `chat_id`: Unique chat identifier (session_id for stream events)
- `user_id`: Unique user identifier (empty for stream events)
- `turn_id`: Turn identifier (empty for stream events)
- `seq`: Monotonically increasing sequence number per plugin instance (separate counters for payload and meta)
- `ts`: Unix timestamp (seconds since epoch, float)
- `v`: Payload schema version (currently 1)
- `data`: Optional nested data (used by `platform_event` for `event_type` and `payload`)

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

# Run hook tests
pytest tests/test_hooks.py -v

# Run hook integration tests
pytest tests/test_hook_integration.py -v

# Run publisher tests
pytest tests/test_publisher.py -v

# Coverage report
pytest tests/ --cov=body_expression_bridge --cov-report=term-missing
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
├── test_unit.py               # Core unit tests (platform filtering, payload building, extraction, middleware, dispatcher lifecycle)
├── test_integration.py        # Integration tests (protocol, worker, e2e hook flows)
├── test_hooks.py              # Hook-specific unit tests (all 11 hooks)
├── test_hook_integration.py   # Hook integration tests (end-to-end protocol verification)
└── test_publisher.py          # Publisher architecture tests (EventDispatcher, _build_payload, circuit breaker, queue limits)
```

Current test count: **178 tests** across 5 test files.

## Requirements

- Python ≥ 3.11
- `httpx` (for async HTTP calls)

## License

MIT
