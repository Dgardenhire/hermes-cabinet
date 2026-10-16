#!/usr/bin/env python3
"""Serve the repo for the harness, with /api/* answered by the real backend (api.py).

    /usr/bin/python3 harness/serve.py [port]       then open http://127.0.0.1:<port>/harness/

Standard library only. The /api routes mirror dashboard/plugin_api.py exactly.

The harness is a sandbox. Like the tests, it points HERMES_HOME, HOME and Cabinet's data folder at a temporary
folder (tests/_load.py), so adding a bot here makes a real profile folder in that temporary home and never in
~/.hermes. A few harness-only routes under /__h/ stand in for what Hermes and the website do:

- POST /__h/reset                  empty the sandbox (profiles, Cabinet's record, the catalog in use)
- POST /__h/seed   {"what": ...}   set up a scene: "taken" (profiles you already have), "mybots" (things Cabinet set up),
                                   "hermes-bots" (profiles Hermes's own Bots catalog added, with its bot-blueprint.yaml)
- POST /__h/live   {"on": bool}    pretend botcabinet.com publishes a newer catalog (default: it answers 404, as it does today)
- POST /__h/handover {"on": bool}  pretend this Hermes ships its own Bots catalog, on the Python side. The page's own
                                   signal is the gateway answering bots.catalog, which harness/sdk.js fakes for handover=1
- POST /__h/gateway {method, params}  Hermes's profiles.create, done to the sandbox

The sandbox is laid out like a real install (CABINET_SANDBOX_LAYOUT=home, see tests/_load.py): Hermes's home is
<sandbox>/home/.hermes, so the page shows ~/.hermes/profiles/<name> as it does on a real machine.

Kanban: a small in-memory board stands in for hermes_cli.kanban_db, so setting up a crew can make its cards here.
It keeps the cards Cabinet makes (blocked, chained, assigned) until the next reset.

No request leaves the machine by default: the catalog fetch is replaced here. CABINET_HARNESS_CATALOG=live lets the
real fetch reach https://botcabinet.com/catalog.json instead, so a screenshot run can show the published catalog.
"""

from __future__ import annotations

import contextlib
import copy
import functools
import http.server
import json
import os
import shutil
import sys
import types
from pathlib import Path

os.environ.setdefault("CABINET_SANDBOX_LAYOUT", "home")  # before _load builds the sandbox

ROOT = Path(__file__).resolve().parents[1]


def _install_kanban_stub():
    """An in-memory stand-in for Hermes's Kanban board (hermes_cli.kanban_db), with the calls Cabinet makes."""
    if "hermes_cli.kanban_db" in sys.modules:
        return sys.modules["hermes_cli.kanban_db"]
    pkg = sys.modules.get("hermes_cli")
    if pkg is None:
        pkg = types.ModuleType("hermes_cli")
        pkg.__path__ = []  # a package with nothing else in it: hermes_cli.profiles and friends still fail to import
        sys.modules["hermes_cli"] = pkg
    kb = types.ModuleType("hermes_cli.kanban_db")
    kb.BOARD = []

    def init_db():
        return None

    @contextlib.contextmanager
    def connect_closing():
        yield kb.BOARD

    def create_task(conn, *, title, body, assignee=None, created_by=None, parents=(), initial_status="todo", idempotency_key=None):
        for card in conn:
            if idempotency_key and card["key"] == idempotency_key:
                return card["id"]
        cid = f"t_{len(conn) + 1:08x}"
        conn.append({"id": cid, "title": title, "body": body, "assignee": assignee, "by": created_by, "parents": list(parents),
                     "status": initial_status, "key": idempotency_key})
        return cid

    def archive_task(conn, cid):
        for card in conn:
            if card["id"] == cid and card["status"] != "archived":
                card["status"] = "archived"
                return True
        return False

    kb.init_db, kb.connect_closing, kb.create_task, kb.archive_task = init_db, connect_closing, create_task, archive_task
    sys.modules["hermes_cli.kanban_db"] = kb
    pkg.kanban_db = kb
    return kb


KANBAN = _install_kanban_stub()
sys.path.insert(0, str(ROOT / "tests"))
from _hermes_fake import fake_delete, gateway_create, hermes_bots_install, make_foreign_profile, wipe_profiles  # noqa: E402
from _load import sub  # noqa: E402

api = sub("api")
catalog = sub("catalog")
hermes = sub("hermes_bridge")
installer = sub("installer")
paths = sub("paths")
schema = sub("catalog_schema")
sync = sub("sync")

hermes.delete_profile = fake_delete  # the sandbox's stand-in for Hermes's delete

STATE = {"live": False, "handover": False}
_real_detect = hermes.detect_core_catalog
_real_fetch = sync.fetch
REAL_CATALOG = os.environ.get("CABINET_HARNESS_CATALOG") == "live"


def harness_fetch(open_fn=None, url=sync.URL, timeout=sync.TIMEOUT):
    """The website, as the harness plays it: a 404 until /__h/live turns a newer catalog on, or the real site."""
    if REAL_CATALOG:
        return _real_fetch() if open_fn is None else _real_fetch(open_fn, url, timeout)
    if not STATE["live"]:
        raise sync.SyncError("botcabinet.com hasn't published its catalog file yet")
    doc = copy.deepcopy(catalog.bundled())
    doc["generatedAt"] = "2026-10-04"
    doc["siteCommit"] = "c0ffee1 2026-10-04"
    doc["bots"][0] = dict(doc["bots"][0], summary="Finds timely topics, useful developments, and promising opportunities in the areas you choose, now with a source for every item.")
    doc["bots"].append({
        "slug": "minutes-taker", "name": "Minutes Taker", "title": "Meeting notes and follow-ups", "category": "planning",
        "summary": "Turns your meeting notes into clear minutes, decisions and follow-up tasks.", "audience": "People who run recurring meetings",
        "outcome": "Turn the notes I provide into short minutes with decisions and owners",
        "inputs": ["Your meeting notes or a transcript", "Who attended"], "outputs": ["Short minutes", "A list of decisions", "Follow-up tasks with owners"],
        "examples": ["Turn these notes into minutes."], "firstMission": "Make minutes from one fictional meeting and check every decision against the notes.",
        "approvals": ["Ask before sending minutes to anyone"], "offLimits": ["Do not invent decisions that are not in the notes"], "worksWith": ["planner"],
        "role": "You are Minutes Taker. Turn only the notes the owner provides into short minutes. List decisions and follow-ups with owners. "
                "Never invent a decision. Ask before sending minutes to anyone.",
        "skills": [{"name": "Minutes Taker core Skill", "whenToUse": "When I paste meeting notes.", "steps": ["Confirm the notes.", "Draft the minutes.", "Mark anything unclear."]}],
        "routine": {"trigger": "Run when I provide notes.", "activation": "manual-test-required"}, "riskLevel": "Moderate", "credits": [],
        "href": "https://botcabinet.com/bots/minutes-taker/", "test": {"status": "not-tested", "line": "Import checked; a full task run is still pending"},
    })
    return schema.validate_catalog(doc)


sync.fetch = harness_fetch


def reset():
    wipe_profiles()
    data = paths.data_dir()
    for child in list(data.iterdir()):
        shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink()
    catalog.set_active(None)
    sync._STATE.update({"source": None, "updatedAt": None, "checkedAt": None, "error": None})
    STATE.update({"live": False, "handover": False})
    hermes.detect_core_catalog = _real_detect
    KANBAN.BOARD.clear()


def add_bot(slug, **edits):
    plan = installer.plan_bot(slug, edits or None)
    gateway_create(plan["create"]["params"])
    return installer.confirm(plan["planId"])


def seed(what):
    if what == "taken":
        make_foreign_profile("scout", "You are my own scout. Find me three ideas every morning.\n")
        make_foreign_profile("planner", "You are my planner. Keep my week in order.\n")
    elif what == "mybots":
        make_foreign_profile("planner", "You are my planner. Keep my week in order.\n")
        add_bot("daily-newspaper")
        changed = add_bot("scout")
        (hermes.profiles_root() / "scout" / "SOUL.md").write_text("You are Scout. I rewrote this to focus on grants.\n")
        plan = installer.plan_crew("personal-planning-desk")
        made = []
        for m in plan["members"]:
            if m["mode"] == "create":
                gateway_create(m["create"]["params"])
                made.append(m["profile"])
        installer.confirm_crew(plan["planId"], made)
        installer.save_kit("Lookout", {"botName": "Lookout", "jobOutcome": "Watch three pricing pages and summarise what changed",
                                       "inputsContext": "Three links", "outputsDeliverables": "A short weekly summary", "cadenceTrigger": "When I ask",
                                       "toolsIntegrations": "Web pages only", "approvalBoundaries": "Before contacting anyone",
                                       "firstRunTest": "Summarise one page", "prohibitedUncertainty": "Say what is missing", "continuityMemory": "Nothing"},
                           "Every Monday, summarize what changed on three competitors' pricing pages")
        return changed
    elif what == "hermes-bots":
        hermes_bots_install("planner", "weekly-planner", "You are Weekly Planner, from Hermes's own Bots catalog.\n")
        hermes_bots_install("research-analyst", "research-analyst", "You are Research Analyst, from Hermes's own Bots catalog.\n")
    return None


def route(method: str, path: str, body):
    parts = [p for p in path.split("?")[0].split("/") if p]
    if parts and parts[0] == "__h":
        return harness_route(method, parts[1:], body)
    parts = parts[1:]  # drop "api"
    n = len(parts)
    if method == "GET" and parts == ["fit"]:
        return api.fit_form()
    if method == "POST" and parts == ["fit"]:
        return api.fit_answer(body)
    if method == "GET" and n == 2 and parts[0] == "crew":
        return api.crew_plan(parts[1])
    if method == "POST" and parts == ["lab", "start"]:
        return api.lab_start(body)
    if method == "POST" and parts == ["lab", "plan"]:
        return api.lab_plan(body)
    if method == "GET" and parts == ["catalog"]:
        return api.catalog_view()
    if method == "POST" and parts == ["catalog", "refresh"]:
        return api.catalog_refresh()
    if method == "POST" and n == 3 and parts[0] == "bots" and parts[2] == "plan":
        return api.bot_plan(parts[1], body)
    if method == "POST" and parts == ["install", "confirm"]:
        return api.install_confirm(body)
    if method == "POST" and n == 3 and parts[0] == "crews" and parts[2] == "setup":
        return api.crew_setup(parts[1], body)
    if method == "POST" and parts == ["crews", "confirm"]:
        return api.crew_confirm(body)
    if method == "GET" and n == 3 and parts[0] == "crews" and parts[2] == "cards":
        return api.crew_cards_plan(int(parts[1]))
    if method == "POST" and n == 3 and parts[0] == "crews" and parts[2] == "cards":
        return api.crew_cards_create(int(parts[1]), body)
    if method == "POST" and parts == ["kits"]:
        return api.kit_save(body)
    if method == "POST" and n == 3 and parts[0] == "kits" and parts[2] == "plan":
        return api.kit_plan(int(parts[1]), body)
    if method == "GET" and parts == ["mybots"]:
        return api.my_bots()
    if method == "GET" and n == 3 and parts[0] == "remove":
        return api.remove_preview(parts[1], int(parts[2]))
    if method == "POST" and n == 3 and parts[0] == "remove":
        return api.remove(parts[1], int(parts[2]), body)
    if method == "POST" and n == 2 and parts[0] == "forget":
        return api.forget(int(parts[1]))
    raise api.ApiError(404, "Not Found")


def harness_route(method, parts, body):
    if parts == ["reset"]:
        reset()
        return {"ok": True}
    if parts == ["seed"]:
        seed((body or {}).get("what"))
        return {"ok": True}
    if parts == ["live"]:
        STATE["live"] = bool((body or {}).get("on"))
        return {"ok": True}
    if parts == ["handover"]:
        on = bool((body or {}).get("on"))
        hermes.detect_core_catalog = (lambda: {"available": True, "via": "hermes_cli.bot_catalog", "detail": "This Hermes ships its own Bots catalog.",
                                               "names": ["scout", "planner", "research-analyst", "inbox-triage"]}) if on else _real_detect
        return {"ok": True}
    if parts == ["gateway"]:
        params = (body or {}).get("params") or {}
        if (body or {}).get("method") != "profiles.create":
            raise api.ApiError(404, "Hermes gateway unavailable")
        try:
            path = gateway_create(params)
        except FileExistsError as exc:
            raise api.ApiError(409, str(exc))
        return {"ok": True, "name": params["name"], "path": str(path), "soul_written": True}
    raise api.ApiError(404, "Not Found")


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _api(self, method):
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        try:
            status, payload = 200, route(method, self.path, body)
        except api.ApiError as exc:
            status, payload = exc.status_code, {"detail": exc.detail}
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        self.send_header("cache-control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path.startswith("/api/") or self.path.startswith("/__h/"):
            return self._api("GET")
        return super().do_GET()

    def do_POST(self):
        return self._api("POST")


def make_server(port: int = 0) -> http.server.ThreadingHTTPServer:
    return http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Handler, directory=str(ROOT)))


if __name__ == "__main__":
    server = make_server(int(sys.argv[1]) if len(sys.argv) > 1 else 8893)
    print(f"http://127.0.0.1:{server.server_address[1]}/harness/")
    server.serve_forever()
