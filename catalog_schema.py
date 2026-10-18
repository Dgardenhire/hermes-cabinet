"""The strict shape of the Bot Cabinet catalog (schema version 2).

One file serves three jobs: it checks a catalog fetched from botcabinet.com before Cabinet believes a
word of it, it checks the bundled snapshot in the tests, and ``tools/make_site_catalog.py`` uses it so
the file the site publishes can never drift from what Cabinet accepts. It is standard library only and
imports nothing from the plugin, so the tools can load it on their own.

Strict means three things:

- Every field has a type, a length limit and, where it matters, a pattern. Anything else is refused.
- A key this version does not know is refused, not ignored. When the site adds a field it raises
  ``schemaVersion``, and Cabinet ships support for that number first. Until then Cabinet keeps showing
  its saved copy. (docs/site-catalog-contract.md)
- Text is refused, never quietly repaired, if it carries control characters or the invisible
  direction-changing characters used to disguise text.

``validate_catalog`` returns a clean copy with every optional field filled in. It raises
``SchemaError`` with every problem it found, so a bad publish is easy to read and to fix.
"""

from __future__ import annotations

import datetime
import re
from typing import Any
from urllib.parse import urlsplit

SCHEMA_VERSION = 2
ORIGIN = "https://botcabinet.com"
ORIGIN_HOST = "botcabinet.com"

MAX_BOTS = 300
MAX_CREWS = 100
MAX_JOBS = 300
MAX_MEMBERS = 12
MAX_STEPS = 20

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TOKEN = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Characters that change how text reads or hide inside it: C0/C1 controls (newline and tab are allowed
# only where a field says so), the bidirectional overrides and isolates, line/paragraph separators,
# zero-width characters and the byte-order mark.
_BAD = re.compile("[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")


class SchemaError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        shown = "; ".join(problems[:6]) + (f"; and {len(problems) - 6} more" if len(problems) > 6 else "")
        super().__init__(shown)


class _Check:
    def __init__(self) -> None:
        self.problems: list[str] = []

    def bad(self, where: str, why: str) -> None:
        self.problems.append(f"{where}: {why}")

    def keys(self, obj: Any, where: str, required: tuple[str, ...], optional: tuple[str, ...]) -> bool:
        if not isinstance(obj, dict):
            self.bad(where, "must be an object")
            return False
        for k in required:
            if k not in obj:
                self.bad(where, f"missing '{k}'")
        for k in obj:
            if k not in required and k not in optional:
                self.bad(where, f"unknown key '{k}'")
        return all(k in obj for k in required)

    def text(self, value: Any, where: str, max_len: int, *, multiline: bool = False, required: bool = True) -> str:
        if not isinstance(value, str):
            self.bad(where, "must be text")
            return ""
        if _BAD.search(value.replace("\n", "").replace("\t", "") if multiline else value):
            self.bad(where, "contains control or hidden characters")
            return ""
        if not multiline and ("\n" in value or "\t" in value):
            self.bad(where, "must be one line")
            return ""
        out = value.strip()
        if required and not out:
            self.bad(where, "must not be empty")
        if len(out) > max_len:
            self.bad(where, f"is {len(out)} characters; the limit is {max_len}")
            return out[:max_len]
        return out

    def pattern(self, value: Any, where: str, rx: re.Pattern, what: str) -> str:
        if not isinstance(value, str) or not rx.match(value):
            self.bad(where, f"must be {what}")
            return ""
        return value

    def lines(self, value: Any, where: str, max_items: int, max_len: int) -> list[str]:
        if not isinstance(value, list):
            self.bad(where, "must be a list")
            return []
        if len(value) > max_items:
            self.bad(where, f"has {len(value)} items; the limit is {max_items}")
        return [self.text(v, f"{where}[{i}]", max_len) for i, v in enumerate(value[:max_items])]

    def url(self, value: Any, where: str, *, host: str | None = None) -> str:
        if not isinstance(value, str) or len(value) > 300 or _BAD.search(value) or " " in value:
            self.bad(where, "must be a web address of at most 300 characters")
            return ""
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            self.bad(where, "must be an https address with no login in it")
            return ""
        if host and parts.hostname.lower() != host:
            self.bad(where, f"must be on {host}")
            return ""
        return value


def _date(c: _Check, value: Any, where: str) -> str:
    if not isinstance(value, str) or not DATE.match(value):
        c.bad(where, "must be a date like 2026-10-04")
        return ""
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        c.bad(where, "is not a real date")
        return ""
    return value


def _unique(c: _Check, items: list[dict], where: str) -> None:
    seen: set[str] = set()
    for i, it in enumerate(items):
        slug = it.get("slug", "")
        if slug in seen:
            c.bad(f"{where}[{i}].slug", f"'{slug}' appears twice")
        seen.add(slug)


BOT_REQUIRED = ("slug", "name", "title", "category", "summary", "outcome")
BOT_OPTIONAL = ("audience", "inputs", "outputs", "examples", "firstMission", "approvals", "offLimits", "worksWith",
                "role", "skills", "routine", "riskLevel", "credits", "href", "test")


def _bot(c: _Check, raw: Any, i: int) -> dict[str, Any]:
    where = f"bots[{i}]"
    if not c.keys(raw, where, BOT_REQUIRED, BOT_OPTIONAL):
        return {}
    slug = c.pattern(raw["slug"], f"{where}.slug", SLUG, "lowercase letters, numbers and dashes")
    out: dict[str, Any] = {
        "slug": slug,
        "name": c.text(raw["name"], f"{where}.name", 60),
        "title": c.text(raw["title"], f"{where}.title", 120),
        "category": c.pattern(raw["category"], f"{where}.category", TOKEN, "a short lowercase word"),
        "summary": c.text(raw["summary"], f"{where}.summary", 400),
        "audience": c.text(raw.get("audience", ""), f"{where}.audience", 400, required=False),
        "outcome": c.text(raw["outcome"], f"{where}.outcome", 500),
        "inputs": c.lines(raw.get("inputs", []), f"{where}.inputs", 12, 300),
        "outputs": c.lines(raw.get("outputs", []), f"{where}.outputs", 12, 300),
        "examples": c.lines(raw.get("examples", []), f"{where}.examples", 8, 300),
        "firstMission": c.text(raw.get("firstMission", ""), f"{where}.firstMission", 500, required=False),
        "approvals": c.lines(raw.get("approvals", []), f"{where}.approvals", 20, 300),
        "offLimits": c.lines(raw.get("offLimits", []), f"{where}.offLimits", 20, 300),
        "worksWith": [c.pattern(s, f"{where}.worksWith[{j}]", SLUG, "a Bot slug") for j, s in
                      enumerate(raw.get("worksWith", [])[:20] if isinstance(raw.get("worksWith", []), list) else [])],
        "role": c.text(raw.get("role", ""), f"{where}.role", 4000, multiline=True, required=False),
        "riskLevel": c.text(raw.get("riskLevel", ""), f"{where}.riskLevel", 30, required=False),
        "href": c.url(raw.get("href", f"{ORIGIN}/bots/{slug}/"), f"{where}.href", host=ORIGIN_HOST),
    }
    if not isinstance(raw.get("worksWith", []), list):
        c.bad(f"{where}.worksWith", "must be a list")
    skills = raw.get("skills", [])
    out["skills"] = []
    if not isinstance(skills, list) or len(skills) > 8:
        c.bad(f"{where}.skills", "must be a list of at most 8")
    else:
        for j, s in enumerate(skills):
            sw = f"{where}.skills[{j}]"
            if c.keys(s, sw, ("name",), ("whenToUse", "steps")):
                out["skills"].append({"name": c.text(s["name"], f"{sw}.name", 80),
                                      "whenToUse": c.text(s.get("whenToUse", ""), f"{sw}.whenToUse", 400, required=False),
                                      "steps": c.lines(s.get("steps", []), f"{sw}.steps", 12, 700)})
    routine = raw.get("routine")
    out["routine"] = None
    if routine is not None and c.keys(routine, f"{where}.routine", ("trigger",), ("activation",)):
        out["routine"] = {"trigger": c.text(routine["trigger"], f"{where}.routine.trigger", 400),
                          "activation": c.pattern(routine.get("activation", "manual-test-required"),
                                                  f"{where}.routine.activation", TOKEN, "a short lowercase word")}
    out["credits"] = []
    credits = raw.get("credits", [])
    if not isinstance(credits, list) or len(credits) > 4:
        c.bad(f"{where}.credits", "must be a list of at most 4")
    else:
        for j, cr in enumerate(credits):
            cw = f"{where}.credits[{j}]"
            if c.keys(cr, cw, ("line", "href"), ()):
                out["credits"].append({"line": c.text(cr["line"], f"{cw}.line", 240), "href": c.url(cr["href"], f"{cw}.href")})
    test = raw.get("test")
    out["test"] = None
    if test is not None and c.keys(test, f"{where}.test", ("status",), ("line",)):
        out["test"] = {"status": c.pattern(test["status"], f"{where}.test.status", TOKEN, "a short lowercase word"),
                       "line": c.text(test.get("line", ""), f"{where}.test.line", 300, required=False)}
    return out


def _crew(c: _Check, raw: Any, i: int, bot_slugs: set[str]) -> dict[str, Any]:
    where = f"crews[{i}]"
    if not c.keys(raw, where, ("slug", "name", "summary", "members", "handoffOrder", "approvalActions", "prohibitedActions", "testStatus"),
                  ("href",)):
        return {}
    slug = c.pattern(raw["slug"], f"{where}.slug", SLUG, "lowercase letters, numbers and dashes")
    members = []
    raw_members = raw["members"]
    if not isinstance(raw_members, list) or not 1 <= len(raw_members) <= MAX_MEMBERS:
        c.bad(f"{where}.members", f"must be a list of 1 to {MAX_MEMBERS}")
        raw_members = []
    for j, m in enumerate(raw_members):
        mw = f"{where}.members[{j}]"
        if c.keys(m, mw, ("slug", "name", "responsibility"), ()):
            ms = c.pattern(m["slug"], f"{mw}.slug", SLUG, "a Bot slug")
            if ms and ms not in bot_slugs:
                c.bad(f"{mw}.slug", f"'{ms}' is not a Bot in this catalog")
            members.append({"slug": ms, "name": c.text(m["name"], f"{mw}.name", 60),
                            "responsibility": c.text(m["responsibility"], f"{mw}.responsibility", 400)})
    steps = []
    raw_steps = raw["handoffOrder"]
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= MAX_STEPS:
        c.bad(f"{where}.handoffOrder", f"must be a list of 1 to {MAX_STEPS}")
        raw_steps = []
    for j, s in enumerate(raw_steps):
        sw = f"{where}.handoffOrder[{j}]"
        if c.keys(s, sw, ("timing", "action", "owner"), ()):
            steps.append({"timing": c.text(s["timing"], f"{sw}.timing", 80), "action": c.text(s["action"], f"{sw}.action", 400),
                          "owner": c.text(s["owner"], f"{sw}.owner", 120)})
    return {"slug": slug, "name": c.text(raw["name"], f"{where}.name", 80), "summary": c.text(raw["summary"], f"{where}.summary", 400),
            "members": members, "handoffOrder": steps,
            "approvalActions": c.lines(raw["approvalActions"], f"{where}.approvalActions", 20, 300),
            "prohibitedActions": c.lines(raw["prohibitedActions"], f"{where}.prohibitedActions", 20, 300),
            "testStatus": c.pattern(raw["testStatus"], f"{where}.testStatus", TOKEN, "a short lowercase word"),
            "href": c.url(raw.get("href", f"{ORIGIN}/crew-kits/{slug}/"), f"{where}.href", host=ORIGIN_HOST)}


def _job(c: _Check, raw: Any, i: int, bot_slugs: set[str]) -> dict[str, Any]:
    where = f"jobs[{i}]"
    if not c.keys(raw, where, ("slug", "title", "outcome", "botSlugs", "optionalBotSlugs", "firstTest"), ()):
        return {}
    out = {"slug": c.pattern(raw["slug"], f"{where}.slug", SLUG, "lowercase letters, numbers and dashes"),
           "title": c.text(raw["title"], f"{where}.title", 120), "outcome": c.text(raw["outcome"], f"{where}.outcome", 500),
           "firstTest": c.text(raw["firstTest"], f"{where}.firstTest", 500)}
    for key in ("botSlugs", "optionalBotSlugs"):
        value = raw[key]
        if not isinstance(value, list) or len(value) > 12:
            c.bad(f"{where}.{key}", "must be a list of at most 12")
            value = []
        out[key] = [c.pattern(s, f"{where}.{key}[{j}]", SLUG, "a Bot slug") for j, s in enumerate(value)]
        for s in out[key]:
            if s and s not in bot_slugs:
                c.bad(f"{where}.{key}", f"'{s}' is not a Bot in this catalog")
    return out


def validate_catalog(doc: Any) -> dict[str, Any]:
    """A clean copy of ``doc``, or ``SchemaError`` listing everything wrong with it."""
    c = _Check()
    top_required = ("schemaVersion", "generatedAt", "source", "siteCommit", "origin", "bots", "crews", "jobs")
    if not c.keys(doc, "catalog", top_required, ()):
        raise SchemaError(c.problems)
    if doc["schemaVersion"] != SCHEMA_VERSION or isinstance(doc["schemaVersion"], bool):
        c.bad("schemaVersion", f"is {doc['schemaVersion']!r}; this Cabinet reads version {SCHEMA_VERSION}")
        raise SchemaError(c.problems)
    if doc["origin"] != ORIGIN:
        c.bad("origin", f"must be exactly {ORIGIN}")
    for key, limit in (("bots", MAX_BOTS), ("crews", MAX_CREWS), ("jobs", MAX_JOBS)):
        # At least one of each: the Fit Test and the crew screens are built on them.
        if not isinstance(doc[key], list) or not 1 <= len(doc[key]) <= limit:
            c.bad(key, f"must be a list of 1 to {limit}")
            doc = {**doc, key: []}
    bots = [_bot(c, b, i) for i, b in enumerate(doc["bots"])]
    slugs = {b["slug"] for b in bots if b}
    out = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _date(c, doc["generatedAt"], "generatedAt"),
        "source": c.text(doc["source"], "source", 200),
        "siteCommit": c.text(doc["siteCommit"], "siteCommit", 80),
        "origin": ORIGIN,
        "bots": bots,
        "crews": [_crew(c, x, i, slugs) for i, x in enumerate(doc["crews"])],
        "jobs": [_job(c, x, i, slugs) for i, x in enumerate(doc["jobs"])],
    }
    _unique(c, out["bots"], "bots")
    _unique(c, out["crews"], "crews")
    _unique(c, out["jobs"], "jobs")
    if c.problems:
        raise SchemaError(c.problems)
    return out
