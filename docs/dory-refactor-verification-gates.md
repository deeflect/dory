---
title: Dory Refactor — Verification & Quality Gates
status: draft
type: design
created: 2026-05-22
scope: public-safe
---

# Dory Refactor — Verification & Quality Gates for Multi-Subagent Execution

This document defines the **verification gates, quality checks, stop/go criteria, and concrete commands** for each phase of the Dory refactor. Every subagent must pass its gated checks before the next subagent or phase can proceed.

Refactor phases (from `docs/dory-refactor-synthesis.md`):

- **Phase 0** — Safety & performance patches
- **Phase 1** — De-bloat core (split & extract)
- **Phase 2** — Vertical slice of compiled cards
- **Phase 3** — Facts & observations
- **Phase 4** — Tiered retrieval
- **Phase 5** — Project context tree / coding memory

---

## Universal Gates (Every Change)

These gates run on **every subagent output**, regardless of phase.

### G0.1 — Syntax & Import Integrity

```bash
# Ensure no syntax errors, missing imports, or stale bytecode
uv run ruff check . --select E,F   # Syntax + undefined names
uv run python -c "
import ast, sys, pathlib
errors = []
for f in pathlib.Path('src').rglob('*.py'):
    try:
        ast.parse(f.read_text())
    except SyntaxError as e:
        errors.append(f'{f}: {e}')
if errors:
    for e in errors:
        print(e)
    sys.exit(1)
print('All source files parse cleanly')
"
```

**Stop/Go:** Syntax errors → **STOP** (fix before proceeding). All clear → **GO**.

### G0.2 — Type Checking

```bash
# Static type check on changed files (subagent must list touched files)
uv run ruff check . --select ANN   # Annotation checks (if enabled)
# Or run mypy on the changed modules if configured:
# uv run mypy src/dory_core/search.py src/dory_core/retrieval_planner.py
```

**Stop/Go:** Type errors in public signatures or critical internals → **STOP** (fix). Annotation warnings only → **CAUTION** (log but proceed).

### G0.3 — Lint (Ruff Full Check)

```bash
uv run ruff check .
```

**Stop/Go:** Any lint errors → **STOP** (fix). Warnings only → **GO** with warning log.

### G0.4 — Public Safety / Leak Check

```bash
# Scan all touched files for private paths, tokens, secrets
uv run python scripts/release/check-public-safety.py --path <touched-file>...

# Full scan if broad changes
uv run python scripts/release/check-public-safety.py --path src
```

**Stop/Go:** Any leak finding → **STOP** (investigate and fix). Clean → **GO**.

### G0.5 — No Core→CLI Import Regression

```bash
# Verify core never imports CLI
uv run python -c "
import ast, pathlib, sys
errors = []
for f in pathlib.Path('src/dory_core').rglob('*.py'):
    tree = ast.parse(f.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith('dory_cli') or alias.name.startswith('dory_http') or alias.name.startswith('dory_mcp'):
                    errors.append(f'{f}: imports {alias.name}')
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module.startswith('dory_cli') or node.module.startswith('dory_http') or node.module.startswith('dory_mcp')):
                errors.append(f'{f}: from {node.module} import')
if errors:
    for e in errors:
        print(e)
    print('ERROR: dory_core must not import from adapter packages')
    sys.exit(1)
print('OK: dory_core has no adapter imports')
"
```

**Stop/Go:** Any adapter import in core → **STOP**. Clean → **GO**.

---

## Phase 0 Gates — Safety & Performance Patches

Scope:
1. Unknown profile fails closed
2. Active memory returns partial context on timeout
3. Hybrid is not default for interactive Hermes contexts
4. Active memory gets strict internal stage budgets
5. `/v1/get` returns line/hash metadata

### P0.1 — Unknown Profile Fails Closed (Safety Gate)

**Rationale:** `wake(profile="nonexistent")` must not leak personal/core context.

**Verification:**

```bash
# Run the profile validation test
uv run pytest -q tests/unit/test_profiles.py -x

# Integration-level test (if exists):
uv run pytest -q tests/integration/http/test_profiles_http.py -x
```

**Manual/synthetic test to add (if not present):**

```python
# In tests/unit/test_profiles.py or similar
def test_unknown_profile_does_not_leak_core():
    """Unknown profile must not silently fall back to personal/core context."""
    with pytest.raises(ValueError, match="unknown profile") as exc:
        resolve_profile("nonexistent_profile_abc123")
    # OR: the minimal safe profile is returned — verify it has no core/user paths
    profile = resolve_profile_safe("nonexistent_profile_abc123")
    assert "core/user.md" not in profile.allowed_roots
```

**Stop/Go:** Test passes → **GO**. Test fails (leaks context) → **STOP** (critical safety issue). No test for this behavior → **STOP** (add test first).

### P0.2 — Active Memory Partial Context on Timeout

**Rationale:** `dory_active_memory` with 5s budget must return best partial context, not fail hard.

**Verification:**

```bash
# Unit tests
uv run pytest -q tests/unit/test_active_memory.py -x -v

# Integration tests
uv run pytest -q tests/integration/core/test_active_memory_flow.py -x -v
uv run pytest -q tests/integration/http/test_active_memory_http.py -x -v
```

**Key assertions to check:**
- Timeout returns `partial=True` or similar flag
- Response includes whatever was collected before budget expired
- No unhandled exception/timeout propagation to caller

**Stop/Go:** All active-memory tests pass → **GO**. Any test fails → **STOP**.

### P0.3 — Hybrid Not Default for Interactive Hermes

**Rationale:** Telegram/Hermes interactive chat must use BM25 or card-first, not expensive hybrid.

**Verification:**

```bash
# Check the default search mode configuration
uv run python -c "
from dory_core.config import DorySettings
s = DorySettings()
# Assert the default profile for 'casual' or 'hermes' uses bm25 or cards
print(f'Default search mode: {s.default_search_mode}')
assert s.default_search_mode in ('bm25', 'cards', 'auto'), \
    f'Default search mode should not be hybrid, got: {s.default_search_mode}'
"

# Check Hermes provider defaults
grep -n 'search_mode\|mode.*=' plugins/hermes-dory/provider.py | head -20
```

**Stop/Go:** Default is NOT hybrid → **GO**. Default is hybrid → **STOP** (performance regression for interactive use).

### P0.4 — Active Memory Stage Budgets

**Rationale:** Internal stages (0-100ms cards, 100-800ms BM25, 800-2500ms vector, etc.) must be enforced.

**Verification:**

```bash
# Check budget constants exist and are used
uv run python -c "
from dory_core.active_memory import _STAGE_BUDGETS  # or equivalent
assert len(_STAGE_BUDGETS) > 0, 'No stage budgets defined'
total = sum(budget for _, budget in _STAGE_BUDGETS)
print(f'Total stage budget: {total}ms')
assert total <= 5000, f'Total budget exceeds 5000ms: {total}ms'
"
```

**Stop/Go:** Budgets defined, sum ≤ 5s → **GO**. No budgets or sum > 5s → **STOP**.

### P0.5 — /v1/get Returns Line/Hash Metadata

**Verification:**

```bash
uv run pytest -q tests/integration/http/test_get_contract.py -x -v
```

**Stop/Go:** Contract tests pass → **GO**. Failures → **STOP**.

### P0.6 — Phase 0 Regression Suite

```bash
# Full test suite
uv run pytest -q -x
```

**Stop/Go:** All 808+ tests pass → **GO**. Any failure → **STOP** (regression introduced).

---

## Phase 1 Gates — De-Bloat Core

Scope:
1. Extract migration modules from hot core → `src/dory_core/migration/` package
2. Move slug/canonical helpers to neutral core modules
3. Break `dory_core → dory_cli` import
4. Split `search.py` → `search/*.py` package
5. Split Hermes provider → multiple files
6. Create one `DoryRuntime` in core

### P1.1 — Migration Module Extraction

**Verification gates:**

```bash
# 1. Migration modules no longer live at src/dory_core/migration_*.py root
ls src/dory_core/migration_*.py 2>/dev/null && echo "FAIL: migration files still in core root" || echo "PASS: no migration files in core root"

# 2. New package exists
test -d src/dory_core/migration && echo "PASS: migration package exists" || echo "FAIL: migration package missing"

# 3. Public API preserved — existing migration tests still pass
uv run pytest -q tests/unit/test_migration_*.py -x -v
uv run pytest -q tests/integration/core/test_migration_*.py -x -v
uv run pytest -q tests/integration/acceptance/test_memory_schema_migration_acceptance.py -x -v
```

**Import contract verification:**

```bash
# No hot-path module imports from migration package
uv run python -c "
import ast, pathlib
HOT_MODULES = ['search.py', 'active_memory.py', 'wake.py', 'runtime.py', 'semantic_write.py', 'ops.py']
errors = []
for fname in HOT_MODULES:
    f = pathlib.Path('src/dory_core') / fname
    if not f.exists():
        continue
    tree = ast.parse(f.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, 'module', None) or ''
            for alias in node.names:
                if 'migration' in (alias.name + mod):
                    errors.append(f'{f}: imports migration module')
if errors:
    for e in errors:
        print(e)
    print('FAIL: hot path imports migration')
else:
    print('PASS: hot path does not import migration')
"
```

**Stop/Go:**
- Migration files still in core root → **STOP**
- Migration tests fail → **STOP**
- Hot path imports migration → **CAUTION** (log, flag for Phase 1 follow-up)

### P1.2 — Slug/Canonical Helper Cleanup

**Verification:**

```bash
# Check that semantic_write.py imports from neutral module, not migration
uv run python -c "
import ast, pathlib
f = pathlib.Path('src/dory_core/semantic_write.py')
tree = ast.parse(f.read_text())
for node in ast.walk(tree):
    if isinstance(node, ast.ImportFrom):
        if node.module and 'migration' in node.module:
            for alias in node.names:
                print(f'FAIL: semantic_write imports from migration: {alias.name}')
                raise SystemExit(1)
print('PASS: semantic_write does not import from migration')
"

# Verify slug helpers moved to src/dory_core/slug.py
grep -q 'def normalize_' src/dory_core/slug.py && echo "PASS: slug helpers in slug.py" || echo "FAIL: slug helpers missing"
```

**Stop/Go:** semantic_write still imports from migration → **STOP**. Clean → **GO**.

### P1.3 — Core→CLI Import Break

**Verification:**

```bash
# Already covered by G0.5, but specific check:
uv run python -c "
import ast, pathlib
errors = []
for f in pathlib.Path('src/dory_core').rglob('*.py'):
    tree = ast.parse(f.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and ('dory_cli' in node.module or 'dory_http' in node.module or 'dory_mcp' in node.module):
                errors.append(f'{f}: imports {node.module}')
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if any(alias.name.startswith(p) for p in ['dory_cli', 'dory_http', 'dory_mcp']):
                    errors.append(f'{f}: imports {alias.name}')
if errors:
    for e in errors:
        print(e)
    print('FAIL')
else:
    print('PASS: no adapter imports in core')
"
```

**Stop/Go:** Any adapter import in core → **STOP**.

### P1.4 — search.py Split

**Verification gates:**

```bash
# 1. Old monolithic file no longer exists or is a thin shim
test -f src/dory_core/search.py && echo "CHECK: search.py still exists — verify it's a thin re-export shim" || echo "PASS: search.py removed"

# 2. New search/ package exists with expected modules
echo "--- Search package contents ---"
ls src/dory_core/search/ 2>/dev/null || echo "FAIL: search/ package missing"

# 3. All existing search tests pass
uv run pytest -q tests/unit/test_search_*.py -x -v
uv run pytest -q tests/integration/core/test_search_*.py -x -v

# 4. All imports across codebase still resolve (search is public API)
uv run python -c "from dory_core.search import SearchEngine, SearchReq; print('PASS: public search API resolves')"
uv run python -c "from dory_core.types import SearchReq, SearchResult; print('PASS: types still importable')"
```

**Expected split layout:**

```
src/dory_core/search/
  __init__.py        # Public API re-exports (SearchEngine, merge_rankings, _ChunkRow, etc.)
  retrieval.py       # Retrieval execution (moved from original search.py)
  scoring.py         # Scoring logic
  fusion.py          # RRF and ranking fusion
  planner.py         # Query planning (if not already in retrieval_planner.py)
  dedup.py           # Deduplication
  policies.py        # Path policies, privacy logic
```

**Stop/Go:**
- `search/` package missing → **STOP**
- Any search test fails → **STOP**
- Public API import broken → **STOP**

### P1.5 — Hermes Provider Split

**Verification gates:**

```bash
# 1. Old monolithic provider no longer exists or is thin shim
wc -l plugins/hermes-dory/provider.py
# Should be much smaller than 2221 lines

# 2. Expected module files exist
echo "--- Hermes provider contents ---"
ls -la plugins/hermes-dory/

# 3. Hermes config tests pass
uv run pytest -q tests/unit/test_hermes_provider_config.py -x -v

# 4. Hermes provider still loads correctly
uv run python -c "
from plugins.hermes_dory.provider import HermesDoryProvider
print('PASS: HermesDoryProvider loads')
"

# 5. Hermes shim contract tests pass
uv run pytest -q tests/integration/http/test_hermes_shim_contract.py -x -v
```

**Expected layout:**

```
plugins/hermes-dory/
  __init__.py        # Re-exports
  config.py          # Config loading, env parsing
  client.py          # HTTP client behavior, fallback
  schemas.py         # Tool schemas
  tools.py           # Tool handlers
  provider.py        # Provider class (thin, imports from siblings)
```

**Stop/Go:**
- provider.py still >500 lines → **CAUTION** (check if truly split)
- Hermes config test fails → **STOP**
- Provider import fails → **STOP**
- Shim contract test fails → **STOP**

### P1.6 — Unified DoryRuntime

**Verification gates:**

```bash
# 1. DoryRuntime exists in core
grep -q 'class DoryRuntime' src/dory_core/runtime.py && echo "PASS: DoryRuntime class exists" || echo "FAIL: DoryRuntime class missing"

# 2. HTTP uses DoryRuntime (not its own HttpRuntime)
grep -q 'from dory_core.runtime import DoryRuntime' src/dory_http/app.py && echo "PASS: HTTP uses DoryRuntime" || echo "CAUTION: HTTP may still have its own runtime"

# 3. MCP uses DoryRuntime
grep -q 'from dory_core.runtime import DoryRuntime' src/dory_mcp/server.py && echo "PASS: MCP uses DoryRuntime" || echo "CAUTION: MCP may still have its own runtime"

# 4. CLI uses DoryRuntime
grep -q 'from dory_core.runtime import DoryRuntime' src/dory_cli/main.py && echo "PASS: CLI uses DoryRuntime" || echo "CAUTION: CLI may still have its own runtime"

# 5. Integration tests pass (these exercise runtime)
uv run pytest -q tests/integration/http/ -x -v
uv run pytest -q tests/integration/cli/ -x -v
uv run pytest -q tests/integration/mcp/ -x -v
```

**Stop/Go:**
- DoryRuntime class missing → **STOP**
- Any integration test suite fails → **STOP**
- Multiple runtimes still exist → **CAUTION** (flag for Phase 1.6 follow-up)

### P1.7 — Phase 1 Regression Suite

```bash
# Full regression
uv run pytest -q -x --tb=short 2>&1 | tail -20
```

**Stop/Go:** All tests pass → **GO**. Failures → **STOP** (investigate which subagent change caused regression).

---

## Phase 2 Gates — Vertical Slice of Compiled Cards

Scope: project:dory card + RetrievalPlan schema + planner-generated steps + active-memory card-first fallback.

### P2.1 — Card Model & Storage

**Verification:**

```bash
# Card types exist and are importable
uv run python -c "
from dory_core.types import Card, CardLevel, CardPlane
print('PASS: Card types importable')
assert CardLevel.L0.value == 'L0'
assert CardLevel.L1.value == 'L1'
"

# Card storage reads/writes work
uv run pytest -q tests/unit/test_card_storage.py -x -v 2>/dev/null || echo "INFO: test_card_storage.py may not exist yet — add it"
```

**Stop/Go:** Card types missing or tests fail → **STOP**.

### P2.2 — RetrievalPlan Schema & Planner

**Verification:**

```bash
# RetrievalPlan exists
uv run python -c "
from dory_core.retrieval_planner import RetrievalPlan, RetrievalStep, RetrievalPlane
print('PASS: RetrievalPlan types importable')
"

# Planner generates valid plans
uv run pytest -q tests/unit/test_retrieval_planner.py -x -v
uv run pytest -q tests/unit/test_query_planner_toggle.py -x -v
```

**Stop/Go:** Types missing or tests fail → **STOP**.

### P2.3 — Active Memory Card-First Fallback

**Verification:**

```bash
# Active memory uses card retrieval before expensive search
uv run python -c "
from dory_core.active_memory import compose_active_context
# Verify card stage exists and runs first
"

# Existing active memory tests still pass
uv run pytest -q tests/unit/test_active_memory.py -x -v
uv run pytest -q tests/integration/core/test_active_memory_flow.py -x -v
```

**Stop/Go:** Active memory tests fail → **STOP**.

### P2.4 — Phase 2 Regression Suite

```bash
uv run pytest -q -x
```

**Stop/Go:** All tests pass → **GO**.

---

## Phase 3 Gates — Facts & Observations

### P3.1 — Fact Model

**Verification:**

```bash
uv run python -c "
from dory_core.types import Fact, FactKind, FactStatus
print('PASS: Fact types importable')
"
uv run pytest -q tests/unit/test_fact_model.py -x -v 2>/dev/null || echo "INFO: test_fact_model.py may not exist yet — add it"
```

### P3.2 — Observation Model with Evidence

**Verification:**

```bash
uv run python -c "
from dory_core.types import Observation, ObservationTrend, Evidence
print('PASS: Observation types importable')
"
```

### P3.3 — Trend/Freshness Computation

**Verification:**

```bash
uv run pytest -q tests/unit/test_trend_computation.py -x -v 2>/dev/null || echo "INFO: test_trend_computation.py may not exist yet — add it"
```

### P3.4 — Fact/Observation Retrieval

**Verification:**

```bash
# Can retrieve facts/observations before raw search
uv run python -c "
from dory_core.search import SearchEngine
# Verify fact retrieval is a retrieval strategy
"
```

**Stop/Go:** Any Phase 3 test fails → **STOP**.

---

## Phase 4 Gates — Tiered Retrieval

### P4.1 — L0/L1/L2 Summaries

**Verification:**

```bash
# L0 and L1 summaries exist for project/state/core docs
uv run python -c "
from dory_core.types import CardLevel
"
```

### P4.2 — Planner Chooses Depth

**Verification:**

```bash
uv run pytest -q tests/unit/test_retrieval_depth.py -x -v
```

### P4.3 — Deep Vector/Hybrid Only on Explicit Request

**Verification:**

```bash
uv run pytest -q tests/unit/test_search_mode_defaults.py -x -v
```

**Stop/Go:** Deep retrieval used in non-explicit mode → **STOP**.

---

## Phase 5 Gates — Project Context Tree / Coding Memory

### P5.1 — Content-Hash Snapshots

**Verification:**

```bash
uv run pytest -q tests/unit/test_content_hash.py -x -v
```

### P5.2 — Summary Staleness Propagation

**Verification:**

```bash
uv run pytest -q tests/unit/test_staleness.py -x -v
```

### P5.3 — Archive + Ghost Stubs

**Verification:**

```bash
uv run pytest -q tests/unit/test_archive.py -x -v
```

### P5.4 — Optional Nested Git

**Verification:**

```bash
uv run pytest -q tests/unit/test_memory_versioning.py -x -v
```

---

## Cross-Cutting Performance Gates

### PERF.1 — Wake Latency

```bash
# Measure wake latency before and after changes
uv run python -c "
import time
from dory_core.wake import wake
start = time.monotonic()
result = wake(profile='test_profile', budget=1200)
elapsed = (time.monotonic() - start) * 1000
print(f'wake latency: {elapsed:.0f}ms')
assert elapsed < 200, f'wake took {elapsed:.0f}ms, expected <200ms'
"
```

**Gate:** wake should stay under 200ms. If regression >20% from baseline → **CAUTION** (flag).

### PERF.2 — Search Latency by Mode

```bash
# Baseline measurements
uv run python -c "
import time
from dory_core.search import SearchEngine
# Measure BM25, vector, hybrid modes
# Each mode must be under its threshold
"
```

**Expected thresholds (from synthesis):**
- BM25: < 3s
- Vector: < 5s
- Hybrid: < 20s (but only on explicit request)
- Active memory with cards: < 5s

**Gate:** Any mode exceeds 2x its baseline → **CAUTION** (investigate). Active memory > 5s → **STOP**.

### PERF.3 — Import Time

```bash
# Measure cold import time of critical modules
uv run python -c "
import time
start = time.perf_counter()
from dory_core.search import SearchEngine
print(f'search import: {(time.perf_counter()-start)*1000:.1f}ms')
"
```

**Gate:** Import time increase > 2x → **CAUTION**.

---

## Cross-Cutting Integration Gates

### INT.1 — HTTP API Contract Tests

```bash
uv run pytest -q tests/integration/http/ -x -v
```

**Key files:** `test_http_routes.py`, `test_get_contract.py`, `test_schema_parity.py`, `test_hermes_shim_contract.py`, `test_active_memory_http.py`, `test_profiles_http.py`, `test_memory_write_http.py`

**Stop/Go:** Any HTTP test fails → **STOP**.

### INT.2 — MCP Server Contract Tests

```bash
uv run pytest -q tests/integration/mcp/ -x -v
```

**Key files:** `test_tool_schema.py`, `test_get_parity.py`, `test_http_bridge.py`, `test_stdio_server.py`, `test_tcp_server.py`

**Stop/Go:** Any MCP test fails → **STOP**.

### INT.3 — CLI Contract Tests

```bash
uv run pytest -q tests/integration/cli/ -x -v
```

**Key files:** `test_cli_read_path.py`, `test_ops_commands.py`, `test_semantic_write_commands.py`, `test_eval_runner.py`

**Stop/Go:** Any CLI test fails → **STOP**.

### INT.4 — Hermes Provider Contract

```bash
uv run pytest -q tests/unit/test_hermes_provider_config.py -x -v
uv run pytest -q tests/integration/http/test_hermes_shim_contract.py -x -v

# Read-only live check (if Hermes is running)
curl -s http://localhost:8766/v1/health | python -c "import sys,json; d=json.load(sys.stdin); assert d.get('status')=='ok'; print('PASS: Hermes/Dory daemon healthy')"
```

**Stop/Go:** Daemon health check fails → **CAUTION** (not necessarily refactor issue). Contract tests fail → **STOP**.

### INT.5 — OpenClaw Plugin Integrity

```bash
(cd packages/openclaw-dory && npm ci && npm run build)
# Check that the built dist file is valid
node -e "const m = require('./dist/index.js'); console.log('PASS: OpenClaw plugin loads, exports:', Object.keys(m).join(', '))"
```

**Stop/Go:** Build fails or plugin doesn't load → **STOP** (if Phase 5 changes affected it).

---

## Cross-Cutting Code Quality Gates

### Q.1 — File Size Budget

```bash
# Enforce that no file exceeds agreed-upon size limits
find src -name '*.py' -exec sh -c 'lines=$(wc -l < "$1"); if [ "$lines" -gt 500 ]; then echo "OVERSIZED: $1 ($lines lines)"; fi' _ {} \;
```

**Gate:** Any single file >500 lines → **CAUTION** (check if further split needed).
Any file >800 lines → **STOP** (must split further).

### Q.2 — Module Coupling

```bash
# Check that search/* modules don't import each other circularly
uv run python -c "
import ast, pathlib, sys
modules = list(pathlib.Path('src/dory_core/search').glob('*.py'))
# Simple circular import detection
for m in modules:
    if m.name == '__init__.py':
        continue
    tree = ast.parse(m.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and 'search' in node.module:
                peer = node.module.split('.')[-1]
                if peer and peer != m.stem:
                    print(f'{m.name} imports sibling {peer}')
"
```

**Gate:** Sibling imports across search/ modules → **CAUTION** (may indicate wrong split).

### Q.3 — Duplicate Runtime Detection

```bash
# Ensure only one DoryRuntime exists
grep -rn 'class.*Runtime' src/dory_core/runtime.py src/dory_http/ src/dory_mcp/ src/dory_cli/ --include='*.py' | grep -v __pycache__ | grep -v '#'
```

**Gate:** More than one Runtime class → **STOP** (must consolidate).

### Q.4 — Dead Import Detection

```bash
# Check that removed modules/tests aren't imported from obsolete paths
grep -rn 'from dory_core.migration_' src/ --include='*.py' | grep -v __pycache__ | grep -v '/migration/'
```

**Gate:** Any stale import → **STOP** (must update to new package path).

---

## Stop/Go Decision Matrix

| Condition | Action |
|-----------|--------|
| Syntax error in any touched file | **STOP** — fix before proceeding |
| Any lint error (ruff select E/F) | **STOP** — fix before proceeding |
| Public safety leak found | **STOP** — investigate and fix |
| Any existing test fails | **STOP** — regression introduced |
| Phase 0 safety test fails (unknown profile leak) | **STOP** — critical safety issue |
| Phase 0 active memory timeout test fails | **STOP** — budget-first behavior broken |
| Phase 1 migration extraction test fails | **STOP** — public API broken |
| Phase 1 core→CLI import found | **STOP** — dependency direction wrong |
| Phase 1 search/* package missing or imports broken | **STOP** — public API broken |
| Phase 1 Hermes provider import fails | **STOP** — plugin broken |
| File >800 lines (Phase 1+) | **STOP** — must split further |
| Multiple Runtime classes | **STOP** — must consolidate |
| Stale migration import paths | **STOP** — must update |
| Any integration test suite fails | **STOP** — surface contract broken |
| Dead code from removed modules | **STOP** — cleanup needed |
| wake latency >200ms or 2x baseline | **CAUTION** — flag for perf review |
| File 500-800 lines | **CAUTION** — consider further split |
| Sibling imports in search/ | **CAUTION** — review coupling |
| Daemon health check fails | **CAUTION** — may be env, not refactor |
| Import time >2x baseline | **CAUTION** — flag |
| Lint warnings only (no errors) | **GO** — log warnings |

---

## Subagent Execution Workflow

Each subagent must:

1. **Pre-flight**: Run G0.1 (syntax), G0.5 (adapter import check) before making any changes
2. **Edit**: Apply changes per phase spec
3. **Post-edit gates**: Run all applicable gates for the phase:
   - G0.1–G0.5 (universal)
   - Phase-specific gates (P1.1–P1.7, etc.)
   - PERF.1–PERF.3 (performance)
   - INT.1–INT.5 (integration)
   - Q.1–Q.4 (quality)
4. **Full regression**: `uv run pytest -q -x`
5. **Report**: Output a structured summary:
   - Files touched
   - Gates passed/failed
   - Test results
   - Any CAUTION flags
   - Stop/Go recommendation
6. **Handoff**: If GO, pass to next subagent. If STOP, return to parent with detailed failure info.

---

## Concrete Command Reference

### Quick Smoke Test (every change)

```bash
uv sync --frozen --all-groups && uv run ruff check . && uv run pytest -q -x --tb=short
```

### Full CI-Equivalent (before final sign-off)

```bash
# Python checks
uv sync --frozen --all-groups
uv run ruff check .
uv run pytest -q --tb=short

# OpenClaw build
(cd packages/openclaw-dory && npm ci && npm run build)

# Package build
uv build --wheel --sdist

# Docker build (if Docker available)
docker build -t dory:verify .

# Public eval validation
uv run python eval/validate.py

# Public safety scan
uv run python scripts/release/check-public-safety.py \
  --path README.md --path AGENTS.md --path CLAUDE.md \
  --path CONTRIBUTING.md --path CODE_OF_CONDUCT.md \
  --path SECURITY.md --path .github/pull_request_template.md \
  --path .github/ISSUE_TEMPLATE --path eval/README.md \
  --path eval/INDEX.md --path eval/categories.md \
  --path eval/public --path examples/corpus \
  --path docs/agent-integration.md --path docs/evals/README.md \
  --path docs/current-state/README.md \
  --path docs/current-state/operations-and-validation.md \
  --path references/runbook.md --path references/client-runbook.md \
  --path LICENSE --path pyproject.toml

# Built artifact safety scan
uv run python scripts/release/check-public-safety.py --path dist
```

### Git Diff Review (changes vs main)

```bash
git diff main --name-only
git diff main --stat
```

### Hermes/Dory Live Read-Only Check

```bash
# If daemon is running
curl -s http://localhost:8766/v1/health
curl -s 'http://localhost:8766/v1/search?query=test&limit=1'
curl -s http://localhost:8766/v1/status
```

---

*This gate design is aligned with AGENTS.md principles: prefer smallest correct diff, keep generated artifacts intentional, and respect existing package boundaries.*
