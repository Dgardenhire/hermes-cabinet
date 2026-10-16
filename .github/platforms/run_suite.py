"""Run the whole test suite with Hermes importable, the way tests/run_with_hermes.py does on a developer's machine (CI only).

    CABINET_HERMES_SRC=/path/to/separate/hermes-clone python .github/platforms/hermes_run.py .github/platforms/run_suite.py

The repo's rule: tests that call Hermes's own functions import Hermes from a SEPARATE clone, never from the installed
copy, and keep their temporary files out of the Hermes home. Hermes's runtime starts with the installed checkout on
sys.path and its temporary folder inside the Hermes home, so this script swaps in the separate clone (same commit as
the install) and a temporary folder outside it before any test imports Hermes. Only the interpreter and installed
packages are borrowed, as run_with_hermes.py borrows them.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parents[2] / "tests"
src = Path(os.environ["CABINET_HERMES_SRC"]).resolve()
assert (src / "hermes_cli" / "profiles.py").is_file(), f"no Hermes clone at {src}"
installed = Path(sys.modules["hermes_bootstrap"].__file__).resolve().parent if "hermes_bootstrap" in sys.modules else None
assert installed is None or installed != src, "the clone must not be the installed copy"
sys.path[:] = [p for p in sys.path if not (installed and Path(p or ".").resolve() == installed)]
for name in [m for m in sys.modules if m.split(".")[0] in {"hermes_cli", "hermes_constants", "agent", "tools", "tui_gateway",
                                                            "gateway", "plugins", "hermes_state", "utils", "cron"}]:
    del sys.modules[name]
sys.path.insert(0, str(src))
scratch = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()).resolve() / "cabinet-suite-tmp"
scratch.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(scratch)
for var in ("TMPDIR", "TEMP", "TMP"):
    os.environ[var] = str(scratch)
print(f"Hermes code from {src}; installed copy {installed} kept off the path; temporary files in {scratch}", flush=True)

os.chdir(TESTS)
sys.path.insert(0, str(TESTS))
suite = unittest.defaultTestLoader.discover(str(TESTS), pattern="test_*.py", top_level_dir=str(TESTS))
result = unittest.TextTestRunner(verbosity=1).run(suite)
print(f"ran {result.testsRun}, failures {len(result.failures)}, errors {len(result.errors)}, skipped {len(result.skipped)}")
for test, reason in result.skipped:
    print(f"skipped: {test.id()}: {reason}")
raise SystemExit(0 if result.wasSuccessful() else 1)
