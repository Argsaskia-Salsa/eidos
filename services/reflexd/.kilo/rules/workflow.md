# Workflow Rules for reflexd Changes

## Core Principle
**Successful change = all tests pass + linter clean + coverage maintained**

---

## 1. Testing (before changes)
- [ ] Run existing tests: `pytest tests/ -v`
- [ ] Check coverage: `pytest --cov=reflexd --cov-report=term-missing`
- [ ] Record baseline: test count, coverage %, warnings

## 2. Writing Tests (for new functionality)
- [ ] Add unit tests for each new function/method
- [ ] Add edge-case tests (None, empty strings, exceptions)
- [ ] Add regression tests for fixed bugs
- [ ] Use mocks for external deps (HTTP gateway, aiohttp)
- [ ] Async tests: `@pytest.mark.asyncio` + proper async context manager mocks

## 3. Making Changes
- [ ] Implement minimal necessary changes
- [ ] Follow existing patterns (Registry, GatewayClient, safety, server handlers)
- [ ] Add type hints for new functions
- [ ] Update docstrings when changing public API

## 4. Fixing to Pass Tests
- [ ] Run tests after each logical change block
- [ ] Fix only what breaks — no preventive refactoring
- [ ] If test checks old behavior → update test, not code
- [ ] For async: mock `aiohttp.ClientSession.post` returning async context manager

## 5. Linter (ruff)
- [ ] `ruff check .` — must be 0 errors
- [ ] `ruff format .` — auto-format
- [ ] Config in `pyproject.toml`: line-length=100, target-version=py311
- [ ] Ignores only for tests: `S101`, `T201`

## 6. Code Coverage
- [ ] `pytest --cov=reflexd --cov-report=term-missing`
- [ ] Coverage must not drop below current (79%)
- [ ] New code must have tests
- [ ] Document untestable paths (gateway connection, main entrypoint)

## 7. Unit vs Regression Tests
| Type | When | Example |
|------|------|---------|
| Unit | New function/method | `clamp_move_head`, `check_rate_limit`, `handle_inject_event` |
| Unit | Helper logic | `on_event` decorator, `get_gateway_client` |
| Regression | Bug fixed | "rate limit not enforced", "handler overwrite warning" |
| Integration | Real HTTP/MCP | `tests/integration/test_mcp_endpoints.py` |

## 8. Final Check (Definition of Done)
- [ ] `pytest tests/ -v` — **56 passed** (or more)
- [ ] `ruff check .` — **All checks passed**
- [ ] `pytest --cov=reflexd` — **≥ 79%**
- [ ] No new deprecation warnings
- [ ] README/CHANGELOG updated when config/API changes

---

## Quick Check Commands
```bash
# Full cycle
cd C:\Development\Erika\eidos\services\reflexd
pytest tests/ -v && ruff check . && pytest --cov=reflexd --cov-report=term-missing
```

## Change History
| Date | Change | Tests | Coverage |
|------|--------|-------|----------|
| 2026-11-16 | Initial test implementation | 56/56 | 79% |

---

## Project Structure
```
services/reflexd/
├── pyproject.toml          # pytest, ruff, mypy config
├── reflexd/
│   ├── __init__.py         # on_event, call
│   ├── registry.py         # handler registry
│   ├── safety.py           # clamp_move_head, rate_limit
│   ├── gateway_client.py   # MCP gateway client
│   ├── gateway_tools.py    # tool definitions
│   ├── server.py           # MCP server handlers
│   ├── main.py             # entrypoint
│   └── reflexes/           # thinking, idle, greeting
└── tests/
    ├── conftest.py         # fixtures
    ├── unit/               # 41 unit tests
    │   ├── test_registry.py
    │   ├── test_safety.py
    │   ├── test_gateway_client.py
    │   ├── test_server_handlers.py
    │   ├── test_reflex_thinking.py
    │   ├── test_reflex_idle.py
    │   └── test_reflex_greeting.py
    └── integration/        # 15 integration tests
        └── test_mcp_endpoints.py
```