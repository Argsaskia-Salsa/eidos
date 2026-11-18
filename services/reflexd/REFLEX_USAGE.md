# Reflex System Usage Guide

## Overview

The reflex system provides event-driven behavior for the robot with support for:
- **Event handlers** (`@on_event`) - react to events
- **Cleanup handlers** (`@on_cleanup`) - run when interrupted
- **Event chaining** (`trigger_event`) - reflexes triggering other reflexes
- **Priority preemption** - higher priority cancels lower priority
- **Conflict groups** - same group = mutual exclusion
- **MCP tools** - manual control via `cancel_reflex` and `list_running_reflexes`

---

## Basic Reflex Definition

```python
from reflexd import call, on_event, on_cleanup

@on_event("my_event", priority=5, conflict_group="group_name")
async def on_my_event(data: dict):
    # data contains event payload
    await call("set_avatar", face="happy")
    await call("move_head", yaw=10, pitch=12)

@on_cleanup("my_event")
async def cleanup_my_event():
    # Runs when this reflex is cancelled/interrupted
    await call("set_avatar", face="neutral")
    await call("move_head", yaw=0, pitch=12)
```

### Decorator Parameters

| Parameter        | Type             | Default | Description                   |
|------------------|------------------|---------|-------------------------------|
| `priority`       | int              | 0       | Higher = wins preemption      |
| `conflict_group` | str \| None      | None    | Same group = mutual exclusion |
| `cleanup`        | Callable \| None | None    | Alternative to `@on_cleanup`  |

---

## Event Chaining with `trigger_event`

```python
from reflexd import call, on_event, on_cleanup, trigger_event

@on_event("thinking_start", priority=10, conflict_group="head_movement")
async def on_thinking_start(_event):
    await call("set_avatar", face="thinking")
    await call("set_all_leds", r=255, g=0, b=0)
    
    # Trigger another reflex explicitly
    await trigger_event("led_rainbow_start", {})

@on_event("led_rainbow_start", priority=5)
async def on_led_rainbow_start(_event):
    # This runs asynchronously, non-blocking
    asyncio.create_task(_rainbow_loop())

@on_cleanup("thinking_start")
async def cleanup_thinking():
    await call("clear_leds")
    await call("set_avatar", face="neutral")
    # Trigger cleanup in chained reflex
    await trigger_event("led_rainbow_stop", {})
```

**Key points:**
- `trigger_event` is **fire-and-forget** - returns immediately
- Chained reflex runs independently with its own priority/conflict_group
- Use `trigger_event` in cleanup to cascade cancellation

---

## Priority Preemption

```python
# Low priority idle behavior
@on_event("idle_tick", priority=1, conflict_group="head_movement")
async def on_idle(_event):
    while True:
        await call("move_head", yaw=random.randint(-30, 30), pitch=12)
        await asyncio.sleep(random.uniform(2, 5))

# High priority thinking - preempts idle
@on_event("thinking_start", priority=10, conflict_group="head_movement")
async def on_thinking_start(_event):
    await call("set_avatar", face="thinking")
    # idle_tick automatically cancelled due to:
    # 1. Higher priority (10 > 1)
    # 2. Same conflict_group ("head_movement")
```

**Preemption rules:**
1. Same `conflict_group` → new event **always** cancels running one
2. Different groups, higher `priority` → new event cancels lower priority
3. Same priority, different groups → both run concurrently

---

## Conflict Groups

```python
# Head movement group - only one at a time
@on_event("look_left", priority=5, conflict_group="head_movement")
async def on_look_left(_event):
    await call("move_head", yaw=-45, pitch=12)

@on_event("look_right", priority=5, conflict_group="head_movement")
async def on_look_right(_event):
    await call("move_head", yaw=45, pitch=12)

# LED group - independent from head
@on_event("led_red", priority=5, conflict_group="leds")
async def on_led_red(_event):
    await call("set_all_leds", r=255, g=0, b=0)

# These can run simultaneously (different conflict_groups)
await trigger_event("look_left", {})   # Uses head_movement
await trigger_event("led_red", {})     # Uses leds - runs in parallel!
```

---

## Manual Control via MCP Tools

### Cancel a Specific Reflex

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "cancel_reflex",
    "arguments": { "name": "thinking_start" }
  },
  "id": 1
}
```

Response:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": { "status": "ok", "cancelled": true }
}
```

### List Running Reflexes

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "list_running_reflexes",
    "arguments": {}
  },
  "id": 1
}
```

Response:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": { "running": ["thinking_start", "idle_tick"] }
}
```

---

## Complete Example: Thinking → Idle Chain

```python
from reflexd import call, on_event, on_cleanup, trigger_event
import asyncio
import random

DEFAULT_PITCH = 12

# 1. Idle behavior (low priority, yields to everything)
@on_event("idle_tick", priority=1, conflict_group="head_movement")
async def on_idle(_event):
    try:
        while True:
            yaw = random.randint(-30, 30)
            await call("move_head", yaw=yaw, pitch=DEFAULT_PITCH)
            await asyncio.sleep(random.uniform(2, 5))
    except asyncio.CancelledError:
        await call("move_head", yaw=0, pitch=DEFAULT_PITCH)
        raise

@on_cleanup("idle_tick")
async def cleanup_idle():
    await call("move_head", yaw=0, pitch=DEFAULT_PITCH)

# 2. User interaction (medium priority)
@on_event("user_message", priority=5, conflict_group="head_movement")
async def on_user_message(_event):
    await call("set_avatar", face="happy")
    await call("move_head", yaw=10, pitch=DEFAULT_PITCH)
    # Trigger greeting LED
    await trigger_event("greeting_led", {})

@on_cleanup("user_message")
async def cleanup_user_message():
    await call("set_avatar", face="neutral")
    await call("move_head", yaw=0, pitch=DEFAULT_PITCH)

@on_event("greeting_led", priority=3, conflict_group="leds")
async def on_greeting_led(_event):
    for _ in range(3):
        await call("set_all_leds", r=0, g=255, b=0)
        await asyncio.sleep(0.2)
        await call("set_all_leds", r=0, g=0, b=0)
        await asyncio.sleep(0.2)

# 3. Thinking (high priority, preempts user_message and idle)
@on_event("thinking_start", priority=10, conflict_group="head_movement")
async def on_thinking_start(_event):
    await call("set_avatar", face="thinking")
    await call("set_all_leds", r=255, g=0, b=0)
    await trigger_event("led_rainbow", {})

@on_cleanup("thinking_start")
async def cleanup_thinking():
    await call("clear_leds")
    await call("set_avatar", face="neutral")
    await call("move_head", yaw=0, pitch=DEFAULT_PITCH)
    await trigger_event("led_rainbow_stop", {})

@on_event("led_rainbow", priority=5, conflict_group="leds")
async def on_led_rainbow(_event):
    colors = [(255,0,0), (255,127,0), (255,255,0), (0,255,0), (0,255,255), (0,0,255)]
    idx = 0
    try:
        while True:
            c = colors[idx % len(colors)]
            await call("set_all_leds", r=c[0], g=c[1], b=c[2])
            idx += 1
            await asyncio.sleep(0.2)
    except asyncio.CancelledError:
        raise

@on_cleanup("led_rainbow")
async def cleanup_led_rainbow():
    await call("clear_leds")
```

**Behavior:**
1. `idle_tick` runs continuously (head moves randomly)
2. User sends message → `user_message` preempts `idle_tick` (priority 5 > 1)
3. `user_message` triggers `greeting_led` (runs in parallel, different group)
4. `thinking_start` arrives → preempts BOTH `user_message` and `idle_tick` (priority 10 > 5, same group)
5. `thinking_start` triggers `led_rainbow` 
6. On `thinking_end` or manual cancel → cleanup runs, cascades to chained reflexes

---

## MCP Inject Event Examples

```bash
# Start thinking
curl -X POST http://localhost:8770/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"inject_event","arguments":{"name":"thinking_start","data":{}}},"id":1}'

# Send user message
curl -X POST http://localhost:8770/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"user_message","arguments":{"data":{"text":"Hello"}}},"id":1}'

# Cancel thinking manually
curl -X POST http://localhost:8770/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"cancel_reflex","arguments":{"name":"thinking_start"}},"id":1}'

# Check what's running
curl -X POST http://localhost:8770/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"list_running_reflexes","arguments":{}},"id":1}'
```

---

## Best Practices

1. **Always add cleanup handlers** - ensures robot returns to neutral state
2. **Use conflict_groups for hardware resources** - head, leds, avatar are separate
3. **Set appropriate priorities** - idle=1, user=5, thinking=10, emergency=100
4. **Use `trigger_event` for composition** - keeps reflexes small and reusable
5. **Handle `asyncio.CancelledError`** - re-raise after cleanup in long-running loops
6. **Keep handlers fast** - offload long work to background tasks

---

## Event Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    MCP inject_event                         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              handle_inject_event(name, data)                │
│  1. Cancel same-name running reflex                         │
│  2. Preempt lower priority / same conflict_group            │
│  3. Run cleanup handlers for preempted reflexes             │
│  4. Start new handler as asyncio.Task                       │
│  5. Track in _running: {task, priority, conflict_group}     │
└──────────────────────────┬──────────────────────────────────┘
                           │
           ┌───────────────┼──────────────────┐
           ▼               ▼                  ▼
    ┌─────────────┐  ┌─────────────┐  ┌──────────────┐
    │  Handler    │  │  Handler    │  │  Handler     │
    │ (priority=1)│  │ (priority=5)│  │ (priority=10)│
    │ conflict=   │  │ conflict=   │  │ conflict=    │
    │ head_move   │  │ head_move   │  │ head_move    │
    └─────┬───────┘  └─────┬───────┘  └───────┬──────┘
          │                │                  │
          │     HIGHER     │                  │
          │   PRIORITY     │                  │
          │    WINS        │                  │
          └────────────────┴──────────────────┘
                           │
                           ▼
                  ┌────────────────┐
                  │  trigger_event │──► New event (fire-and-forget)
                  │  (in handler)  │     independent priority/group
                  └────────────────┘
```
