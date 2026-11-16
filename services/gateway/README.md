# Eidos Gateway Service (`stackchan_mcp`)

Production StackChan MCP gateway for M5Stack hardware. Runs `stackchan-mcp serve --transport streamable-http` in a virtual environment with configuration from `.env`.

## Prerequisites

Navigate to the service directory (`~/projects/eidos/services/gateway`):

1. **Create virtual environment and install package in editable mode:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -e .
   ```
   *(If using the modern `uv` manager: `uv venv .venv && uv pip install -e .`)*

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
> WS_PORT=8766
> ```

---

## Install and Run as systemd Service

The service is configured as a user unit (`systemctl --user`) using the directory `~/projects/eidos/services/gateway`.

### 1. Create Service File

Run the following command to generate the unit. The heredoc intentionally uses unquoted `EOF` so that the `SERVICE_DIR` variable expands to an absolute path:

```bash
SERVICE_DIR="$HOME/projects/eidos/services/gateway"
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/eidos-gateway.service <<EOF
[Unit]
Description=Eidos Gateway Service (stackchan_mcp)
After=network.target

[Service]
WorkingDirectory=$SERVICE_DIR
EnvironmentFile=$SERVICE_DIR/.env
ExecStart=$SERVICE_DIR/.venv/bin/python -m stackchan_mcp serve --transport streamable-http
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
```

> **Note:** The `--transport streamable-http` flag is required. Without it, `stackchan-mcp serve` defaults to `stdio` transport (for CLI clients like Claude Code), which exits immediately when stdin is closed — causing systemd to restart the service in a loop. The `streamable-http` transport runs the HTTP MCP server on port 8767 (`/mcp` endpoint) for reflexd and other services.

Verify that `SERVICE_DIR` points to the actual service directory.

### 2. Enable and Start

```bash
# Reload systemd configuration
systemctl --user daemon-reload

# Enable autostart and start service now
systemctl --user enable --now eidos-gateway.service
```

*(Optional)* To keep the service running after closing SSH session or user logout:

```bash
sudo loginctl enable-linger "$USER"
```

---

## Update Service and Dependencies

After pulling fresh changes from the repository (`git pull`) in `~/projects/eidos/services/gateway`:

1. **Update dependencies (if `pyproject.toml` changed):**
   ```bash
   .venv/bin/pip install -e .
   ```
   *(If using `uv`: `uv pip install -e .`)*

2. **Restart the service:**
   ```bash
   systemctl --user restart eidos-gateway.service
   ```

If only code changed and the package was installed in editable mode (`pip install -e .`), a service restart is sufficient.

If the unit file changed:

```bash
systemctl --user daemon-reload
systemctl --user restart eidos-gateway.service
```

If `.env` changed:

```bash
systemctl --user restart eidos-gateway.service
```

---

## Management and Logs

* **Check status:**
  ```bash
  systemctl --user status eidos-gateway.service
  ```

* **Follow logs in real time:**
  ```bash
  journalctl --user -u eidos-gateway.service -f
  ```

* **Stop and disable autostart:**
  ```bash
  systemctl --user disable --now eidos-gateway.service
  ```

---

## Control StackChan K151 (Cube) via MCP HTTP

The gateway exposes a Streamable HTTP MCP endpoint at `http://127.0.0.1:8767/mcp` for controlling the connected ESP32 device (StackChan K151).

### Quick Example

```bash
TOKEN="your-STACKCHAN_TOKEN-from-.env"

# 1. Initialize session → get Mcp-Session-Id
SESSION_ID=$(curl -s -i -X POST http://127.0.0.1:8767/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":1}' \
  | grep -i mcp-session-id | cut -d' ' -f2 | tr -d '\r')

echo "Session: $SESSION_ID"

# 2. Move head (yaw: -90..90, pitch: 5..85)
curl -s -X POST http://127.0.0.1:8767/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"move_head","arguments":{"yaw":30,"pitch":45}},"id":2}'

# 3. Set face expression
curl -s -X POST http://127.0.0.1:8767/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"set_avatar","arguments":{"face":"happy"}},"id":3}'

# 4. Set all LEDs (R, G, B 0-255)
curl -s -X POST http://127.0.0.1:8767/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"set_all_leds","arguments":{"r":255,"g":0,"b":0}},"id":4}'

# 5. Get current head angles
curl -s -X POST http://127.0.0.1:8767/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_head_angles","arguments":{}},"id":5}'
```

### Available Tools

| Tool | Description | Arguments |
|------|-------------|-----------|
| `move_head` | Move head to yaw/pitch | `yaw` (-90..90), `pitch` (5..85), `speed` (optional: `low`/`mid`/`high` or dps int) |
| `get_head_angles` | Get current angles | — |
| `set_avatar` | Set face expression | `face` (`idle`, `happy`, `sad`, `surprised`, `thinking`, `embarrassed`) |
| `set_mouth` | Set mouth shape | `mouth` (`default`, `smile`, `open`, `line`) |
| `set_blink` | Enable/disable blink | `enabled` (boolean) |
| `set_all_leds` | Set all 12 base ring LEDs | `r`, `g`, `b` (0-255) |
| `set_led` | Set single LED | `index` (0-11), `r`, `g`, `b` |
| `set_leds` | Set multiple LEDs | `colors` array of `{index, r, g, b}` |
| `clear_leds` | Clear all LEDs | — |
| `take_photo` | Capture photo from camera | `question` (string) |
| `get_status` | Gateway + device status | — |
| `get_device_info` | Battery, volume, brightness, etc. | — |

> **Note:** The `Mcp-Session-Id` header is required for all `tools/call` requests after `initialize`. The session is tied to the connected ESP32 device.
