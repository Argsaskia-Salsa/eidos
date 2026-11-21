"""
Base StreamServer using Template Method pattern.
Subclasses implement 6 abstract hooks (~50 lines) to get full WS + HTTP server.
"""

import asyncio
import json
import signal
import time
from abc import ABC, abstractmethod

import websockets
from aiohttp import web

# Handle websockets version differences (17.x moved exceptions to top-level)
try:
    from websockets.exceptions import ConnectionClosed
except ImportError:
    ConnectionClosed = websockets.ConnectionClosed

from .registry import StreamRegistry


class ServerConfig:
    """Configuration for StreamServer - subclasses override class attributes."""

    WS_HOST: str = "0.0.0.0"
    WS_PORT: int = 8772
    HTTP_PORT: int = 8773
    DEFAULT_FPS: float = 20.0
    DEFAULT_INTENSITY: float = 1.0


class StreamServer(ABC):
    """
    Template Method base class for stream servers.

    Concrete implementation in base:
    - WS lifecycle (connections, frame loop, timing, reconnect detection)
    - HTTP setup, routing, startup/shutdown, signals
    - State management (current_element, element_args, fps, intensity, frame_cache)
    - Caching with key = (element_name, json.dumps(args))
    - Standard endpoints (/status, POST /{element}, POST /fps, POST /{intensity}, GET /{element}s, POST /{element}s/reload)

    Abstract hooks (subclass implements ~50 lines):
    - element_name: str - "pattern" | "pose" | "mode"
    - intensity_name: str - "brightness" | "smoothing" | "sensitivity"
    - registry: StreamRegistry - injected registry
    - make_frame(element_data, intensity) -> dict - WS JSON serializer
    - generate_frames(element_func, **args) -> list - call element_func with args
    - get_config() -> ServerConfig - ports, default_fps, constants
    """

    # Subclass MUST override these
    element_name: str = "element"
    intensity_name: str = "intensity"
    registry: type[StreamRegistry]

    def __init__(self):
        self.config = self.get_config()
        self._state = self._create_state()
        self._ws_server = None
        self._http_runner = None
        self._shutdown_event = asyncio.Event()

    @abstractmethod
    def get_config(self) -> ServerConfig:
        """Return server configuration with ports, defaults, constants."""

    @abstractmethod
    def make_frame(self, element_data: list, intensity: float) -> dict:
        """Serialize element data + intensity to WS JSON frame."""

    @abstractmethod
    def generate_frames(self, element_func: callable, **args) -> list:
        """Call element_func with args, return list of frames."""

    def _create_state(self):
        """Create state object. Subclass can override for custom state."""
        return self.StreamState(self)

    # ===== STATE MANAGEMENT =====

    class StreamState:
        """Server state with caching - created by _create_state()."""

        def __init__(self, server: "StreamServer"):
            self.server = server
            config = server.config
            self.element_name = self._default_element()
            self.element_args: dict = {}
            self.fps = config.DEFAULT_FPS
            self.intensity = config.DEFAULT_INTENSITY
            self._frame_cache: list | None = None
            self._cache_key: tuple | None = None
            self.frame_count = 0
            self.connected_clients = 0
            self.start_time = time.time()

        def _default_element(self) -> str:
            """Get first available element as default."""
            specs = self.server.registry.specs()
            return next(iter(specs)) if specs else "off"

        def get_element(self) -> list:
            """Get cached element frames or generate new ones."""
            key = (self.element_name, json.dumps(self.element_args, sort_keys=True))
            if self._cache_key != key:
                func = self.server.registry.ELEMENTS.get(
                    self.element_name,
                    self.server.registry.ELEMENTS.get(self._default_element()),
                )
                if func:
                    self._frame_cache = self.server.generate_frames(
                        func, **self.element_args
                    )
                else:
                    self._frame_cache = []
                self._cache_key = key
                self.frame_count = 0
            return self._frame_cache or []

        def set_element(self, name: str, **args):
            if name not in self.server.registry.ELEMENTS:
                available = list(self.server.registry.ELEMENTS.keys())
                raise ValueError(
                    f"Unknown {self.server.element_name}: {name}. Available: {available}"
                )
            self.element_name = name
            self.element_args = args

        def set_fps(self, fps: float):
            self.fps = max(1, min(60, fps))

        def set_intensity(self, intensity: float):
            self.intensity = max(0.0, min(1.0, intensity))

        def apply_intensity(self, frames: list) -> list:
            """Apply intensity to frames. Override in subclass if needed."""
            if self.intensity >= 1.0:
                return frames
            return [
                [[int(c * self.intensity) for c in led] for led in frame]
                for frame in frames
            ]

        def status(self) -> dict:
            return {
                self.server.element_name: self.element_name,
                f"{self.server.element_name}_args": self.element_args,
                "fps": self.fps,
                self.server.intensity_name: self.intensity,
                "frames_in_element": len(self.get_element())
                if self._frame_cache
                else 0,
                "frame_index": self.frame_count,
                "connected_clients": self.connected_clients,
                "uptime_seconds": time.time() - self.start_time,
            }

    @property
    def state(self):
        return self._state

    # ===== WEBSOCKET HANDLER =====

    async def ws_handler(self, websocket):
        self._state.connected_clients += 1
        client_ip = (
            websocket.remote_address[0] if websocket.remote_address else "unknown"
        )
        print(
            f"Gateway connected from {client_ip} (clients: {self._state.connected_clients})"
        )

        try:
            element_data = self._state.get_element()
            frame_idx = 0
            frame_interval = 1.0 / self._state.fps
            last_time = time.monotonic()

            while True:
                # Check if element/FPS/intensity changed
                current_element = self._state.get_element()
                current_interval = 1.0 / self._state.fps

                if (
                    current_element is not element_data
                    or abs(current_interval - frame_interval) > 0.001
                    or self._state.intensity != getattr(self, "_last_intensity", None)
                ):
                    element_data = current_element
                    frame_interval = current_interval
                    frame_idx = 0
                    self._last_intensity = self._state.intensity

                if not element_data:
                    await asyncio.sleep(0.1)
                    continue

                # Apply intensity and get frame
                adjusted_frames = self._state.apply_intensity(element_data)
                frame_colors = adjusted_frames[frame_idx]

                # Serialize frame
                frame = self.make_frame(frame_colors, self._state.intensity)

                await websocket.send(json.dumps(frame))
                frame_idx = (frame_idx + 1) % len(element_data)
                self._state.frame_count = frame_idx

                # Precise timing using monotonic clock to avoid drift
                elapsed = time.monotonic() - last_time
                sleep_time = max(0, frame_interval - elapsed)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                last_time = time.monotonic()

        except ConnectionClosed as e:
            print(f"Gateway disconnected from {client_ip} (code={e.code})")
        except Exception as e:
            print(f"WS error with {client_ip}: {e}")
        finally:
            self._state.connected_clients -= 1

    # ===== HTTP ENDPOINTS =====

    async def http_status(self, request):
        return web.json_response(self._state.status())

    async def http_set_element(self, request):
        try:
            data = await request.json()
            name = data.get(self.element_name)
            args = data.get("args", {})
            self._state.set_element(name, **args)
            return web.json_response({"ok": True, "status": self._state.status()})
        except ValueError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def http_set_fps(self, request):
        try:
            data = await request.json()
            fps = float(data.get("fps", self.config.DEFAULT_FPS))
            self._state.set_fps(fps)
            return web.json_response({"ok": True, "status": self._state.status()})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def http_set_intensity(self, request):
        try:
            data = await request.json()
            intensity = float(
                data.get(self.intensity_name, self.config.DEFAULT_INTENSITY)
            )
            self._state.set_intensity(intensity)
            return web.json_response({"ok": True, "status": self._state.status()})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def http_list_elements(self, request):
        specs = self.registry.specs()
        builtin_names = self.registry.builtin_names()
        return web.json_response(
            {
                f"{self.element_name}s": [
                    {
                        "name": name,
                        "args": specs[name].args,
                        "description": specs[name].description,
                        "builtin": name in builtin_names,
                    }
                    for name in specs.keys()
                ],
                "current": self._state.element_name,
                "args": self._state.element_args,
            }
        )

    async def http_reload_elements(self, request):
        try:
            elements = self.registry.reload_all()
            return web.json_response(
                {
                    "ok": True,
                    f"{self.element_name}s": elements,
                    "current": self._state.element_name,
                    "message": f"Reloaded {len(elements)} {self.element_name}s",
                }
            )
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    # ===== SERVER LIFECYCLE =====

    def _setup_routes(self, app: web.Application):
        """Setup HTTP routes. Override to add custom routes."""
        element = self.element_name
        intensity = self.intensity_name

        app.router.add_get("/status", self.http_status)
        app.router.add_post(f"/{element}", self.http_set_element)
        app.router.add_post("/fps", self.http_set_fps)
        app.router.add_post(f"/{intensity}", self.http_set_intensity)
        app.router.add_get(f"/{element}s", self.http_list_elements)
        app.router.add_post(f"/{element}s/reload", self.http_reload_elements)

    async def start(self):
        """Start both WS and HTTP servers."""
        # HTTP server
        app = web.Application()
        self._setup_routes(app)

        self._http_runner = web.AppRunner(app)
        await self._http_runner.setup()
        http_site = web.TCPSite(
            self._http_runner, self.config.WS_HOST, self.config.HTTP_PORT
        )
        await http_site.start()

        # WebSocket server
        self._ws_server = await websockets.serve(
            self.ws_handler, self.config.WS_HOST, self.config.WS_PORT
        )

        print(f"{self.__class__.__name__} running:")
        print(
            f"  WebSocket (gateway): ws://{self.config.WS_HOST}:{self.config.WS_PORT}"
        )
        print(
            f"  HTTP (control):      http://{self.config.WS_HOST}:{self.config.HTTP_PORT}"
        )
        specs = self.registry.specs()
        print(f"  {self.element_name.capitalize()}s: {list(specs.keys())}")
        print()

        # Signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._shutdown_event.set)
            except NotImplementedError:
                pass

        await self._shutdown_event.wait()
        await self.stop()

    async def stop(self):
        """Stop both servers."""
        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()
        if self._http_runner:
            await self._http_runner.cleanup()
        print(f"{self.__class__.__name__} stopped")
