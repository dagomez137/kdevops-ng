# SPDX-License-Identifier: copyleft-next-0.3.1
"""Every step module under `f/` must import.

A step's body only ever runs inside Windmill, so a name that no longer exists in
a shared module is invisible to Ruff (which cannot see across modules) and to
the fixture tests (which import the few modules they exercise). It surfaces as
an ImportError in a job log, after a flow has already started and a guest has
already been touched. Importing every module here costs milliseconds and turns
that into a gate failure.

The one thing that cannot be checked here is a module reaching `wmill`,
Windmill's own client, which is injected into the execution environment and is
not in this checkout; that import is skipped by name, and nothing else is.
"""

import importlib
from pathlib import Path

import pytest

F = Path("f")


def _step_modules() -> list[str]:
    # A directory like f/qsu/qemu-system is not a Python identifier, but
    # import_module takes the dotted string as written, which is how the steps
    # themselves reach it.
    return [
        str(path.with_suffix("")).replace("/", ".")
        for path in sorted(F.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


@pytest.mark.parametrize("module", _step_modules())
def test_every_step_module_imports(module):
    try:
        importlib.import_module(module)
    except ModuleNotFoundError as exc:
        # Only the injected client is allowed to be absent. A name that a
        # shared module no longer exports raises ImportError, not this, so it
        # still fails.
        if exc.name == "wmill":
            pytest.skip("imports Windmill's injected wmill client")
        raise
