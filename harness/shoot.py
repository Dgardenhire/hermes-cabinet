#!/usr/bin/env python3
"""Screenshot Cabinet's screens with headless Chrome, ONE AT A TIME (the machine is short on memory).
Needs network for React from esm.sh. The backend is the real api.py, served by harness/serve.py.

With Python Playwright installed, each page is shot after the harness says it is ready (it sets
data-ready="1" once the scene is seeded and every click has run), using the installed Google Chrome.
Without it, plain headless Chrome is used with a time budget, which can catch a page before it renders
when a scene takes long to seed; those frames are retried and reported as blank.

    /usr/bin/python3 harness/shoot.py [names...]        (default: every screen, into harness/shots/)
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from serve import ROOT, make_server  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
TRIP = "result:Plan a three-day trip to Santa Fe for four people under $1500"
SCENARIOS = {
    "01-start": ("view=start", 1400, 1700),
    "02-start-light": ("view=start&theme=light", 1400, 1700),
    "03-cabinet": ("view=cabinet", 1400, 2100),
    "04-cabinet-tested": ("view=cabinet&status=tested", 1400, 1100),
    "05-cabinet-empty": ("view=cabinet&q=astronaut", 1400, 800),
    "06-bot-daily-newspaper": ("view=bot&id=daily-newspaper", 1400, 2000),
    "07-bot-scout-light": ("view=bot&id=scout&theme=light", 1400, 2000),
    "08-fit": ("view=fit&job=client-proposal", 1400, 1400),
    "09-answer-crew": (f"view=fit&set={TRIP},frequency:once,needsContinuingContext:false,needsMultipleSpecialists:true,"
                       "workProvenManually:false,overlapsExistingRole:no&click=Find the right setup", 1400, 1700),
    "10-answer-bot": ("view=fit&job=personal-morning-newspaper&click=Find the right setup", 1400, 1500),
    "11-lab": ("view=lab", 1100, 1000),
    "12-lab-plan": ("view=lab&lab=Every Monday, summarize what changed on three competitors' pricing pages", 1100, 2600),
    "13-crews": ("view=crews", 1400, 2100),
    "14-crew-detail": ("view=crew&id=personal-planning-desk", 1400, 1500),
    "15-crew-failed-light": ("view=crew&id=publishing-desk&theme=light", 1400, 1500),
    "16-records": ("view=records", 1400, 3000),
    "17-records-light": ("view=records&theme=light", 1400, 3000),
    "18-narrow": ("view=cabinet", 820, 1600),
    "19-offline": ("view=fit&state=offline", 1200, 800),
    "20-loading": ("view=fit&state=loading", 1400, 900),
    # Adding a Bot, setting up a crew, My Bots, live sync and the hand-over notice. The harness backend is a sandbox:
    # profiles made here are folders in a temporary home, never in ~/.hermes.
    "21-add-bot": ("view=add&id=daily-newspaper", 1400, 1700),
    "22-add-name-taken": ("view=add&id=scout&seed=taken", 1400, 1700),
    "23-add-name-conflict": ("view=add&id=scout&seed=taken&edit=Profile name=scout", 1400, 1700),
    "24-add-done": ("view=add&id=scout&click=Add Scout", 1400, 1200),
    "25-add-light": ("view=add&id=planner&theme=light", 1400, 1700),
    "26-add-narrow": ("view=add&id=scout", 820, 2400),
    "27-setup-crew": ("view=setup&id=personal-planning-desk", 1400, 2000),
    "28-setup-crew-existing": ("view=setup&id=personal-planning-desk&seed=taken", 1400, 2800),
    "29-setup-crew-done": ("view=setup&id=personal-planning-desk&click=Set up Personal Planning Desk", 1400, 1500),
    "30-mybots-empty": ("view=mybots", 1400, 800),
    "31-mybots": ("view=mybots&seed=mybots", 1400, 2200),
    "32-mybots-remove": ("view=mybots&seed=mybots&click=Remove", 1400, 2500),
    "33-mybots-changed": ("view=mybots&seed=mybots&click=Why it stays", 1400, 2500),
    "34-cabinet-live": ("view=cabinet&live=1", 1400, 2200),
    "35-handover": ("view=add&id=scout&handover=1", 1400, 1700),
    "36-lab-created": ("view=lab&lab=Every Monday, summarize what changed on three competitors' pricing pages&keep=name=Lookout&click=Create Lookout&click2=Save as a kit", 1100, 3000),
    "37-mybots-light": ("view=mybots&seed=mybots&theme=light", 1400, 2200),
    "38-narrow-crew": ("view=crew&id=personal-planning-desk", 640, 1900),
    "39-start-handover": ("view=start&handover=1", 1400, 1900),
    "40-mybots-narrow": ("view=mybots&seed=mybots", 640, 2400),
    "41-setup-failed-crew": ("view=setup&id=publishing-desk&theme=light", 1400, 1400),
    "42-answer-crew-nomatch": ("view=fit&set=result:zzqx vlorp,frequency:once,needsContinuingContext:false,needsMultipleSpecialists:true,"
                               "workProvenManually:false,overlapsExistingRole:no&click=Find the right setup", 1400, 1300),
    "43-mybots-crew-cards": ("view=mybots&seed=mybots&click=Make Kanban cards", 1400, 2200),
    # Hermes's own Bots catalog (NousResearch/hermes-agent#119260): the gateway answers bots.catalog (handover=1) and
    # carries scout and planner; seed=hermes-bots puts planner and research-analyst in as Hermes's catalog installs them.
    "44-cabinet-handover": ("view=cabinet&handover=1&seed=hermes-bots", 1400, 2200),
    "45-add-not-carried": ("view=add&id=daily-newspaper&handover=1", 1400, 1800),
    "46-add-already-added": ("view=add&id=planner&seed=hermes-bots", 1400, 1000),
    "47-mybots-hermes-bots": ("view=mybots&seed=mybots,hermes-bots", 1400, 2500),
    "48-bot-scout-handover": ("view=bot&id=scout&handover=1", 1400, 2000),
    "49-add-handover-light": ("view=add&id=scout&handover=1&theme=light", 1400, 1000),
    # Added Oct 15 for the manual: the Daily Newspaper add finished, and a crew with its Kanban cards made on the harness board.
    "50-add-done-newspaper": ("view=add&id=daily-newspaper&click=Add Daily Newspaper", 1400, 1200),
    "51-setup-crew-cards": ("view=setup&id=personal-planning-desk&click=Set up Personal Planning Desk&tick=Yes, make these"
                            "&click2=Make 4 Kanban cards", 1400, 1500),
}


BLANK_BYTES = 30000


def shoot_once(port, query, w, h, target):
    target.unlink(missing_ok=True)
    profile = tempfile.mkdtemp(prefix="cabinet-shot-")
    try:
        cmd = [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--disable-extensions", "--force-prefers-reduced-motion",
               "--renderer-process-limit=1", "--js-flags=--max-old-space-size=256", f"--user-data-dir={profile}",
               f"--window-size={w},{h}", "--virtual-time-budget=40000", "--timeout=20000", f"--screenshot={target}",
               f"http://127.0.0.1:{port}/harness/index.html?{query}"]
        # Chrome writes the PNG when the virtual time budget ends but can linger afterwards, so
        # wait for the file to settle, then stop it. One Chrome at a time.
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline, last = time.time() + 90, -1
        while time.time() < deadline and proc.poll() is None:
            time.sleep(1)
            size = target.stat().st_size if target.exists() else -1
            if size > 0 and size == last:
                break
            last = size
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        # Chrome can still be writing its profile as it exits; a leftover temp folder is fine, a crash is not.
        shutil.rmtree(profile, ignore_errors=True)


def shoot_ready(browser, port, query, w, h, target):
    """Open the page, wait for the harness's ready flag, then take the picture. Returns False if it never got ready."""
    target.unlink(missing_ok=True)
    page = browser.new_page(viewport={"width": w, "height": h}, reduced_motion="reduce")
    try:
        page.goto(f"http://127.0.0.1:{port}/harness/index.html?{query}")
        page.wait_for_function("document.documentElement.dataset.ready === '1'", timeout=90000)
        page.wait_for_timeout(600)
        page.screenshot(path=str(target))
        return True
    except Exception as exc:  # a scene that never renders is a finding, not a crash of the whole run
        print(f"  not ready: {exc.__class__.__name__}", flush=True)
        return False
    finally:
        page.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "harness" / "shots"))
    ap.add_argument("names", nargs="*")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    server = make_server(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    failed = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sync_playwright = None
    if sync_playwright:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            for name in args.names or list(SCENARIOS):
                query, w, h = SCENARIOS[name]
                target = out / f"{name}.png"
                ok = shoot_ready(browser, port, query, w, h, target) and target.stat().st_size >= BLANK_BYTES
                if not ok:
                    failed.append(name)
                print(f"{name}: {target}" + ("" if ok else " (blank or missing)"), flush=True)
            browser.close()
        server.shutdown()
        return 1 if failed else 0
    for name in args.names or list(SCENARIOS):
        query, w, h = SCENARIOS[name]
        target = out / f"{name}.png"
        # A blank frame (the page hadn't rendered when Chrome took the picture, usually because the machine was
        # busy) is a small, nearly empty PNG. Try again, up to three times, rather than keep it.
        for attempt in range(3):
            shoot_once(port, query, w, h, target)
            if target.exists() and target.stat().st_size >= BLANK_BYTES:
                break
        ok = target.exists() and target.stat().st_size >= BLANK_BYTES
        if not ok:
            failed.append(name)
        print(f"{name}: {target}" + ("" if ok else " (blank or missing)"), flush=True)
    server.shutdown()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
