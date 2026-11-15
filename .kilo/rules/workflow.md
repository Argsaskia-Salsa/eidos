# Workflow Rules for body_expression_bridge Changes

## Core Principle
**Successful change = all tests pass + linter clean + coverage maintained**

---

## 1. Testing (before changes)
- [ ] Run existing tests: `pytest tests/ -v`
- [ ] Check coverage: `pytest --cov=body_expression_bridge --cov-report=term-missing`
- [ ] Record baseline: test count, coverage %, warnings

## 2. Writing Tests (for new functionality)
- [ ] Add unit tests for each new function/method
- [ ] Add edge-case tests (None, empty strings, exceptions)
- [ ] Add regression tests for fixed bugs
- [ ] Use mocks for external deps (HTTP, worker loop)
- [ ] Async tests: `@pytest.mark.asyncio` + proper `asyncio.run_coroutine_threadsafe` mocks

## 3. Making Changes
- [ ] Implement minimal necessary changes
- [ ] Follow existing patterns (EventDispatcher, _build_payload, _is_local_platform)
- [ ] Add type hints for new functions
- [ ] Update docstrings when changing public API

## 4. Fixing to Pass Tests
- [ ] Run tests after each logical change block
- [ ] Fix only what breaks — no preventive refactoring
- [ ] If test checks old behavior → update test, not code
- [ ] For async: mock `asyncio.run_coroutine_threadsafe`, return `MagicMock` future

## 5. Linter (ruff)
- [ ] `ruff check .` — must be 0 errors
- [ ] `ruff format .` — auto-format
- [ ] Config in `pyproject.toml`: line-length=100, target-version=py311
- [ ] Ignores only for tests: `S101`, `T201`

## 6. Code Coverage
- [ ] `pytest --cov=body_expression_bridge --cov-report=term-missing`
- [ ] Coverage must not drop below current (79%)
- [ ] New code must have tests
- [ ] Document untestable paths (worker thread startup, exception paths)

## 7. Unit vs Regression Tests
| Type | When | Example |
|------|------|---------|
| Unit | New function/method | `_build_payload`, `_extract_chat_user_from_history` |
| Unit | Helper logic | `_is_local_platform`, `_schedule_post` |
| Regression | Bug fixed | "thinking_end fires on exception" |
| Integration | Real HTTP/worker | Separate `tests/test_integration.py` |

## 8. Final Check (Definition of Done)
- [ ] `pytest tests/ -v` — **37 passed** (or more)
- [ ] `ruff check .` — **All checks passed**
- [ ] `pytest --cov=body_expression_bridge` — **≥ 79%**
- [ ] No new deprecation warnings
- [ ] README/plugin.yaml updated when config/API changes

---

## Quick Check Commands
```bash
# Full cycle
cd %HOME%\.hermes\plugins\body_expression_bridge
pytest tests/ -v && ruff check . && pytest --cov=body_expression_bridge --cov-report=term-missing
```

## Change History
| Date | Change | Tests | Coverage |
|------|--------|-------|----------|
| 2026-11-14 | Refactor: EventDispatcher, tests, linter | 37/37 | 79% |
| 2026-11-15 | Rename: body_bridge → body_expression_bridge | 37/37 | 79% |