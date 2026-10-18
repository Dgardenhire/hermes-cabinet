#!/usr/bin/env python3
"""Build Cabinet's picture of botcabinet.com: data/site.json and desktop/src/site-data.js.

    python3 tools/make_site_data.py <path-to-bot-cabinet-site> [--ref <git ref>]

Reads the site repository READ ONLY, at one git ref (default: the newest commit on any branch), with
`git show`, so the working tree there is never touched. Nothing is downloaded.

What it copies, as published:
  - every Bot in public/api/v2/bots.json with its portable pack (job, inputs, outputs, first mission,
    approvals, limits), its painted portrait, its credit (starter-bots.ts `inspiredBy`) and its
    "works with" list;
  - each Bot's test status, decided exactly the way the site decides it (src/lib/bot-status-copy.ts):
    a recorded task run (src/data/bot-release-evidence.ts BOT_RUNTIME_EVIDENCE) or two reproduced
    role runs (REPRODUCED_BOT_EVIDENCE) make it "tested"; an import check alone does not;
  - every Crew Kit bundle (members, handoff order, approvals, limits, published crew test result);
  - the Test Records (src/data/proof-room.ts) and the Inspection Desk's evidence labels (src/app/trust).

Images are resized with macOS `sips` (portraits 340px wide, JPEG quality 62; the two scenes 1000-1200px;
the wordmark a small PNG) and embedded as data URIs in desktop/src/site-data.js, because a desktop
plugin may load nothing over the network.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "data" / "site.json"
OUT_JS = ROOT / "desktop" / "src" / "site-data.js"
ORIGIN = "https://botcabinet.com"

CATEGORY_LABELS = {  # src/data/starter-bots.ts STARTER_CATEGORY_LABELS
    "writing": "Writing and communications",
    "research": "Research and monitoring",
    "planning": "Planning and client work",
    "technical": "Software and operations",
    "learning": "Learning",
}
CREW_STATUS = {  # src/app/crew-kits/[slug]/page.tsx statusLabel
    "import-test-passed": "Passed",
    "not-tested": "Not tested",
    "failed-acceptance": "Failed acceptance",
    "correction-prepared-not-tested": "Correction prepared; not tested",
    "not-recorded": "Not recorded",
}
PROOF_STATE_NAMES = {  # src/data/proof-room.ts PROOF_STATE_NAMES
    "test-designed": "Test designed",
    "test-prepared": "Test prepared",
    "recorded-excerpt": "Recorded excerpt",
    "prompt-contract-recorded": "Two prompt-contract runs recorded",
    "failed-runtime": "Runtime failed acceptance",
    "runtime-passed": "Runtime passed once",
    "reproduced": "Reproduced",
}

# Outside work a Bot's skill adapts (src/data/guides.ts, "Fix one bad AI result": the Ops Bot's
# Diagnose an AI result skill, see the portable-pack test that checks this link).
EXTRA_CREDITS = {
    "ops": {"creator": "Muse at Work", "work": "Diagnose My Agent's Mistake",
            "href": "https://museatwork.app/#w=1d0661a5-1cf7-4013-9aff-9cf605bcbe8c",
            "line": "Its Diagnose an AI result skill adapts Diagnose My Agent's Mistake, listed on Muse at Work"},
}
# Community guides on the Hermes Bible that match a Bot's job, credited to their authors
# (the mapping and authors in .planning/bot-cabinet-v2/DESIGN.md S3, taken from each page's JSON-LD).
BIBLE = "https://www.hermesbible.com/flows/"
FLOWS = {
    "kanban": {"title": "How to Dominate Projects with the Hermes Agent Kanban Board", "by": "Tony Simons",
               "href": BIBLE + "dominate-projects-with-hermes-kanban"},
    "research": {"title": "Build a 3-Agent Research Department", "by": "YanXbt",
                 "href": BIBLE + "3-agent-research-department-notebooklm-obsidian"},
    "jira": {"title": "Four AI agents turn Jira tickets into reviewed PRs", "by": "Luke",
             "href": BIBLE + "jira-to-pr-four-agents"},
    "steal": {"title": "20 Hermes Agent Workflows You'll Want to Steal Immediately", "by": "Tony Simons",
              "href": BIBLE + "20-hermes-agent-workflows-to-steal"},
    "operator": {"title": "How to Become a Hermes Agent Operator", "by": "Shann³",
                 "href": BIBLE + "hermes-agent-operator-control-room-fleet"},
}
BOT_FLOWS = {"planner": ["kanban", "steal"], "chief-of-staff": ["kanban"], "scout": ["research"],
             "researcher": ["research"], "daily-newspaper": ["research"], "coder": ["jira"]}
CREW_FLOWS = {"*": ["operator"], "personal-planning-desk": ["operator", "research"],
              "weekly-business-review": ["operator", "research"], "product-delivery-crew": ["operator", "jira"]}

SCENES = {  # name: (site path, width, quality, format)
    "doors": ("public/atelier/cabinet-doors-v1.png", 1200, 56, "jpeg"),
    "drafting": ("public/atelier/bot-lab-drafting-room-v1.jpg", 1000, 48, "jpeg"),
    "wordmark": ("public/brand/bot-cabinet-wordmark-transparent-v1.png", 360, None, "png"),
}


def git(site: Path, *args: str, binary: bool = False):
    out = subprocess.run(["git", "-C", str(site), *args], capture_output=True, check=True)
    return out.stdout if binary else out.stdout.decode("utf-8")


def show(site: Path, ref: str, path: str) -> str:
    return git(site, "show", f"{ref}:{path}")


def newest_ref(site: Path) -> str:
    return git(site, "log", "--all", "--date-order", "-1", "--format=%h").strip()


def strip_period(text: str) -> str:
    return text[:-1] if text.endswith(".") and not text.endswith("..") else text


def long_date(iso: str) -> str:
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
              "October", "November", "December"]
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{months[m - 1]} {d}, {y}"


def starter_blocks(text: str) -> dict[str, str]:
    """Each starter Bot's source block in starter-bots.ts, keyed by slug (TS and JSON-style entries)."""
    marks = [(m.start(), m.group(1)) for m in re.finditer(r'(?m)^\s*"?slug"?:\s*"([a-z0-9-]+)"', text)]
    blocks = {}
    for i, (start, slug) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        blocks.setdefault(slug, text[start:end])
    return blocks


def ts_list(block: str, key: str) -> list[str]:
    m = re.search(rf'"?{key}"?:\s*\[([^\]]*)\]', block)
    return re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1)) if m else []


def inspired_by(block: str) -> dict | None:
    m = re.search(r'"?inspiredBy"?:\s*\{([^}]*)\}', block)
    if not m:
        return None
    body = m.group(1)
    get = lambda k: (re.search(rf'"?{k}"?:\s*"((?:[^"\\]|\\.)*)"', body) or [None, ""])[1]
    return {"creator": get("creator"), "work": get("work"), "href": get("href")}


def evidence_maps(text: str) -> tuple[dict, dict]:
    """REPRODUCED_BOT_EVIDENCE and BOT_RUNTIME_EVIDENCE from bot-release-evidence.ts."""
    rep_src = text.split("REPRODUCED_BOT_EVIDENCE", 1)[1].split("};", 1)[0]
    reproduced = {}
    for m in re.finditer(r'(?m)^\s*"?([a-z0-9-]+)"?:\s*\{([^}]*)\}', rep_src.split("= {", 1)[1]):
        body = m.group(2)
        f = lambda k: re.search(rf'{k}:\s*"([^"]*)"', body).group(1)
        reproduced[m.group(1)] = {"hermesVersion": f("hermesVersion"), "testedDate": f("testedDate"),
                                  "proofSlug": f("proofSlug")}
    run_src = text.split("BOT_RUNTIME_EVIDENCE", 1)[1].split("= {", 1)[1].split("\n};", 1)[0]
    runtime = {}
    for m in re.finditer(r'"([a-z0-9-]+)":\s*\{(.*?)\n  \}', run_src, flags=re.S):
        body = m.group(2)
        f = lambda k: re.search(rf'{k}:\s*"((?:[^"\\]|\\.)*)"', body).group(1)
        runtime[m.group(1)] = {"testedDate": f("testedDate"), "proofPath": f("proofPath"), "summary": f("summary")}
    return reproduced, runtime


def bot_status(slug: str, pack: dict, reproduced: dict, runtime: dict) -> dict:
    """The site's own test status for one Bot (src/lib/bot-status-copy.ts botImportAndRunStatus)."""
    imp = pack["platforms"]["hermes"].get("importEvidence")
    tested, not_tested = [], []
    if imp:
        tested.append({"what": "Hermes import check",
                       "detail": f"The archive passed an isolated import and bundled-Skill presence check in Hermes Agent "
                                 f"{imp['hermesVersion']} on {long_date(imp['testedDate'])}",
                       "date": imp["testedDate"]})
    else:
        not_tested.append({"what": "Hermes import check", "detail": "New prepared profile: Hermes import testing is pending"})
    run, rep = runtime.get(slug), reproduced.get(slug)
    if run:
        status, kind = "tested", "one-run"
        tested.append({"what": "One task run", "detail": strip_period(run["summary"]), "date": run["testedDate"],
                       "href": ORIGIN + run["proofPath"]})
        not_tested.append({"what": "Repeated runs", "detail": "This is one run, not evidence of general reliability or approval for scheduling"})
        line = "One recorded task run is available"
    elif rep:
        status, kind = "tested", "reproduced"
        tested.append({"what": "Two task runs", "detail": "Two published first-mission role runs passed their disclosed checks",
                       "date": rep["testedDate"], "href": f"{ORIGIN}/proof/{rep['proofSlug']}/"})
        not_tested.append({"what": "Human technical review", "detail": "Human technical review remains pending"})
        line = "Two recorded task runs passed"
    else:
        status, kind = "not-tested", "import-only" if imp else "prepared"
        not_tested.append({"what": "A real task", "detail": "Human technical and role-specific output tests remain pending"})
        line = "Hermes archive import checked; a full task run is still pending" if imp else "Prepared, but not yet task-tested"
    # botcabinet.com also tracks other platforms (for example a Grok Bot build); Cabinet is for Hermes, so those are left out.
    return {"status": status, "kind": kind, "line": line, "tested": tested, "notTested": not_tested}


def owner_parts(owner: str, by_name: dict) -> list[dict]:
    parts = []
    for raw in [p.strip() for p in owner.split("+")]:
        b = by_name.get(raw.casefold())
        parts.append({"bot": b, "label": raw} if b else {"you": True, "label": raw[:1].upper() + raw[1:]})
    return parts


def resize(site: Path, ref: str, path: str, width: int, quality: int | None, fmt: str, tmp: Path) -> str:
    src = tmp / ("src" + Path(path).suffix)
    src.write_bytes(git(site, "show", f"{ref}:{path}", binary=True))
    out = tmp / ("out." + ("jpg" if fmt == "jpeg" else "png"))
    cmd = ["sips", "-s", "format", fmt, "--resampleWidth", str(width)]
    if quality:
        cmd += ["-s", "formatOptions", str(quality)]
    subprocess.run(cmd + [str(src), "--out", str(out)], check=True, capture_output=True)
    mime = "image/jpeg" if fmt == "jpeg" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(out.read_bytes()).decode("ascii")


def build(site: Path, ref: str) -> tuple[dict, dict]:
    commit = git(site, "log", "-1", "--format=%h %cs", ref).strip()
    api = json.loads(show(site, ref, "public/api/v2/bots.json"))
    starters = starter_blocks(show(site, ref, "src/data/starter-bots.ts"))
    reproduced, runtime = evidence_maps(show(site, ref, "src/data/bot-release-evidence.ts"))
    by_name = {}
    bots = []
    for entry in api["bots"]:
        slug = entry["slug"]
        pack = json.loads(show(site, ref, f"public/downloads/portable-bot-packs/v2/{slug}.json"))
        block = starters.get(slug, "")
        credit = inspired_by(block)
        credits = []
        if credit:
            credits.append({**credit, "line": f"Inspired by {credit['creator']}'s {credit['work']}"})
        if slug in EXTRA_CREDITS:
            credits.append(EXTRA_CREDITS[slug])
        bot = {
            "slug": slug, "name": entry["name"], "title": entry["title"],
            "category": entry["category"], "categoryLabel": CATEGORY_LABELS.get(entry["category"], entry["category"]),
            "summary": strip_period(entry["summary"]),
            "audience": strip_period(pack["identity"].get("audience", "")),
            "outcome": strip_period(pack["job"]["outcome"]),
            "inputs": pack["job"]["inputs"], "outputs": pack["job"]["outputs"],
            "examples": pack["job"].get("exampleRequests", []),
            "firstMission": pack["job"]["firstMission"],
            "approvals": pack["controls"]["requiresApproval"], "offLimits": pack["controls"]["prohibited"],
            "worksWith": ts_list(block, "worksWith"),
            "portrait": entry["portraitUrl"],
            "credits": credits,
            "learnMore": [FLOWS[k] for k in BOT_FLOWS.get(slug, [])],
            "href": f"{ORIGIN}/bots/{slug}/",
            "test": bot_status(slug, pack, reproduced, runtime),
        }
        bots.append(bot)
        by_name[entry["name"].casefold()] = slug
    names = {b["slug"]: b["name"] for b in bots}
    crews = []
    bundle_dir = "public/downloads/crew-kits/bundles"
    for path in sorted(p for p in git(site, "ls-tree", "--name-only", f"{ref}:{bundle_dir}").split() if p.endswith(".json")):
        kit = json.loads(show(site, ref, f"{bundle_dir}/{path}"))
        md = show(site, ref, f"public/downloads/crew-kits/{kit['slug']}.md").splitlines()
        summary = next((line.strip() for line in md[1:] if line.strip()), "")
        status = kit.get("runtimeStatus", "not-tested")
        crews.append({
            "slug": kit["slug"], "name": kit["name"], "summary": strip_period(summary),
            "members": [{"slug": m["slug"], "name": names.get(m["slug"], m["name"]), "responsibility": strip_period(m["responsibility"])}
                        for m in kit["members"]],
            "steps": [{"n": i + 1, "step": h["timing"], "action": strip_period(h["action"]), "owner": h["owner"],
                       "who": owner_parts(h["owner"], by_name)} for i, h in enumerate(kit["handoffOrder"])],
            "approvals": kit["approvalActions"], "offLimits": kit["prohibitedActions"],
            "testStatus": status, "testLabel": CREW_STATUS.get(status, status),
            "testDate": kit.get("runtimeCheckedAt"),
            "testHref": ORIGIN + kit["runtimeEvidenceHref"] if kit.get("runtimeEvidenceHref") else None,
            "permissions": kit.get("permissionsNotice", ""),
            "learnMore": [FLOWS[k] for k in CREW_FLOWS.get(kit["slug"], CREW_FLOWS["*"])],
            "href": f"{ORIGIN}/crew-kits/{kit['slug']}/",
        })
    records = proof_records(show(site, ref, "src/data/proof-room.ts"))
    proof_page = show(site, ref, "src/app/proof/page.tsx")
    trust_page = show(site, ref, "src/app/trust/page.tsx")
    clean = lambda s: re.sub(r"\s+", " ", s).strip()
    inspection = {
        # Test Records (src/app/proof/page.tsx): the lead, the label legend, the disclosure line.
        "recordsLead": clean(re.search(r'className="inner-deck">(.*?)</p>', proof_page, flags=re.S).group(1)),
        "legend": [{"label": clean(a), "meaning": clean(b)}
                   for a, b in re.findall(r"<div><dt>([^<]+)</dt><dd>([^<]+)</dd></div>", proof_page)
                   if "{" not in a + b],
        "disclosure": clean(re.search(r'proof-room-disclosure">.*?<p>(.*?)</p>', proof_page, flags=re.S).group(1)),
        # Inspection Desk (src/app/trust/page.tsx): how evidence is described, what a check proves.
        "evidenceKinds": [{"title": t, "copy": c} for t, c in re.findall(r'title: "([^"]+)",\s*copy: "([^"]+)"', trust_page)],
        "checks": [{"title": clean(t), "copy": clean(c)} for t, c in
                   re.findall(r'<article className="content-card"><h2>([^<]+)</h2><p>([^<]+)</p></article>', trust_page)],
        "caveat": clean(re.sub(r"<[^>]+>", " ", re.search(r'trust-caveat">(.*?)</aside>', trust_page, flags=re.S).group(1))),
    }
    site_data = {
        "source": "botcabinet.com site repository (Damon Gardenhire, MIT)",
        "siteCommit": commit,
        "origin": ORIGIN,
        "tagline": "Independent guide to useful Bots · Across platforms",
        "promise": "Find a Bot that makes your day easier",
        "deck": "Find a useful idea, choose where to run it, and try one real task. Each Bot page tells you what has, and has not, been tested",
        "ladder": [
            {"kind": "assignment", "label": "Assignment", "note": "One result"},
            {"kind": "skill", "label": "Skill", "note": "Reusable method"},
            {"kind": "routine", "label": "Routine", "note": "Runs on a trigger"},
            {"kind": "bot", "label": "Bot", "note": "Keeps the job and context"},
            {"kind": "crew", "label": "Crew", "note": "Specialists with handoffs"},
        ],
        "categories": [{"key": k, "label": v} for k, v in CATEGORY_LABELS.items()],
        "bots": bots,
        "crews": crews,
        "records": records,
        "recordStates": [{"state": k, "label": v} for k, v in PROOF_STATE_NAMES.items()],
        "inspection": inspection,
        "pages": {
            "start": ORIGIN + "/start/", "fit": ORIGIN + "/fit/", "cabinet": ORIGIN + "/bots/", "lab": ORIGIN + "/workshop/",
            "crews": ORIGIN + "/crew-kits/", "records": ORIGIN + "/proof/", "inspection": ORIGIN + "/trust/",
        },
    }
    return site_data, images(site, ref, bots)


def proof_records(text: str) -> list[dict]:
    body = text.split("PROOF_ROOM_DEMOS", 1)[1]
    out = []
    for m in re.finditer(r'\n  \{\n    slug: "([^"]+)",(.*?)\n  \}', body, flags=re.S):
        block = m.group(2)
        f = lambda k: (re.search(rf'\n    {k}: "((?:[^"\\]|\\.)*)"', block) or [None, ""])[1]
        state = f("state")
        out.append({"slug": m.group(1), "bot": f("botSlug"), "crew": f("subjectKind") == "crew", "title": f("title"),
                     "outcome": strip_period(f("outcome")), "summary": f("summary"), "state": state,
                     "stateLabel": PROOF_STATE_NAMES.get(state, state), "stateDetail": f("stateDetail"),
                     "href": f"{ORIGIN}/proof/{m.group(1)}/"})
    return out


def images(site: Path, ref: str, bots: list[dict]) -> dict:
    out = {}
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        for path in sorted({b["portrait"] for b in bots}):
            out[path] = resize(site, ref, "public" + path, 340, 62, "jpeg", tmp)
        for name, (path, width, quality, fmt) in SCENES.items():
            out[name] = resize(site, ref, path, width, quality, fmt, tmp)
    return out


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    if len(args) < 1:
        print(__doc__)
        return 2
    site = Path(args[0]).resolve()
    ref = argv[argv.index("--ref") + 1] if "--ref" in argv else newest_ref(site)
    data, imgs = build(site, ref)
    OUT_JSON.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        f"// Generated by tools/make_site_data.py from the botcabinet.com site at {data['siteCommit']}. Do not edit;",
        "// run the tool again. SITE equals data/site.json. IMAGES are the site's own art, resized and embedded",
        "// because a desktop plugin loads nothing over the network.",
        "export const SITE = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        "export const IMAGES = {",
        *[f"  {json.dumps(k)}: {json.dumps(v)}," for k, v in imgs.items()],
        "}",
        "",
    ]
    OUT_JS.write_text("\n".join(lines), encoding="utf-8")
    size = OUT_JS.stat().st_size
    print(f"{OUT_JSON.name}: {len(data['bots'])} Bots, {len(data['crews'])} crews, {len(data['records'])} records; "
          f"{OUT_JS.relative_to(ROOT)}: {size / 1024:.0f} KB ({len(imgs)} images) from {data['siteCommit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
