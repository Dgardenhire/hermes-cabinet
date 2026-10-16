"""Run a Python script inside an installed Hermes's own runtime (CI only).

Asks the installed ``hermes`` launcher for its runtime command
(``hermes --print-runtime-command``), which is the exact interpreter, flags and
bootstrap Hermes itself starts with, and swaps the module it would run for the
script given here. The script then sees Hermes's code and dependencies exactly
as a plugin does inside Hermes.

    python .github/platforms/hermes_run.py path/to/script.py [args...]

Used by .github/workflows/platforms.yml on throwaway GitHub runners. It is never
run on a developer's machine.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: hermes_run.py script.py [args...]", file=sys.stderr)
        return 2
    script = str(Path(sys.argv[1]).resolve())
    hermes = shutil.which("hermes")
    if not hermes:
        print("hermes is not on PATH", file=sys.stderr)
        return 2
    out = subprocess.run([hermes, "--print-runtime-command", "--module", "hermes_cli.main"],
                         capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        print(out.stdout, out.stderr, sep="\n", file=sys.stderr)
        return out.returncode or 1
    line = [x for x in out.stdout.splitlines() if x.strip().startswith("[")][-1]
    cmd = json.loads(line)
    code_at = cmd.index("-c") + 1
    code, n = re.subn(r"runpy\.run_module\([^)]*\)\s*$",
                      f"runpy.run_path({script!r}, run_name='__main__')", cmd[code_at])
    if n != 1:
        print(f"unexpected runtime command: {cmd!r}", file=sys.stderr)
        return 2
    cmd[code_at] = code
    full = cmd[:code_at + 1] + [script, *sys.argv[2:]]
    print(f"hermes runtime: {cmd[0]}", flush=True)
    return subprocess.run(full).returncode


if __name__ == "__main__":
    raise SystemExit(main())
