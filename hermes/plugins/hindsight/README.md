# Hindsight Memory Provider for Hermes Agent

Long-term memory with knowledge graph, entity resolution, and multi-strategy retrieval. Supports cloud, local embedded, and local external modes.

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) memory-provider plugin. It used to ship inside Hermes as `plugins/memory/hindsight/`; Nous Research moved every memory provider out of the core tree and handed this one over, so it now lives here and is maintained by the Hindsight team. This directory is the live source, and the Hermes catalog entry points here.

**Upstream:** [vectorize-io/hindsight/tree/main/hindsight-integrations/hermes](https://github.com/vectorize-io/hindsight/tree/main/hindsight-integrations/hermes)  
**License:** [MIT](LICENSE) — Copyright (c) 2025 Vectorize AI, Inc.

## Install

```bash
hermes plugins install vectorize-io/hindsight/hindsight-integrations/hermes
hermes memory setup    # select "hindsight"
```

Dependencies in `pyproject.toml` are installed into the Hermes venv automatically and survive `hermes update`.

`hermes plugins enable` is *not* what activates a memory provider — Hermes treats providers as
`kind: exclusive` and its plugin-enable gate deliberately skips them. A provider is activated by
`memory.provider: <name>` in `config.yaml`, which `hermes memory setup` writes. Setup also installs
the mode-dependent extras (`local_embedded` needs `hindsight-all`, not just the client), so
`plugins install` on its own leaves the provider reporting "not available" in embedded mode.

While Hermes still bundles `plugins/memory/hindsight/`, **the bundled copy wins** — provider lookup
is bundled → `~/.hermes/plugins/` → project → entry point, first hit wins, so installing this plugin
alongside the bundled one is inert. When Hermes drops the bundled copy, `hermes update` migrates
existing users automatically via `hermes_cli/memory_provider_migration.py`, which resolves the
provider name against the Hermes plugin catalog.

The catalog entry lives in their repo at
[`plugin-catalog/hindsight.yaml`](https://github.com/NousResearch/hermes-agent/blob/main/plugin-catalog/hindsight.yaml)
and pins this directory at a specific commit. **Changes here do not reach users until that pin
moves**, so anything shipped from this tree needs a follow-up PR to hermes-agent bumping `sha` and
`version` together.

The catalog entry lives in their repo at
[`plugin-catalog/hindsight.yaml`](https://github.com/NousResearch/hermes-agent/blob/main/plugin-catalog/hindsight.yaml)
and pins this directory at a specific commit. **Changes here do not reach users until that pin
moves**, so anything shipped from this tree needs a follow-up PR to hermes-agent bumping `sha` and
`version` together.

`local_embedded` mode needs `hindsight-all`, which `pyproject.toml` deliberately does not declare
(it would push the local-ML stack onto cloud-mode users). The setup wizard installs it, and
`embedded.py::_ensure_local_runtime` self-installs it on the availability check as a backstop, so
embedded mode no longer depends on the `if provider_name == "hindsight"` special case in Hermes
core's `memory_setup.py` — which leaves the tree when the bundled provider does.

## Architecture Overview

The Hindsight provider implements Hermes' `MemoryProvider` interface and connects Hermes Agent
to the Hindsight memory service (cloud or local). It acts as a **memory bridge** with three
responsibilities:

1. **Retain** — Extracts and stores structured memories from conversation turns
2. **Recall** — Performs multi-strategy retrieval (semantic search + entity graph) for context injection
3. **Reflect** — Synthesizes cross-memory insights via LLM-powered reflection

### Data Flow

```
Hermes Agent (turn) 
    │
    ├─► auto_retain (every N turns) ──► hindsight-client ──► Hindsight API
    │
    ├─► auto_recall (before each turn) ──► hindsight-client ──► Hindsight API
    │       │
    │       └─► Injects memories into conversation context
    │
    └─► LLM Tool calls:
            ├─ hindsight_retain(content, tags)
            ├─ hindsight_recall(query, budget, tags)
            └─ hindsight_reflect(question, scope)
```

### Components

| File               | Role                                                                     |
|--------------------|--------------------------------------------------------------------------|
| `__init__.py`      | Main provider class `HindsightMemoryProvider` + `register()` entry point |
| `settings.py`      | Configuration schema, validation, defaults                               |
| `config_schema.py` | Dashboard-compatible config schema (Hermes core imports this)            |
| `embedded.py`      | Local embedded daemon lifecycle (start/stop/health/materialize env)      |
| `templates.py`     | Starter memory templates for `hermes memory setup`                       |
| `setup.py`         | Interactive wizard for `hermes memory setup`                             |

## Hooks & Tools

The plugin registers one hook and three tools:

| Hook               | Trigger          | Description                                   |
|--------------------|------------------|-----------------------------------------------|
| `on_session_start` | Session boundary | Initializes provider, starts auto-recall task |

| Tool                | Mode              | Description                                                    |
|---------------------|-------------------|----------------------------------------------------------------|
| `hindsight_retain`  | `hybrid`, `tools` | Store information with auto entity extraction; optional `tags` |
| `hindsight_recall`  | `hybrid`, `tools` | Multi-strategy search (semantic + entity graph)                |
| `hindsight_reflect` | `hybrid`, `tools` | Cross-memory synthesis (LLM-powered)                           |

### Tool Payloads

**hindsight_retain**
```json
{
  "name": "hindsight_retain",
  "arguments": {
    "content": "string",
    "tags": ["optional", "list", "of", "tags"]
  }
}
```

**hindsight_recall**
```json
{
  "name": "hindsight_recall",
  "arguments": {
    "query": "search query",
    "budget": "mid",
    "tags": ["optional", "filter", "tags"],
    "types": ["observation", "world", "experience"]
  }
}
```

**hindsight_reflect**
```json
{
  "name": "hindsight_reflect",
  "arguments": {
    "question": "synthesis question",
    "scope": "all"
  }
}
```

## Configuration

Config file: `~/.hermes/hindsight/config.json` (written by `hermes memory setup`)

### Connection

| Key       | Default                              | Description                                    |
|-----------|--------------------------------------|------------------------------------------------|
| `mode`    | `cloud`                              | `cloud`, `local_embedded`, or `local_external` |
| `api_url` | `https://api.hindsight.vectorize.io` | API URL (cloud and local_external modes)       |

### Memory Bank

| Key                   | Default  | Description                                                                                         |
|-----------------------|----------|-----------------------------------------------------------------------------------------------------|
| `bank_id`             | `hermes` | Memory bank name (static fallback)                                                                  |
| `bank_id_template`    | —        | Dynamic template with placeholders: `{profile}`, `{workspace}`, `{platform}`, `{user}`, `{session}` |
| `bank_mission`        | —        | Reflect mission for synthesis framing                                                               |
| `bank_retain_mission` | —        | Retain mission for extraction steering                                                              |

### Recall

| Key                      | Default       | Description                                       |
|--------------------------|---------------|---------------------------------------------------|
| `recall_budget`          | `mid`         | `low` / `mid` / `high`                            |
| `recall_prefetch_method` | `recall`      | `recall` (raw facts) or `reflect` (LLM synthesis) |
| `recall_max_tokens`      | `4096`        | Max tokens for recall results                     |
| `recall_max_input_chars` | `800`         | Max input query length                            |
| `recall_types`           | `observation` | Fact types: `observation,world,experience`        |
| `auto_recall`            | `true`        | Auto-recall before each turn                      |
| `recall_sync`            | `false`       | Sync recall against current message               |

### Retain

| Key                    | Default                                          | Description                             |
|------------------------|--------------------------------------------------|-----------------------------------------|
| `auto_retain`          | `true`                                           | Automatically retain conversation turns |
| `retain_every_n_turns` | `1`                                              | Retain every N turns                    |
| `retain_context`       | `conversation between Hermes Agent and the User` | Context label                           |
| `retain_tags`          | —                                                | Default tags for retained memories      |

### Integration

| Key           | Default  | Description                                                          |
|---------------|----------|----------------------------------------------------------------------|
| `memory_mode` | `hybrid` | `hybrid` (auto + tools), `context` (auto only), `tools` (tools only) |

### Environment Variables

| Variable                     | Description                 |
|------------------------------|-----------------------------|
| `HINDSIGHT_API_KEY`          | API key for Hindsight Cloud |
| `HINDSIGHT_LLM_API_KEY`      | LLM API key for local mode  |
| `HINDSIGHT_API_LLM_BASE_URL` | LLM Base URL for local mode |
| `HINDSIGHT_API_URL`          | Override API endpoint       |
| `HINDSIGHT_BANK_ID`          | Override bank name          |
| `HINDSIGHT_BUDGET`           | Override recall budget      |
| `HINDSIGHT_MODE`             | Override mode               |

## Installation

### Method 1: Hermes CLI (Recommended)

```bash
hermes plugins install vectorize-io/hindsight/hindsight-integrations/hermes
hermes memory setup    # select "hindsight"
```

### Method 2: Manual (Directory-based)

Since this is a directory-based Hermes plugin (not a pip package), copy or symlink:

**Linux/macOS (symlink — recommended for development):**
```bash
ln -sf /path/to/hindsight ~/.hermes/plugins/hindsight
hermes memory setup
```

**Windows (PowerShell):**
```powershell
# Symlink
New-Item -ItemType SymbolicLink -Path $env:USERPROFILE\.hermes\plugins\hindsight -Target C:\path\to\hindsight
hermes memory setup
```

### Verify Installation

```bash
hermes plugins list
# Should show: hindsight (available)
```

## Usage

After setup, memories are automatically retained and recalled:

- **Auto-retain**: Every N turns (configurable), conversation is sent to Hindsight
- **Auto-recall**: Before each turn, relevant memories are injected into context
- **Tools**: LLM can explicitly call `hindsight_retain`, `hindsight_recall`, `hindsight_reflect`

### Manual Retain/Recall

```python
# Via tool calls from LLM or scripts
await hindsight_retain(content="User prefers Russian responses", tags=["preference"])
result = await hindsight_recall(query="user language preferences", budget="high")
await hindsight_reflect(question="What are the user's communication preferences?")
```

## Development

### Running Tests

```bash
# Install dev dependencies
uv sync
# or
pip install -e ".[dev]"

# Run all tests
uv run pytest tests/ -v

# Run specific test file
uv run pytest tests/test_provider.py -v

# Coverage
uv run pytest tests/ --cov=hindsight --cov-report=term-missing
```

### Test Structure

```
tests/
├── conftest.py                 # Hermes core stubs + plugin loader
├── test_provider.py            # Provider logic (retain/recall/reflect)
├── test_settings.py            # Config parsing & validation
└── test_local_runtime_install.py  # Embedded runtime dependency logic
```

The tests stub Hermes core interfaces (`MemoryProvider`, secret scope, `cfg_get`, ...) and load
the plugin directory as a package — the same way Hermes' plugin loader does. A recording fake
client stands in for the Hindsight SDK so tests assert what the provider actually sends.

### Linting

```bash
uv run ruff check .
uv run ruff format .
```

### Key Implementation Notes

- `config_schema.py` imports from `plugins.memory.config_schema` on purpose: Hermes core loads
  that file directly and its dashboard expects core's `ProviderConfigSchema` type.
- The setup wizard imports private helpers from `hermes_cli.memory_setup` (`_curses_select`,
  `_prompt`, ...). Those are Hermes internals — expect to own a copy or drop the wizard hook.
- `pyproject.toml` is the only dependency authority; bump it when a newer client is needed.
- The plugin auto-upgrades `hindsight-client` on session start if an older version is detected.

## License

[MIT License](LICENSE) — Copyright (c) 2025 Vectorize AI, Inc.

Maintained by the Hindsight team (originally Nous Research).
