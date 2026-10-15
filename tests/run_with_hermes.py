#!/usr/bin/env python3
"""Run the whole test suite with Hermes importable, safely.

    /usr/bin/python3 tests/run_with_hermes.py

Why this exists: some tests (tests/test_install_hermes.py) call Hermes's own Python functions to create and
delete real profiles. A scratch HERMES_HOME alone does NOT make that safe if the code comes from the real
Hermes install: that once rewrote the owner's real launchers. So this runner:

1. refuses to use the real install (anything under ~/.hermes) as the source of Hermes code. It needs a
   separate clone, named by CABINET_HERMES_SRC (for example CABINET_HERMES_SRC=/path/to/hermes-agent-clone);
   without it, the tests that need Hermes are skipped and the rest run;
2. borrows only the interpreter from Hermes's venv (for its installed packages), never its code;
3. starts the tests with HOME and HERMES_HOME pointed at a temporary folder (tests/_load.py does this
   before anything imports Hermes), and never runs a ``hermes`` command;
4. fingerprints the real ~/.hermes/profiles listing, the real launchers and the real config before and after,
   and fails loudly if any of them changed.
"""

from __future__ import annotations

import hashlib
import os
import pwd
import subprocess
import sys
from pathlib import Path

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
REAL_HERMES = REAL_HOME / ".hermes"
DEFAULT_PY = REAL_HERMES / "hermes-agent" / "venv" / "bin" / "python"
HERE = Path(__file__).resolve().parent


def fingerprint() -> dict[str, str]:
    """What must not change: the profile names, the launchers, the config. Reads only; nothing is opened for writing."""
    out: dict[str, str] = {}
    profiles = REAL_HERMES / "profiles"
    out["profiles"] = ",".join(sorted(p.name for p in profiles.iterdir())) if profiles.is_dir() else ""
    for rel in ("config.yaml", "hermes-agent/.hermes/bin/hermes", "hermes-agent/.hermes/bin/hermes-acp"):
        p = REAL_HERMES / rel
        try:
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "absent"
        except OSError:
            out[rel] = "unreadable"
    return out


def main() -> int:
    raw = os.environ.get("CABINET_HERMES_SRC", "").strip()
    src = Path(raw).expanduser().resolve() if raw else None
    py = Path(os.environ.get("CABINET_HERMES_PY") or DEFAULT_PY)
    if src is not None and str(src).startswith(str(REAL_HERMES.resolve())):
        print(f"Refusing: {src} is inside the real ~/.hermes. Use a separate clone of Hermes (see CABINET_HERMES_SRC).")
        return 2
    if src is None:
        print("CABINET_HERMES_SRC is not set. Tests that need Hermes will be skipped; running the rest.")
        env_path = ""
    elif not (src / "hermes_cli" / "profiles.py").is_file():
        print(f"No Hermes clone at {src}. Tests that need Hermes will be skipped; running the rest.")
        env_path = ""
    else:
        env_path = str(src)
    if not py.is_file():
        print(f"No Hermes interpreter at {py}.")
        return 2
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONDONTWRITEBYTECODE": "1", "HERMES_DISABLE_LAZY_INSTALLS": "1",
           "LANG": "en_US.UTF-8"}
    if env_path:
        env["PYTHONPATH"] = env_path
    if os.environ.get("CABINET_E2E_ALL") == "1":  # every Bot and crew through the real gateway (several minutes)
        env["CABINET_E2E_ALL"] = "1"
    before = fingerprint()
    code = subprocess.run([str(py), "-m", "unittest", "discover", "-s", str(HERE), "-p", "test_*.py", *sys.argv[1:]], env=env, cwd=str(HERE)).returncode
    after = fingerprint()
    if before != after:
        changed = [k for k in before if before[k] != after[k]]
        print(f"FAIL: the real ~/.hermes changed during the tests: {changed}")
        return 3
    print("The real ~/.hermes profiles, launchers and config are unchanged.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
