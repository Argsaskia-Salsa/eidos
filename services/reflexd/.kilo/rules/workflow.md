# Workflow Rules for reflexd Changes

## Core Principle
**Successful change = all tests pass + linter clean + type-check clean + coverage maintained**

---

## 0. Project Architecture & File Map

| Module | Path | Responsibility |
|--------|------|----------------|
| **Registry** | `reflexd/registry.py` | Event handler registration (`on_event`, `get_handler`, `list_handlers`, `clear`). Metadata: priority, cleanup, conflict_group. Global in-memory dict. |
| **GatewayClient** | `reflexd/gateway_client.py` | MCP JSON-RPC client (aiohttp). `connect()`, `call(tool, args)`, `close()`. Applies safety clamps & rate limits for `move_head`. Circuit breaker + retry logic. |
| **Safety** | `reflexd/safety.py` | `clamp_move_head(yaw, pitch, speed)` → clamped tuple. |
| **Server Handlers** | `reflexd/server.py` | MCP server endpoints: `handle_inject_event`, `handle_call`, `handle_list_reflexes`, `handle_list_gateway_tools`, `handle_reload_reflexes`, `handle_jsonrpc`, `health_check`, `cancel_reflex`, `list_running_reflexes`. Uses `Registry` + `GatewayClient`. Preemption by priority/conflict_group. |
| **Reflexes** | `reflexd/reflexes/` | Event implementations: `thinking.py`, `idle.py`, `greeting.py`. Decorated with `@on_event`. |
| **Entrypoint** | `reflexd/main.py` | CLI, config, `REFLEXES_DIR`, `run_server()`. Signal handling, graceful shutdown. |
| **Tools** | `reflexd/gateway_tools.py` | MCP tool definitions (`GATEWAY_TOOLS`). |
| **Circuit Breaker** | `reflexd/circuit_breaker.py` | Failure threshold, recovery timeout, half-open state. Protects gateway calls. |
| **Event Bus** | `reflexd/event_bus.py` | Pub/sub for internal events (`publish`, `subscribe`, `unsubscribe`). |
| **Interruption** | `reflexd/interruption.py` | Reflex cancellation, preemption, same-name replacement logic. |
| **MCP Tools** | `reflexd/mcp_tools.py` | Tool implementations: `cancel_reflex`, `list_running_reflexes`. |
| **Unit Tests** | `tests/unit/` | 14 files, 122 tests. One per module + reflexes + new modules. |
| **Integration Tests** | `tests/integration/test_mcp_endpoints.py` | 15 tests against real aiohttp server. |

---

## 1. Testing (before changes)
- [ ] Run existing tests: `pytest tests/ -v`
- [ ] Check coverage: `pytest --cov=reflexd --cov-report=term-missing`
- [ ] Run type check: `mypy reflexd` (or `pyright reflexd` if configured)
- [ ] Record baseline: test count, coverage %, mypy errors

---

## 2. Writing Tests (for new functionality)
- [ ] Add unit tests for each new function/method
- [ ] Add edge-case tests (`None`, empty strings, exceptions, boundary values)
- [ ] Add regression tests for fixed bugs
- [ ] Use mocks for external deps (HTTP gateway, aiohttp) — see `conftest.py:mock_gateway_client`
- [ ] Async tests: `@pytest.mark.asyncio` + proper async context manager mocks
- [ ] Integration tests for new MCP endpoints/tools

---

## 3. Code Style & Python 3.11+ Conventions
- [ ] **Generics:** Use built-in `list[...]`, `dict[...]`, `tuple[...]`, `set[...]` (not `typing.List` etc.)
- [ ] **Unions:** Prefer `X | None` over `Optional[X]`
- [ ] **Data modeling:** Use `@dataclass` or Pydantic models instead of untyped `dict` for structured data
- [ ] **Async I/O:** Never use blocking I/O (`time.sleep`, `requests`, file I/O) inside `async def` — use `asyncio.sleep`, `aiohttp`, `aiofiles`
- [ ] **Logging:** Use `logging.getLogger(__name__)`; no `print()` in production code
- [ ] **Exceptions:** Never bare `except Exception: pass` — always log or re-raise. Catch specific exceptions.
- [ ] **Type hints:** Required for all new public functions/methods
- [ ] **Docstrings:** Update for public API changes (Google/NumPy style)

---

## 4. Making Changes
- [ ] Implement minimal necessary changes
- [ ] Follow existing patterns (Registry, GatewayClient, safety, server handlers)
- [ ] Add type hints for new functions
- [ ] Update docstrings when changing public API
- [ ] Run `mypy reflexd` after each logical change block

---

## 5. Fixing to Pass Tests
- [ ] Run tests after each logical change block
- [ ] Fix only what breaks — no preventive refactoring
- [ ] **Update tests ONLY when business requirements explicitly change** — do NOT modify tests to match buggy new code
- [ ] For async: mock `aiohttp.ClientSession.post` returning async context manager
- [ ] If test fails due to your change and the test reflects correct expected behavior → fix the code, not the test

---

## 6. Linter (ruff)
- [ ] `ruff check .` — must be 0 errors
- [ ] `ruff format .` — auto-format
- [ ] Config in `pyproject.toml`: line-length=100, target-version=py311
- [ ] Ignores only for tests: `S101` (assert), `T201` (print)

---

## 7. Static Type Checking (mypy)
- [ ] `mypy reflexd` — must be 0 errors (configured in `pyproject.toml:tool.taskipy.tasks.typecheck`)
- [ ] No `# type: ignore` or `# noqa` suppressions without explicit justification in PR/commit
- [ ] Add missing stubs via `mypy --install-types` if needed

---

## 8. Strict Guardrails (LLM Prohibitions)
| Prohibition | Rationale |
|-------------|-----------|
| ❌ Add new dependencies without explicit user approval | Keeps supply chain minimal; avoids version conflicts |
| ❌ Suppress linter/type errors via `# noqa` or `# type: ignore` | Hides real issues; defeats automation |
| ❌ Change public signatures of MCP handlers (`handle_*`), `Registry` (`on_event`, `get_handler`, `list_handlers`, `clear`), or `GatewayClient` (`connect`, `call`, `close`) without updating integration tests | Breaks MCP contract & external consumers |
| ❌ Commit `# noqa: BLE001` or broad exception catches without logging | Swallows errors silently |

---

## 9. Code Coverage
- [ ] `pytest --cov=reflexd --cov-report=term-missing`
- [ ] Coverage must not drop below **current baseline** (recorded in step 1)
- [ ] New code must have tests
- [ ] Document untestable paths (gateway connection, main entrypoint)

---

## 10. Unit vs Regression vs Integration Tests
| Type | When | Example |
|------|------|---------|
| Unit | New function/method | `clamp_move_head`, `check_rate_limit`, `handle_inject_event` |
| Unit | Helper logic | `on_event` decorator, `get_gateway_client` |
| Regression | Bug fixed | "rate limit not enforced", "handler overwrite warning" |
| Integration | Real HTTP/MCP | `tests/integration/test_mcp_endpoints.py` |

---

## 11. Final Check (Definition of Done)
- [ ] `pytest tests/ -v` — **all tests pass** (baseline + new)
- [ ] `ruff check .` — **All checks passed**
- [ ] `mypy reflexd` — **Success: no issues found**
- [ ] `pytest --cov=reflexd` — **≥ baseline coverage %**
- [ ] No new deprecation warnings
- [ ] README/CHANGELOG updated when config/API changes

---

## Quick Check Commands
```bash
# Full cycle
cd C:\Development\Erika\eidos\services\reflexd
pytest tests/ -v && ruff check . && mypy reflexd && pytest --cov=reflexd --cov-report=term-missing
```

## Change History
| Date | Change | Tests | Coverage |
|------|--------|-------|----------|
| 2026-11-16 | Initial test implementation | 56/56 | 79% |
| 2026-11-18 | Added circuit_breaker, event_bus, interruption, mcp_tools modules; extended server handlers; full test coverage | 137/137 | 96% |

---

## Project Structure
```
services/reflexd/
├── pyproject.toml          # pytest, ruff, mypy config
├── reflexd/
│   ├── __init__.py         # on_event, call, trigger_event, subscribe, unsubscribe
│   ├── registry.py         # handler registry + metadata (priority, cleanup, conflict_group)
│   ├── safety.py           # clamp_move_head
│   ├── gateway_client.py   # MCP gateway client + circuit breaker + rate limiter + retry
│   ├── gateway_tools.py    # tool definitions (GATEWAY_TOOLS)
│   ├── server.py           # MCP server handlers + preemption logic
│   ├── main.py             # entrypoint + logging + signal handling
│   ├── circuit_breaker.py  # failure threshold, recovery timeout, half-open
│   ├── event_bus.py        # internal pub/sub
│   ├── interruption.py     # cancellation, preemption, same-name replacement
│   ├── mcp_tools.py        # cancel_reflex, list_running_reflexes implementations
│   └── reflexes/           # thinking, idle, greeting
└── tests/
    ├── conftest.py         # fixtures
    ├── fixtures/           # sample reflexes for integration tests
    ├── unit/               # 122 unit tests
    │   ├── test_registry.py
    │   ├── test_registry_metadata.py
    │   ├── test_safety.py
    │   ├── test_gateway_client.py
    │   ├── test_server_handlers.py
    │   ├── test_reflex_thinking.py
    │   ├── test_reflex_idle.py
    │   ├── test_reflex_greeting.py
    │   ├── test_circuit_breaker.py
    │   ├── test_event_bus.py
    │   ├── test_interruption.py
    │   ├── test_main.py
    │   ├── test_mcp_tools.py
    │   └── test_init.py
    └── integration/        # 15 integration tests
        └── test_mcp_endpoints.py
```