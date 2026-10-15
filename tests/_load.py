"""Import the plugin the way Hermes does, with no Hermes install.

Hermes imports a directory plugin as a package with ``submodule_search_locations`` set to the plugin
folder. The repo folder is ``bot-cabinet-v2``, which isn't an importable name, so tests load it as
``bot_cabinet_v2``. Every test module does ``from _load import sub``.
"""

from __future__ import annotations

import atexit
import importlib
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bot_cabinet_v2"

# Isolation. Every test run gets its own Hermes home and its own Cabinet data folder, set before the plugin
# is imported, so no test can read or write the user's real ~/.hermes. tests/test_isolation.py proves it.
SANDBOX = Path(tempfile.mkdtemp(prefix="cabinet-test-"))
os.environ["HERMES_HOME"] = str(SANDBOX / "hermes-home")
os.environ["CABINET_DATA_DIR"] = str(SANDBOX / "cabinet-data")
os.environ["HOME"] = str(SANDBOX / "home")
(SANDBOX / "home").mkdir(parents=True, exist_ok=True)
(SANDBOX / "hermes-home" / "profiles").mkdir(parents=True, exist_ok=True)
atexit.register(shutil.rmtree, SANDBOX, ignore_errors=True)


def _load():
    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]
    spec = importlib.util.spec_from_file_location(PACKAGE, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    return module


pkg = _load()


def sub(name: str):
    return importlib.import_module(f"{PACKAGE}.{name}")
