#!/usr/bin/env python3
"""Build data/catalog.json (the catalog snapshot, schema version 2) from the Bot Cabinet site.

    python3 tools/make_catalog.py <path-to-bot-cabinet-site> [--ref <git ref>]

The site repository is read READ ONLY with ``git show`` at one commit, so its working tree is never
touched and nothing is downloaded. The commit must be the one ``data/site.json`` was built from
(``tools/make_site_data.py``), because the picture-and-status half of every entry comes from there.

What is copied, as published:
  - from ``public/downloads/portable-bot-packs/v2/<slug>.json``: the Bot's role text
    (``instructions.durableRoleAndBoundaries``), its Skills (name, when to use, steps), its routine
    (trigger and activation status) and its risk level;
  - from ``data/site.json`` (same commit): job, inputs, outputs, first mission, approvals, limits,
    credits, test status, and the Crew Kits;
  - from ``src/data/use-cases.ts``: the job guides.

Cabinet's own installer builds a new Hermes profile from these fields and from nothing else, so
anything the site does not publish (a model, tool settings) stays unset.

The same shape is what botcabinet.com should publish at /catalog.json, see docs/site-catalog-contract.md.
``tools/make_site_catalog.py`` emits and checks that file.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "catalog.json"
SITE_JSON = ROOT / "data" / "site.json"
ORIGIN = "https://botcabinet.com"
MAX_STEPS = 12


def git(site: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(site), *args], capture_output=True, check=True).stdout.decode("utf-8")


def show(site: Path, ref: str, path: str) -> str:
    return git(site, "show", f"{ref}:{path}")


def _ts_string(block: str, key: str) -> str:
    m = re.search(rf'\b{key}:\s*"((?:[^"\\]|\\.)*)"', block)
    return json.loads(f'"{m.group(1)}"') if m else ""


def _ts_list(block: str, key: str) -> list[str]:
    m = re.search(rf"\b{key}:\s*\[([^\]]*)\]", block)
    return re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1)) if m else []


def jobs(site: Path, ref: str) -> list[dict]:
    text = show(site, ref, "src/data/use-cases.ts")
    body = text.split("export const BOT_USE_CASES", 1)[1]
    chunks = re.split(r"\n  \{\n(?=    slug:)", body)[1:]
    out = []
    for chunk in chunks:
        out.append({"slug": _ts_string(chunk, "slug"), "title": _ts_string(chunk, "title"),
                    "outcome": _ts_string(chunk, "outcome"), "botSlugs": _ts_list(chunk, "botSlugs"),
                    "optionalBotSlugs": _ts_list(chunk, "optionalBotSlugs"), "firstTest": _ts_string(chunk, "firstTest")})
    return [j for j in out if j["slug"]]


def thin_bots(site: Path, ref: str) -> dict[str, dict]:
    """The site's own one-line records, with the trailing period kept as published."""
    data = json.loads(show(site, ref, "public/api/v2/bots.json"))
    return {b["slug"]: {k: b[k] for k in ("slug", "name", "title", "category", "summary")} for b in data["bots"]}


def build(site: Path, ref: str) -> dict:
    rich = json.loads(SITE_JSON.read_text(encoding="utf-8"))
    commit = git(site, "log", "-1", "--format=%h %cs", ref).strip()
    if rich["siteCommit"] != commit:
        raise SystemExit(f"data/site.json was built at {rich['siteCommit']} but --ref is {commit}. "
                         "Run tools/make_site_data.py at the same ref first.")
    thin = thin_bots(site, ref)
    bots = []
    for b in rich["bots"]:
        pack = json.loads(show(site, ref, f"public/downloads/portable-bot-packs/v2/{b['slug']}.json"))
        routine = (pack.get("routines") or [None])[0]
        skills = []
        for s in pack.get("skills", []):
            skills.append({"name": s["name"], "whenToUse": s.get("whenToUse", ""), "steps": list(s.get("steps", []))[:MAX_STEPS]})
        bots.append({
            **thin[b["slug"]],
            "audience": b["audience"], "outcome": b["outcome"],
            "inputs": b["inputs"], "outputs": b["outputs"], "examples": b["examples"],
            "firstMission": b["firstMission"], "approvals": b["approvals"], "offLimits": b["offLimits"],
            "worksWith": b["worksWith"],
            "role": pack["instructions"]["durableRoleAndBoundaries"],
            "skills": skills,
            "routine": ({"trigger": routine["trigger"], "activation": routine.get("activationStatus", "manual-test-required")}
                        if routine else None),
            "riskLevel": pack["controls"].get("riskLevel", ""),
            "credits": [{"line": c["line"], "href": c["href"]} for c in b["credits"]],
            "href": b["href"],
            "test": {"status": b["test"]["status"], "line": b["test"]["line"]},
        })
    crews = []
    for c in rich["crews"]:
        crews.append({"slug": c["slug"], "name": c["name"], "summary": c["summary"],
                      "members": c["members"],
                      "handoffOrder": [{"timing": s["step"], "action": s["action"], "owner": s["owner"]} for s in c["steps"]],
                      "approvalActions": c["approvals"], "prohibitedActions": c["offLimits"],
                      "testStatus": c["testStatus"], "href": c["href"]})
    return {"schemaVersion": 2, "generatedAt": commit.split(" ")[1], "source": "botcabinet.com site repository (Damon Gardenhire, MIT)",
            "siteCommit": commit, "origin": ORIGIN, "bots": bots, "crews": crews, "jobs": jobs(site, ref)}


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    ref = argv[argv.index("--ref") + 1] if "--ref" in argv else None
    if ref:
        args = [a for a in args if a != ref]
    if len(args) != 1:
        print(__doc__)
        return 2
    site = Path(args[0]).resolve()
    ref = ref or json.loads(SITE_JSON.read_text(encoding="utf-8"))["siteCommit"].split(" ")[0]
    from catalog_schema import validate_catalog
    doc = build(site, ref)
    doc = validate_catalog(doc)
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{OUT}: {len(doc['bots'])} Bots, {len(doc['crews'])} crews, {len(doc['jobs'])} jobs at {doc['siteCommit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
