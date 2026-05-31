"""
Pytest configuration for RXinDexer tests.

This file ensures that the electrumx modules can be imported correctly
during test execution.
"""

import sys
import os

import pytest

# Add the project root directory to Python path
# This ensures that electrumx.lib and other modules can be imported
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# test_local.py is a standalone manual script ("Usage: python3 tests/test_local.py")
# whose module body runs checks and calls sys.exit() at import time. It is not a
# pytest module and crashes collection; the same coverage lives in the proper
# pytest test files. Exclude it from collection (still runnable directly).
collect_ignore = ["test_local.py"]


@pytest.fixture(scope="module", autouse=True)
def _preserve_os_environ():
    """Snapshot and restore os.environ around each test *module*.

    Several modules (e.g. test_env.py, test_compaction.py) mutate the global
    os.environ in place — including os.environ.clear() — to exercise config
    parsing. Left unrestored this leaks across modules and makes the suite
    order-dependent: test_env.py would wipe the base env that test_env_base.py
    establishes at import time, so test_env_base.py fails only when it runs
    after test_env.py.

    Scope is per-module, not per-test, on purpose: test_env.py's own tests are
    intentionally not self-contained (helpers like assert_boolean rely on env
    state set by earlier tests in the same module), so a per-test restore would
    break them. Restoring at module boundaries isolates modules from each other
    while preserving intra-module sharing.
    """
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


# Verify that critical modules are available
def pytest_configure(config):
    """Called after command line options have been parsed."""
    try:
        import electrumx.lib
        print("✅ electrumx.lib is available")
    except ImportError as e:
        print(f"❌ electrumx.lib not available: {e}")
        print(f"Project root in path: {project_root}")
        print(f"Current Python path: {sys.path[:3]}...")  # Show first few entries
