# Plan: Pub/Sub Architecture Refactor

## Goal
Transform from tight coupling (body_expression_bridge → reflexd via direct MCP calls) to pub/sub architecture:
- body_expression_bridge: Publisher only (HTTP POST /events)
- reflexd: Subscriber (HTTP POST /events endpoint) + MCP session management
- Future: other services can subscribe to same events

## Current State (saved in commit 19fed1f)
- body_expression_bridge: direct MCP calls to reflexd via inject_event
- thinking_reflex: direct MCP calls + LED Stream Server
- Pose Stream Server: HTTP 8775 / WS 8774
- LED Stream Server: HTTP 8773 / WS 8772
- reflexd MCP server on 8770
- All 135 tests passing

## Target Architecture

```
┌─────────────────┐         HTTP POST /events         ┌──────────┐
│ body_expression │ ─────────────────────────────────► │  reflexd │
│   _bridge       │   event: {type, payload, meta}    │  (sub)   │
│  (publisher)    │                                   │          │
└─────────────────┘                                   │  - MCP   │
                                                      │  session │
                                                      │  gateway │
                                                      └──────────┘
                                                      
Future subscribers: logging, metrics, analytics, other services...
```

## Migration Plan

### Phase 1: body_expression_bridge → Publisher
**Files to modify:** `.hermes/plugins/body_expression_bridge/__init__.py`

**Changes:**
1. Remove all reflexd/MCP/HTTP client code
2. Remove EventDispatcher, _post_to_reflexd, _build_payload, _get_client, _initialize_session
2. Add config: `EVENTS_ENDPOINT_URL` (env var, default `http://localhost:8770/events`)
3. Add simple HTTP POST to `/events` endpoint
4. Keep: hooks registration, event scheduling, circuit breaker, LED stream calls (separate)
5. Event format: `{"event_type": "...", "payload": {...}, "meta": {...}}`

**Environment variables:**
- `EVENTS_ENDPOINT_URL` (default: `http://localhost:8770/events`)
- `EVENTS_TIMEOUT_SECONDS` (default: 1.0)
- `EVENTS_MAX_PENDING` (default: 256)
- `EVENTS_SKIP_PLATFORMS` (default: "local,desktop,cli")
- `EVENTS_BREAKER_THRESHOLD` (default: 5)
- `EVENTS_BREAKER_COOLDOWN` (default: 10)

**Event format:**
```json
{
  "event_type": "user_message",
  "payload": {"text": "hello", "platform": "telegram", "chat_id": "123", "user_id": "456"},
  "meta": {"session_id": "abc", "turn_id": "1", "seq": 1, "ts": 1234567890.123, "v": 1}
}
```

### Phase 2: reflexd - Add /events Endpoint
**Files to modify:** `services/reflexd/reflexd/server.py` (or new `server.py`)

**Changes:**
1. Add HTTP endpoint `POST /events` on port 8770 (same as MCP)
2. Parse event, route to internal event queue
3. Keep existing MCP session management, gateway calls, reflex logic
4. Event types to handle: `user_message`, `user_response`, `thinking_start`, `thinking_end`, `platform_event`, `stream_*`, `session_end`, `session_finalize`, `api_error`

### Phase 3: Config & Deployment
1. Update `body_expression_bridge` plugin.yaml with new env vars
2. Update `reflexd` systemd/service config if needed
3. Update `.env.example` files
4. Update README/docs

### Phase 4: Testing & Verification
1. Unit tests for publisher
2. Integration test: publisher → reflexd
3. Full cycle test via curl
4. Run existing 135 tests

## File Changes Summary

### body_expression_bridge (`~/.hermes/plugins/body_expression_bridge/`)
- `__init__.py` - major rewrite (remove reflexd/MCP code)
- `plugin.yaml` - update env vars
- `pyproject.toml` - remove httpx if not needed elsewhere
- Tests - rewrite for publisher

### reflexd (`services/reflexd/reflexd/`)
- `server.py` - add `/events` endpoint
- `main.py` - ensure HTTP server starts
- `gateway_client.py` - keep for MCP session management
- `reflexes/thinking.py` - keep (uses direct MCP calls internally)

### Config
- `.env.example` - add EVENTS_ENDPOINT_URL
- `plugin.yaml` - update env vars list
- `README.md` - update architecture docs

## Rollback Plan
If anything breaks: `git checkout 19fed1f` - returns to working state.

## Success Criteria
1. All 135 tests pass
2. Full cycle works: Telegram → Hermes → body_expression_bridge → reflexd → thinking_reflex → LED/head
3. Auto-idle works (60s → off)
4. New subscriber can be added without touching body_expression_bridge
5. All 135 existing tests pass