"""Exercise the INSTALLED Cabinet against a real Hermes on a CI runner.

Run inside Hermes's own runtime (.github/platforms/hermes_run.py) with HERMES_HOME
pointing at a scratch folder the plugin was installed into. It:

1. lets Hermes's own plugin manager discover and load the installed plugin;
2. plans one Bot, creates it through Hermes's real ``profiles.create`` gateway handler
   (the call the page makes), confirms it, checks Hermes loads the planned instructions,
   then removes it through Cabinet and checks Hermes no longer has it;
3. plans one crew, creates every member through the same handler, confirms the crew,
   then removes the crew;
4. checks every file either side wrote stayed inside the scratch HERMES_HOME.

Exits non-zero on the first failure. No model is called.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import platform
import sys
from pathlib import Path

PLUGIN = "bot-cabinet-v2"
BOT = "scout"
CREW = "personal-planning-desk"


def say(*parts):
    print("[cabinet]", *parts, flush=True)


def check(cond, what):
    if not cond:
        say("FAIL:", what)
        raise SystemExit(1)
    say("ok:", what)


def main() -> int:
    home = Path(os.environ["HERMES_HOME"]).resolve()
    say("platform", platform.platform(), "python", sys.version.split()[0])
    say("HERMES_HOME", home)
    # Bots inherit the owner's model, as New Agent's do; the scratch home has none, so give it one.
    cfg = home / "config.yaml"
    if "model:" not in (cfg.read_text(encoding="utf-8") if cfg.exists() else ""):
        with cfg.open("a", encoding="utf-8") as fh:
            fh.write("\nmodel:\n  provider: openrouter\n  default: example/ci-model\n")

    from hermes_cli import plugins as hp

    hp.discover_plugins(force=True)
    manager = hp.get_plugin_manager()
    listed = {p.get("name") or p.get("key"): p for p in manager.list_plugins()}
    say("plugins Hermes sees:", json.dumps({k: {x: v.get(x) for x in ("enabled", "error", "version")} for k, v in listed.items()
                                             if "cabinet" in str(k)}, default=str))
    loaded = next((lp for key, lp in manager._plugins.items() if PLUGIN in (key, getattr(lp.manifest, "name", None))), None)
    check(loaded is not None, "Hermes discovered the installed plugin")
    check(loaded.enabled and not loaded.error, f"Hermes loaded it enabled with no error (error={loaded.error!r})")
    check(loaded.module is not None, "Hermes imported the plugin module")
    plugin_dir = Path(loaded.module.__file__).resolve().parent
    check(str(plugin_dir).startswith(str(home)), f"the loaded copy is the one installed under HERMES_HOME ({plugin_dir})")
    pkg = loaded.module.__name__
    say("plugin package:", pkg, "at", plugin_dir)

    installer = importlib.import_module(f"{pkg}.installer")
    hermes = importlib.import_module(f"{pkg}.hermes_bridge")
    catalog = importlib.import_module(f"{pkg}.catalog")
    paths = importlib.import_module(f"{pkg}.paths")
    importlib.import_module(f"{pkg}.dashboard.plugin_api")
    say("ok: the dashboard routes module imports in Hermes's runtime")
    check(hermes.available(), "Cabinet reaches Hermes's own profile functions")
    root = hermes.profiles_root().resolve()
    check(str(root).startswith(str(home)), f"profiles go under the scratch home ({root})")
    data = paths.data_dir().resolve()
    check(str(data).startswith(str(home)), f"Cabinet's own data goes under the scratch home ({data})")
    say("display of a profile path:", paths.display(root / "x"))

    gateway = importlib.import_module("tui_gateway.server")
    prompt_builder = importlib.import_module("agent.prompt_builder")
    profiles = importlib.import_module("hermes_cli.profiles")

    def real_create(request):
        method = gateway._methods[request["method"]]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            reply = method("cabinet-ci", request["params"])
        check("error" not in reply, f"Hermes accepted {request['method']} for {request['params'].get('name')} ({reply.get('error')})")
        return reply["result"]

    # --- one Bot: plan, add, remove -------------------------------------------------------------
    check(any(b["slug"] == BOT for b in catalog.bots()), f"the catalog has {BOT}")
    plan = installer.plan_bot(BOT)
    check(plan["ready"], f"plan for {BOT} is ready ({plan.get('problems')})")
    say("planned profile:", plan["profile"])
    result = real_create(plan["create"])
    check(result.get("soul_written") is not False, "Hermes wrote the instructions")
    out = installer.confirm(plan["planId"])
    check(out["install"]["status"] == "ready", f"Cabinet recorded the Bot (status={out['install']['status']}, note={out.get('note')!r})")
    directory = profiles.get_profile_dir(plan["profile"])
    check(directory.is_dir() and str(directory.resolve()).startswith(str(home)), f"the profile exists under the scratch home ({directory})")
    loaded_soul = prompt_builder.load_soul_md(home_override=directory)
    check(loaded_soul == plan["soul"].strip(), "Hermes's SOUL.md loader loads exactly the planned instructions")
    mine = installer.my_bots()
    say("My Bots after add:", json.dumps(mine, default=str)[:600])
    removed = installer.remove("bot", out["install"]["id"], True)
    check(removed["result"] == "removed", f"Cabinet removed the Bot (result={removed['result']})")
    check(not directory.exists(), "the profile is gone from Hermes")
    check(plan["profile"] not in hermes.existing_profiles(), "Hermes no longer lists it")

    # --- one crew: plan, set up, remove ---------------------------------------------------------
    check(any(c["slug"] == CREW for c in catalog.crews()), f"the catalog has {CREW}")
    cplan = installer.plan_crew(CREW)
    check(cplan["ready"], f"crew plan for {CREW} is ready ({cplan.get('problems')})")
    say("crew members:", [m["profile"] for m in cplan["members"]])
    made = []
    for m in cplan["members"]:
        real_create(m["create"])
        made.append(m["profile"])
        d = profiles.get_profile_dir(m["profile"])
        check(prompt_builder.load_soul_md(home_override=d) == m["soul"].strip(), f"{m['profile']} loads its planned instructions")
    cout = installer.confirm_crew(cplan["planId"], made)
    check(cout.get("ok"), f"Cabinet recorded the crew ({cout})")
    crem = installer.remove("crew", cout["crew"]["id"], True)
    check(sorted(crem["removed"]) == sorted(made), f"Cabinet removed every crew member ({crem['removed']} vs {made})")
    for name in made:
        check(not profiles.get_profile_dir(name).exists(), f"{name} is gone")

    say("ALL CHECKS PASSED on", platform.system())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
