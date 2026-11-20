# Streams Package

Unified architecture for streaming data to StackChan (LED, Pose, Beat, etc.).

## Structure

```
reflexd/streams/
├── __init__.py              # Base exports: StreamRegistry, StreamServer, StreamClient, StreamCLI
├── base/                    # Generic base classes (template for new stream types)
│   ├── registry.py          # StreamRegistry with SpecProtocol
│   ├── server.py            # StreamServer (Template Method)
│   ├── client.py            # StreamClient (HTTP control)
│   └── cli.py               # StreamCLI (auto-generates subcommands)
└── led/                     # LED Stream implementation
    ├── __init__.py          # Public API
    ├── config.py            # LEDConfig
    ├── builtins.py          # Built-in patterns
    ├── registry.py          # LEDPatternRegistry
    ├── server.py            # LEDStreamServer
    ├── client.py            # LEDStreamClient (HTTP + MCP)
    ├── cli.py               # LEDStreamCLI
    └── patterns/
        └── custom/          # User patterns (hot-reloadable)
```

## Creating a New Stream Type

To add a new stream type (e.g., `follow_pose_stream`, `beat_mode`):

1. Create `reflexd/streams/<type>/` with the same structure as `led/`
2. Implement:
   - `config.py` - Configuration class attributes
   - `builtins.py` - Built-in elements (functions returning frames)
   - `registry.py` - Extends `StreamRegistry` with `custom_dir`, `module_prefix`
   - `server.py` - Extends `StreamServer` implementing 6 hooks
   - `client.py` - Composes `StreamClient` + transport-specific methods
   - `cli.py` - Extends `StreamCLI` with type-specific args
3. Add `__init__.py` exporting public API
4. Add tests in `tests/unit/streams/<type>/` and `tests/integration/streams/<type>/`

## Base Classes

### StreamRegistry
Generic registry for any stream element (patterns, poses, beats).

```python
class MyRegistry(StreamRegistry):
    custom_dir = Path(__file__).parent / "patterns" / "custom"
    module_prefix = "my_stream.patterns.custom"
    spec_attr = "PATTERN"  # attribute to look for in custom modules
    
    _BUILTIN_SPECS = {
        "element_name": SpecProtocol(...),
    }

MyRegistry.reload_all()  # Load builtins + custom
```

Custom modules just export an object with `name`, `func`, `args`, `description` (SpecProtocol).

### StreamServer (Template Method)
Base class contains full algorithm. Subclass implements 6 hooks:

```python
class MyStreamServer(StreamServer):
    element_name = "pattern"      # "pattern" | "pose" | "mode"
    intensity_name = "brightness" # "brightness" | "smoothing" | "sensitivity"
    registry = MyRegistry
    
    def get_config(self) -> ServerConfig: ...
    def make_frame(self, element_data, intensity) -> dict: ...
    def generate_frames(self, element_func, **args) -> list: ...
```

Auto-provides: WS lifecycle, HTTP routing, state, caching, signals.

### StreamClient
Pure HTTP control client. Compose in stream-specific client:

```python
class MyStreamClient:
    def __init__(self):
        self.http_client = StreamClient(url, element_name, intensity_name)
    
    # Add transport-specific methods (MCP, WS, gRPC)
    async def stream_start(self): ...
```

### StreamCLI
Auto-generates subcommands from registry. Subclass adds type-specific args:

```python
class MyStreamCLI(StreamCLI):
    element_name = "pattern"
    intensity_name = "brightness"
    registry = MyRegistry
    http_url = "http://localhost:8773"
    
    def create_stream_client(self): return MyStreamClient()
    def add_element_args(self, parser): parser.add_argument("--color", ...)
```

## Testing

- Unit: `tests/unit/streams/base/` (registry, server state, client HTTP, CLI generation)
- Integration: `tests/integration/streams/<type>/` (full cycle)