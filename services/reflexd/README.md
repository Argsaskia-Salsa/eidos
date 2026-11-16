# Eidos Reflexd Service

Minimal MCP server (port 8770) for executing StackChan reflexes.

## Architecture

```
body_expression_bridge (Hermes)
    │ MCP inject_event
    ▼
reflexd MCP :8770
    │ inject_event → @on_event handler
    │ call(tool, args)
    ▼
gateway MCP :8767 (stackchan-mcp)
    │
    ▼
ESP32 StackChan K151
```

## Installation

```bash
cd ~/projects/eidos/services/reflexd
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env  # edit token
# systemd unit → ~/.config/systemd/user/eidos-reflexd.service
systemctl --user daemon-reload
systemctl --user enable --now eidos-reflexd.service
```

## Configuration (.env)

| Variable           | Description              | Default                     |
|--------------------|--------------------------|-----------------------------|
| `GATEWAY_MCP_URL`  | Gateway MCP URL          | `http://127.0.0.1:8767/mcp` |
| `STACKCHAN_TOKEN`  | Bearer token for gateway | (required)                  |
| `REFLEXD_MCP_PORT` | Reflexd MCP port         | `8770`                      |
| `REFLEXES_DIR`     | Directory with reflexes  | `./reflexes`                |

## MCP Tools reflexd (:8770)

| Tool                 | Args                | Description                                     |
|----------------------|---------------------|-------------------------------------------------|
| `inject_event`       | `{name, data?}`     | Main entry point from bridge                    |
| `call`               | `{tool, arguments}` | Direct gateway tool call                        |
| `list_reflexes`      | `{}`                | Registered @on_event handlers                   |
| `list_gateway_tools` | `{}`                | Static GATEWAY_TOOLS                            |
| `reload_reflexes`    | `{}`                | Re-import reflexes/*.py                         |
| `user_message`       | `{data?}`           | Convenience: inject_event(name="user_message")  |
| `user_response`      | `{data?}`           | Convenience: inject_event(name="user_response") |
| `thinking_start`     | `{data?}`           | Convenience                                     |
| `thinking_end`       | `{data?}`           | Convenience                                     |

## Reflex API

```python
from reflexd import on_event, call
import asyncio
import random


@on_event("user_message")
async def on_user_message(event):
    await call("set_avatar", face="happy")
    await call("move_head", yaw=10, pitch=60)


@on_event("thinking_start")
async def on_thinking_start(event):
    await call("set_avatar", face="thinking")


# Long-running reflex: runs until cancelled by next event of same type
@on_event("idle_tick")
async def on_idle(event):
    try:
        while True:
            await call("move_head", yaw=random.randint(-30, 30), pitch=60)
            await asyncio.sleep(random.uniform(2, 5))
    except asyncio.CancelledError:
        # Cleanup: return to neutral
        await call("move_head", yaw=0, pitch=60)
        raise
```

## Verification

```bash
# List reflexes
curl -X POST http://localhost:8770/mcp -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_reflexes","arguments":{}}}'

# List gateway tools
curl -X POST http://localhost:8770/mcp -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_gateway_tools","arguments":{}}}'

# Inject event
curl -X POST http://localhost:8770/mcp -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"inject_event","arguments":{"name":"user_message","data":{}}}}'

# Reload reflexes
curl -X POST http://localhost:8770/mcp -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"reload_reflexes","arguments":{}}}'
```

## Systemd

```bash
# Install unit
cp systemd/reflexd.service ~/.config/systemd/user/eidos-reflexd.service
# Edit paths in the unit file if needed
systemctl --user daemon-reload
systemctl --user enable --now eidos-reflexd.service
```

## Configure body_expression_bridge

In Hermes env:
```
REFLEXD_MCP_URL=http://localhost:8770/mcp
```
