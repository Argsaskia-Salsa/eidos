# Eidos Reflexd Service

Minimal MCP server (port 8770) for executing StackChan reflexes. Runs `reflexd` in a virtual environment with configuration from `.env`.

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

---

## Prerequisites

Navigate to the service directory (`~/projects/eidos/services/reflexd`):

1. **Create virtual environment and install package in editable mode:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -e .[dev]
   ```
   *(If using the modern `uv` manager: `uv venv .venv && uv pip install -e .[dev]`)*

2. **Configure environment variables:**
   ```bash
   cp .env.example .env
   nano .env
   ```

> **Important:** `.env` must be compatible with systemd's `EnvironmentFile`. Use simple `KEY=value` lines.
> Do not use `export`, shell substitutions, or complex constructs — systemd does not execute `.env` as a shell script.
>
> Example:
> ```env
> STACKCHAN_TOKEN=abc123
> GATEWAY_MCP_URL=http://127.0.0.1:8767/mcp
> REFLEXD_MCP_PORT=8770
> REFLEXES_DIR=/home/erika/projects/eidos/services/reflexd/reflexes
> ```

**Where to put `.env`:** In the service directory itself — `~/projects/eidos/services/reflexd/.env`. The systemd unit references it via `EnvironmentFile=$SERVICE_DIR/.env`.

---

## Install and Run as systemd Service

The service is configured as a user unit (`systemctl --user`) using the directory `~/projects/eidos/services/reflexd`.

### 1. Create Service File

Run the following command to generate the unit. The heredoc intentionally uses unquoted `EOF` so that the `SERVICE_DIR` variable expands to an absolute path:

```bash
SERVICE_DIR="$HOME/projects/eidos/services/reflexd"
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/eidos-reflexd.service <<EOF
[Unit]
Description=Eidos Reflexd Service
After=network.target eidos-gateway.service
Wants=eidos-gateway.service

[Service]
Type=simple
WorkingDirectory=$SERVICE_DIR
EnvironmentFile=$SERVICE_DIR/.env
ExecStart=$SERVICE_DIR/.venv/bin/python -m reflexd.main
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
```

> **Note:** `After=network.target eidos-gateway.service` + `Wants=eidos-gateway.service` ensures the gateway is started first. Reflexd connects to gateway MCP at `http://127.0.0.1:8767/mcp` (IPv4 forced).

Verify that `SERVICE_DIR` points to the actual service directory.

### 2. Enable and Start

```bash
# Reload systemd configuration
systemctl --user daemon-reload

# Enable autostart and start service now
systemctl --user enable --now eidos-reflexd.service
```

*(Optional)* To keep the service running after closing SSH session or user logout:

```bash
sudo loginctl enable-linger "$USER"
```

---

## Update Service and Dependencies

After pulling fresh changes from the repository (`git pull`) in `~/projects/eidos/services/reflexd`:

1. **Update dependencies (if `pyproject.toml` changed):**
   ```bash
   .venv/bin/pip install -e .[dev]
   ```
   *(If using `uv`: `uv pip install -e .[dev]`)*

2. **Restart the service:**
   ```bash
   systemctl --user restart eidos-reflexd.service
   ```

If only code changed and the package was installed in editable mode (`pip install -e .`), a service restart is sufficient.

If the unit file changed:

```bash
systemctl --user daemon-reload
systemctl --user restart eidos-reflexd.service
```

If `.env` changed:

```bash
systemctl --user restart eidos-reflexd.service
```

---

## Management and Logs

* **Check status:**
  ```bash
  systemctl --user status eidos-reflexd.service
  ```

* **Follow logs in real time:**
  ```bash
  journalctl --user -u eidos-reflexd.service -f
  ```

* **Stop and disable autostart:**
  ```bash
  systemctl --user disable --now eidos-reflexd.service
  ```

---

## LED Stream Service (reflexd.streams.led)

LED streaming daemon: WebSocket server (port 8772) for gateway + HTTP control API (port 8773).
Uses `stackchan_follow_led_stream` MCP tool to stream LED frames to the robot.

### Prerequisites

Same virtual environment as reflexd (shared `.venv` in `~/projects/eidos/services/reflexd`).

**Required environment variables** (in `.env`):
```env
STACKCHAN_TOKEN=your-token          # Required for MCP stream_start/stop/status
GATEWAY_MCP_URL=http://127.0.0.1:8767/mcp
HOST_IP=<YOUR_HOST_IP>              # Host IP for gateway WebSocket connection
```

Optional (with defaults):
```env
WS_HOST=0.0.0.0
WS_PORT=8772
HTTP_PORT=8773
NUM_LEDS=12
DEFAULT_FPS=20
DEFAULT_BRIGHTNESS=1.0
COLOR_ORDER=grb
TARGET=base_ring
MAX_FPS=20
```

### Install and Run as systemd Service

Create the service file:

```bash
SERVICE_DIR="$HOME/projects/eidos/services/reflexd"
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/eidos-led-stream.service <<EOF
[Unit]
Description=Eidos LED Stream Server
After=network.target eidos-gateway.service
Wants=eidos-gateway.service

[Service]
Type=simple
WorkingDirectory=$SERVICE_DIR
EnvironmentFile=$SERVICE_DIR/.env
ExecStart=$SERVICE_DIR/.venv/bin/python -m reflexd.streams.led
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
```

> **Note:** `After=network.target eidos-gateway.service` + `Wants=eidos-gateway.service` ensures the gateway is started first. The LED server connects to gateway via `stackchan_follow_led_stream` MCP tool.

### Enable and Start

```bash
# Reload systemd configuration
systemctl --user daemon-reload

# Enable autostart and start service now
systemctl --user enable --now eidos-led-stream.service
```

*(Optional)* To keep the service running after closing SSH session or user logout:
```bash
sudo loginctl enable-linger "$USER"
```

---

## Update Service and Dependencies

After pulling fresh changes from the repository (`git pull`) in `~/projects/eidos/services/reflexd`:

1. **Update dependencies (if `pyproject.toml` changed):**
   ```bash
   .venv/bin/pip install -e .[dev]
   ```

2. **Restart the service:**
   ```bash
   systemctl --user restart eidos-led-stream.service
   ```

If the unit file changed:
```bash
systemctl --user daemon-reload
systemctl --user restart eidos-led-stream.service
```

If `.env` changed:
```bash
systemctl --user restart eidos-led-stream.service
```

---

## Management and Logs

* **Check status:**
  ```bash
  systemctl --user status eidos-led-stream.service
  ```

* **Follow logs in real time:**
  ```bash
  journalctl --user -u eidos-led-stream.service -f
  ```

* **Stop and disable autostart:**
  ```bash
  systemctl --user disable --now eidos-led-stream.service
  ```

---

## LED Stream Package (reflexd.streams.led)

Unified package for LED streaming: WebSocket (gateway) + HTTP (control) + MCP integration.

### Quick Start

```bash
# Start server (WS:8772, HTTP:8773)
python -m reflexd.streams.led

# CLI control
python -m reflexd.streams.led pattern rainbow --color 255 0 0
python -m reflexd.streams.led fps 30
python -m reflexd.streams.led brightness 0.5
python -m reflexd.streams.led start   # Connect to gateway via MCP
python -m reflexd.streams.led status
```

### Architecture

```
┌─────────────┐     WebSocket      ┌──────────────────┐
│   Gateway   │ ◄───────────────── │ LEDStreamServer  │
│ (MCP tool)  │   frames @ FPS     │  (reflexd:8772)  │
└──────┬──────┘                    └────────┬─────────┘
       │                                    │
       │ HTTP control                       │ HTTP control
       ▼                                    ▼
┌──────────────────┐              ┌──────────────────┐
│ LEDStreamClient  │              │   HTTP API       │
│ (MCP + HTTP)     │              │  /pattern, /fps  │
└──────────────────┘              │  /brightness     │
                                  │  /patterns       │
                                  │  /patterns/reload│
                                  └──────────────────┘
```

### Built-in Patterns

| Pattern     | Description          | Args                            |
|-------------|----------------------|---------------------------------|
| `cycle`     | Sequential 1→2→...→N | —                               |
| `rainbow`   | Flowing rainbow wave | —                               |
| `breathing` | All LEDs breathe     | `color=[0,180,255]`             |
| `kitt`      | KITT scanner         | `color=[255,0,0]`, `tail=0.5`   |
| `solid`     | Single solid color   | `color=[255,255,255]`           |
| `off`       | All LEDs off         | —                               |
| `pulse`     | Pulse in/out         | `color=[255,100,0]`, `freq=1.0` |

### Custom Patterns (Hot-Reloadable)

Create `*.py` in `services/reflexd/reflexd/streams/led/patterns/custom/`:

```python
from reflexd.streams.led.registry import LEDPatternSpec

def my_pattern(num_leds=12, color=[255,0,0], **kwargs):
    # Return list[Frame] where Frame = list[list[int]] (LED x RGB)
    frames = []
    for i in range(num_leds):
        colors = [[0,0,0] for _ in range(num_leds)]
        colors[i] = color
        frames.append(colors)
    return frames

PATTERN = LEDPatternSpec(
    name="my_pattern",
    func=my_pattern,
    args={"color": [255,0,0]},
    description="My custom pattern"
)
```

Reload without restart:
```bash
curl -X POST http://localhost:8773/patterns/reload
# or
python -m reflexd.streams.led reload
```

### Configuration (Environment Variables)

| Variable             | Default                     | Description                    |
|----------------------|-----------------------------|--------------------------------|
| `WS_HOST`            | `0.0.0.0`                   | WebSocket bind address         |
| `WS_PORT`            | `8772`                      | WebSocket port                 |
| `HTTP_PORT`          | `8773`                      | HTTP control port              |
| `NUM_LEDS`           | `12`                        | Number of LEDs                 |
| `DEFAULT_FPS`        | `20`                        | Default frame rate             |
| `DEFAULT_BRIGHTNESS` | `1.0`                       | Default brightness (0.0-1.0)   |
| `COLOR_ORDER`        | `grb`                       | LED color order                |
| `GATEWAY_MCP_URL`    | `http://127.0.0.1:8767/mcp` | Gateway MCP endpoint           |
| `STACKCHAN_TOKEN`    | (required)                  | Bearer token for MCP           |
| `TARGET`             | `base_ring`                 | LED target on robot            |
| `MAX_FPS`            | `20`                        | Max FPS for gateway            |
| `HOST_IP`            | `<YOUR_HOST_IP>`               | Host IP for gateway connection |

### HTTP API

| Method | Endpoint           | Description                    |
|--------|--------------------|--------------------------------|
| GET    | `/status`          | Current state                  |
| POST   | `/pattern`         | Set pattern `{pattern, args?}` |
| POST   | `/fps`             | Set FPS `{fps}`                |
| POST   | `/brightness`      | Set brightness `{brightness}`  |
| GET    | `/patterns`        | List all patterns              |
| POST   | `/patterns/reload` | Hot-reload custom patterns     |

### MCP Integration

`LEDStreamClient` provides MCP methods for `stackchan_follow_led_stream`:

```python
async with LEDStreamClient() as client:
    await client.stream_start()   # Start WS stream via gateway
    await client.stream_stop()    # Stop stream
    status = await client.stream_status()
```

### Module Entrypoint

```bash
# Start server
python -m reflexd.streams.led

# CLI commands
python -m reflexd.streams.led pattern rainbow --color 255 0 0
python -m reflexd.streams.led fps 30
python -m reflexd.streams.led brightness 0.5
python -m reflexd.streams.led list
python -m reflexd.streams.led reload
python -m reflexd.streams.led start
python -m reflexd.streams.led stop
python -m reflexd.streams.led status
python -m reflexd.streams.led monitor -i 2.0
```

---

## MCP Endpoint (reflexd :8770)

The reflexd exposes a Streamable HTTP MCP endpoint at `http://127.0.0.1:8770/mcp` for Hermes bridge and direct calls.

### Tools

| Tool                 | Args                | Description                                       |
|----------------------|---------------------|---------------------------------------------------|
| `inject_event`       | `{name, data?}`     | Main entry point from bridge                      |
| `call`               | `{tool, arguments}` | Direct gateway tool call                          |
| `list_reflexes`      | `{}`                | Registered `@on_event` handlers                   |
| `list_gateway_tools` | `{}`                | Static `GATEWAY_TOOLS` schemas                    |
| `reload_reflexes`    | `{}`                | Re-import `reflexes/*.py`                         |
| `user_message`       | `{data?}`           | Convenience: `inject_event(name="user_message")`  |
| `user_response`      | `{data?}`           | Convenience: `inject_event(name="user_response")` |
| `thinking_start`     | `{data?}`           | Convenience                                       |
| `thinking_end`       | `{data?}`           | Convenience                                       |

### Quick Examples

```bash
# List registered reflexes
curl -X POST http://localhost:8770/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_reflexes","arguments":{}}}'

# List gateway tools available via call()
curl -X POST http://localhost:8770/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_gateway_tools","arguments":{}}}'

# Inject event (e.g., user spoke)
curl -X POST http://localhost:8770/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"inject_event","arguments":{"name":"user_message","data":{}}}}'

# Reload reflexes after adding/modifying files in reflexes/
curl -X POST http://localhost:8770/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"reload_reflexes","arguments":{}}}'
```

### Health Check

```bash
curl http://localhost:8770/health
# {"status":"ok","gateway_connected":true,"reflexes_loaded":5,"uptime_seconds":123.45}
```

---

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

### Built-in Reflexes (auto-loaded from `reflexes/`)

| Event            | Behavior                                              |
|------------------|-------------------------------------------------------|
| `thinking_start` | `set_avatar(face="thinking")`                         |
| `thinking_end`   | `set_avatar(face="neutral")`                          |
| `user_message`   | happy face + head yaw 10°                             |
| `user_response`  | neutral face + head yaw 0°                            |
| `idle_tick`      | random head sway (2–5s), returns to neutral on cancel |

Add custom reflexes by creating `*.py` files in `reflexes/` — they auto-load on startup or via `reload_reflexes`.

---

## Configure body_expression_bridge (Hermes)

In Hermes `.env`:

```env
REFLEXD_MCP_URL=http://localhost:8770/mcp
```

The bridge will call `inject_event({name, data})` on reflexd → reflexd calls gateway tools → ESP32.
