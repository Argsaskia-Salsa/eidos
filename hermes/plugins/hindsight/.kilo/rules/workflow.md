# Workflow Rules for hindsight Plugin Changes

## Core Principle
**Successful change = all tests pass + linter clean + type-check clean + coverage maintained**

---

## 0. Project Architecture & File Map

| Module | Path | Responsibility |
|--------|------|----------------|
| **Provider** | `hindsight_hermes/__init__.py` | `HindsightMemoryProvider` implementing Hermes `MemoryProvider` interface. Retain/recall/reflect, lifecycle, writer thread, embedded daemon management. |
| **Settings** | `hindsight_hermes/settings.py` | Config defaults, validation, normalization, environment variable parsing, provider default models. |
| **Config Schema** | `hindsight_hermes/config_schema.py` | Dashboard-compatible schema (Hermes core imports this). `ProviderConfigSchema` + `ProviderField` definitions. |
| **Embedded** | `hindsight_hermes/embedded.py` | Local embedded daemon lifecycle: start/stop/health/materialize env, port health grace, profile env files, `_ensure_local_runtime`. |
| **Templates** | `hindsight_hermes/templates.py` | Starter memory templates for `hermes memory setup`. `apply_template`, `fetch_hermes_templates`, `fetch_manifest`. |
| **Setup Wizard** | `hindsight_hermes/setup.py` | Interactive wizard for `hermes memory setup`. Imports private helpers from `hermes_cli.memory_setup`. |
| **Tests** | `tests/` | 4 test files: `test_provider.py`, `test_settings.py`, `test_local_runtime_install.py`, `conftest.py`. Fake Hermes stubs + `FakeClient` recording SDK calls. |

---

## 1. Testing (before changes)
- [ ] Run existing tests: `uv run pytest tests/ -v`
- [ ] Check coverage: `uv run pytest --cov=hindsight_hermes --cov-report=term-missing`
- [ ] Run type check: `uv run mypy hindsight_hermes` (once mypy configured in pyproject.toml)
- [ ] Record baseline: test count, coverage %, mypy errors

---

## 2. Writing Tests (for new functionality)
- [ ] Add unit tests for each new function/method in appropriate test file
- [ ] Add edge-case tests (`None`, empty strings, exceptions, boundary values)
- [ ] Add regression tests for fixed bugs
- [ ] Use `FakeClient` from `conftest.py` to record/verify Hindsight SDK calls
- [ ] Stub Hermes core interfaces — see `conftest.py:_install_hermes_stubs`
- [ ] Test all three modes: `cloud`, `local_embedded`, `local_external`
- [ ] Test provider lifecycle: `initialize()` → `sync_turn()` → `shutdown()`
- [ ] Test config loading priority: profile JSON → legacy JSON → env vars
- [ ] Async tests: `@pytest.mark.asyncio` + proper async context manager mocks

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
- [ ] Follow existing patterns (provider, embedded, settings, config_schema)
- [ ] Preserve multi-layer config loading order: profile-scoped JSON → legacy `~/.hindsight/config.json` → env vars
- [ ] Update both `config_schema.py` AND `get_config_schema()` together when adding config keys
- [ ] Add type hints for new functions
- [ ] Update docstrings when changing public API
- [ ] Run `uv run mypy hindsight_hermes` after each logical change block (once configured)

---

## 5. Fixing to Pass Tests
- [ ] Run tests after each logical change block
- [ ] Fix only what breaks — no preventive refactoring
- [ ] **Update tests ONLY when business requirements explicitly change** — do NOT modify tests to match buggy new code
- [ ] For async: mock `aiohttp.ClientSession.post` returning async context manager
- [ ] If test fails due to your change and the test reflects correct expected behavior → fix the code, not the test
- [ ] Embedded daemon tests: use `_ensure_local_runtime` mocking, test port health grace timeout logic

---

## 6. Linter (ruff)
- [ ] `uv run ruff check .` — must be 0 errors
- [ ] `uv run ruff format .` — auto-format
- [ ] Config in `pyproject.toml`: line-length=100, target-version=py311
- [ ] Ignores only for tests: `S101` (assert), `T201` (print)

---

## 7. Static Type Checking (mypy)
- [ ] `uv run mypy hindsight_hermes` — must be 0 errors (configure in `pyproject.toml:tool.mypy`)
- [ ] No `# type: ignore` or `# noqa` suppressions without explicit justification in PR/commit
- [ ] Add missing stubs via `uv run mypy --install-types` if needed
- [ ] Configure `pyproject.toml` with:
  ```toml
  [tool.mypy]
  python_version = "3.11"
  warn_return_any = true
  warn_unused_configs = true
  disallow_untyped_defs = true
  ```

---

## 8. Strict Guardrails (LLM Prohibitions)
| Prohibition | Rationale |
|-------------|-----------|
| ❌ Add new dependencies without explicit user approval | Keeps supply chain minimal; avoids version conflicts with Hermes core |
| ❌ Suppress linter/type errors via `# noqa` or `# type: ignore` | Hides real issues; defeats automation |
| ❌ Change public signatures of `MemoryProvider` methods (`initialize`, `sync_turn`, `shutdown`, `recall_status`, `health_check`, `is_available`, `unavailable_reason`, `save_config`, `post_setup`, `get_config_schema`) without updating integration tests | Breaks Hermes plugin contract |
| ❌ Change tool schemas (`RETAIN_SCHEMA`, `RECALL_SCHEMA`, `REFLECT_SCHEMA`, `APPLY_TEMPLATE_SCHEMA`) without updating dashboard/tests | Breaks LLM tool calling contract |
| ❌ Commit `# noqa: BLE001` or broad exception catches without logging | Swallows errors silently |
| ❌ Modify config loading priority order without migration path | Breaks existing user configurations |

---

## 9. Code Coverage
- [ ] `uv run pytest --cov=hindsight_hermes --cov-report=term-missing`
- [ ] Coverage must not drop below **current baseline** (recorded in step 1)
- [ ] New code must have tests
- [ ] Document untestable paths (embedded daemon subprocess, Hermes core internals, network calls)

---

## 10. Unit vs Regression vs Integration Tests
| Type | When | Example |
|------|------|---------|
| Unit | New function/method | `_validate_config`, `_resolve_bank_id_template`, `_check_api_supports_update_mode_append` |
| Unit | Helper logic | `_scoped_setting`, `_mint_document_id`, `_run_sync`, `_writer_loop` |
| Regression | Bug fixed | "rate limit not enforced", "resume-overwrite fix", "port health grace timeout" |
| Integration | Real Hermes plugin load | `tests/test_provider.py` (provider lifecycle with FakeClient) |

---

## 11. Final Check (Definition of Done)
- [ ] `uv run pytest tests/ -v` — **all tests pass** (baseline + new)
- [ ] `uv run ruff check .` — **All checks passed**
- [ ] `uv run mypy hindsight_hermes` — **Success: no issues found**
- [ ] `uv run pytest --cov=hindsight_hermes` — **≥ baseline coverage %**
- [ ] No new deprecation warnings
- [ ] README/CHANGELOG updated when config/API changes
- [ ] If config schema changed: verify `hermes memory setup` still works

---

## Quick Check Commands
```bash
# Full cycle
cd C:\Development\Erika\eidos\hermes\plugins\hindsight
uv run pytest tests/ -v && uv run ruff check . && uv run mypy hindsight_hermes && uv run pytest --cov=hindsight_hermes --cov-report=term-missing
```

---

## Change History
| Date | Change | Tests | Coverage |
|------|--------|-------|----------|
| 2026-11-23 | Initial workflow rules created | — | — |

---

## Project Structure
```
hermes/plugins/hindsight/
├── pyproject.toml          # pytest, ruff, (mypy to add) config
├── plugin.yaml             # Hermes plugin metadata
├── hindsight_hermes/       # Main package (imported as hindsight_hermes)
│   ├── __init__.py         # HindsightMemoryProvider + register()
│   ├── settings.py         # Config defaults, validation, normalization
│   ├── config_schema.py    # Dashboard-compatible schema (Hermes core imports)
│   ├── embedded.py         # Local embedded daemon lifecycle
│   ├── templates.py        # Starter memory templates
│   └── setup.py            # Interactive setup wizard
├── tests/
│   ├── conftest.py         # Hermes stubs + plugin loader + FakeClient
│   ├── test_provider.py    # Provider logic (retain/recall/reflect/lifecycle)
│   ├── test_settings.py    # Config parsing & validation
│   └── test_local_runtime_install.py  # Embedded runtime dependency logic
├── templates/              # Template manifests (if any)
├── docs/                   # Documentation assets
├── README.md               # Installation, usage, development
├── uv.lock                 # Locked dependencies
└── .hermes-catalog.json    # Hermes catalog entry
```