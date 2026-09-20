"""Import-cycle guard: each entry point must import in a fresh process.

Refactors (improvement plan Phase 1) must not introduce module-level import
cycles. Running each import in a subprocess makes failures deterministic
instead of depending on the test session's import order.
"""

import subprocess
import sys

import pytest

ENTRY_POINTS = [
    "micronote.app",
    "micronote.tasks",
    "micronote.worker",
]


@pytest.mark.parametrize("module", ENTRY_POINTS)
def test_entry_point_imports_in_fresh_process(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_app_import_does_not_pull_in_worker():
    """The web process must not import the worker (it runs separately)."""
    script = "import sys; import micronote.app; assert 'micronote.worker' not in sys.modules, 'app imported worker'"
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
