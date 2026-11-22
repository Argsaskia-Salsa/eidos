# Eidos — Expressive Robotics Platform

Eidos is a modular platform for expressive robotic behaviors, combining natural language dialogue with real-time physical expression (animations, gestures, LED patterns, head movements).

## Architecture Overview

```
┌─────────────────┐     ┌──────────────────────┐     ┌──────────────────┐
│   Telegram      │────▶│   Hermes Gateway     │────▶│  body_expression │
│   Discord       │     │   (Admission, LLM)   │     │  _bridge Plugin  │
│   Slack...      │     │                      │     │  (Publisher)     │
└─────────────────┘     └──────────────────────┘     └─────────┬────────┘
                                                               │ HTTP POST /events
                                                               ▼
┌─────────────────┐     ┌──────────────────────┐     ┌──────────────────┐
│   LED Stream    │◀────│                      │     │                  │
│   Pose Stream   │     │    reflexd           │◀────│  /events         │
│   (WebSocket)   │     │  (Event Processor)   │     │  /events/stream  │
└─────────────────┘     └──────────────────────┘     │  (WebSocket)     │
                                                     └────────┬─────────┘
                                                              │
                                                              ▼
                                                     ┌──────────────────┐
                                                     │  Debug UI        │
                                                     │  (Browser)       │
                                                     └──────────────────┘
```

### Components

| Component                  | Role                                         | Port |
|----------------------------|----------------------------------------------|------|
| **Hermes Gateway**         | Central message broker, LLM orchestration    | 8767 |
| **body_expression_bridge** | Hermes plugin, publishes dialogue events     | —    |
| **reflexd**                | Event processor, reflex executor, MCP server | 8770 |
| **LED Stream**             | Real-time LED pattern streaming              | 8771 |
| **Pose Stream**            | Real-time head/body pose streaming           | 8772 |

## Event Flow

1. **User sends message** → Hermes Gateway receives it
2. **Post-admission hook** → `body_expression_bridge` fires `user_message` event
3. **LLM execution middleware** → Fires `thinking_start` / `thinking_end`
4. **Post-LLM hook** → Fires `user_response` event
5. **Events POSTed to reflexd** → `/events` endpoint queues them
6. **Event processor** → Routes to reflex handlers (`thinking_start`, `thinking_end`, etc.)
7. **Reflexes execute** → Control LEDs, head pose, animations via MCP tools
8. **Real-time streaming** → Events broadcast via WebSocket `/events/stream`

## Event Types

| Event              | Source                     | Description                      |
|--------------------|----------------------------|----------------------------------|
| `user_message`     | `post_gateway_admission`   | Incoming user message after auth |
| `user_response`    | `post_llm_call`            | Assistant response after LLM     |
| `thinking_start`   | `llm_execution` middleware | LLM generation begins            |
| `thinking_end`     | `llm_execution` middleware | LLM generation ends              |
| `platform_event`   | `gateway_platform_event`   | Telegram reactions, edits        |
| `stream_start`     | `on_stream_start`          | Streaming generation begins      |
| `stream_delta`     | `on_stream_delta`          | Streaming text delta (throttled) |
| `stream_end`       | `on_stream_end`            | Streaming generation ends        |
| `session_end`      | `on_session_end`           | Per-turn session close           |
| `session_finalize` | `on_session_finalize`      | Session boundary cleanup         |
| `api_error`        | `api_request_error`        | LLM API failures                 |

## Configuration

### Root `.env`
```bash
EVENTS_ENDPOINT_URL=http://localhost:8770/events
```

### `body_expression_bridge` `.env`
```bash
EVENTS_ENDPOINT_URL=http://localhost:8770/events
EVENTS_TIMEOUT_SECONDS=1.0
EVENTS_MAX_PENDING=256
EVENTS_SKIP_PLATFORMS=local,desktop,cli
EVENTS_BREAKER_THRESHOLD=5
EVENTS_BREAKER_COOLDOWN=10
```

### `reflexd` `.env`
```bash
GATEWAY_MCP_URL=http://127.0.0.1:8767/mcp
STACKCHAN_TOKEN=your-token
REFLEXD_MCP_PORT=8770
EVENTS_WS_MAX_CLIENTS=10
REFLEXES_DIR=/path/to/reflexes
```

## Debug UI — Real-Time Event Monitor

A standalone HTML page for visualizing events in real-time via WebSocket.

### Access
- **Via reflexd**: `http://localhost:8770/tests/debug/event_monitor.html`
- **Direct file**: Open `services/reflexd/tests/debug/event_monitor.html` in browser (add `?host=localhost:8770`)

### Features
- Real-time event table with timestamps
- Color-coded event types (blue=user_message, orange=thinking_start, green=thinking_end, etc.)
- Collapsible JSON payload/meta inspection
- Pause/Resume, Clear, Filter by event type
- Connection status with auto-reconnect (exponential backoff)
- Event counters per type in sidebar
- Persists filter/pause state in localStorage
- Responsive design (works on mobile)

### WebSocket Protocol
```
Connect: GET ws://localhost:8770/events/stream

Server → Client (welcome):
{"type": "connected", "event_types": ["user_message", "thinking_start", ...]}

Server → Client (per event):
{"type": "event", "event_type": "user_message", "payload": {...}, "meta": {...}}
```

## Running Locally

```bash
# Terminal 1: Start reflexd
cd services/reflexd
pip install -e ".[dev]"
cp .env.example .env
# Edit .env with your STACKCHAN_TOKEN
python -m reflexd

# Terminal 2: Test event posting
curl -X POST http://localhost:8770/events \
  -H "Content-Type: application/json" \
  -d '{"event_type":"user_message","payload":{"text":"Hello","platform":"telegram","chat_id":"1","user_id":"1"},"meta":{}}'

# Terminal 3: Open Debug UI
# Browser: http://localhost:8770/tests/debug/event_monitor.html
```

## Testing

```bash
# body_expression_bridge publisher tests
cd .hermes/plugins/body_expression_bridge
pip install -e ".[dev]"
python -m pytest tests/test_publisher.py -v

# reflexd integration tests
cd services/reflexd
pip install -e ".[dev]"
python -m pytest tests/ -v

# All tests
python -m pytest tests/ -v
```

## Reflex Development

Reflexes are Python modules in `services/reflexd/reflexes/` decorated with `@on_event`:

```python
from reflexd import on_event

@on_event("thinking_start", priority=10, conflict_group="expression")
async def thinking_animation(data):
    await set_avatar("thinking")
    await start_led_pattern("pulse", color="blue")
```

Metadata options:
- `priority`: Higher runs first, preempts lower
- `conflict_group`: Only one runs at a time per group
- `cleanup`: Async function called on preemption/completion

## License

MIT
