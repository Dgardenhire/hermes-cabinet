"""The published Bots, Crew Kits and job guides, and plain word matching against them.

``data/catalog.json`` is a snapshot of botcabinet.com taken by ``tools/make_catalog.py``. ``sync.py``
can replace it, for this run, with a newer copy fetched from botcabinet.com (or the last good copy it
kept); ``set_active`` swaps the copy every function below reads. Nothing here touches the network.
Matching is word overlap on the fields v1 matched (name, title, summary, category) plus each job
guide's title and outcome. It spends no model.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent / "data"

STOP = set("""a an and are as at be by can do does for from get i in into is it its me my of on or our so that the
their them then this to up us we what when which who will with you your each every per one two three four five
need needs want help make makes made new more most some any all also just like than over under about""".split())

# Words that mean the same job, folded to one form before matching.
SYNONYMS = {"trip": "travel", "vacation": "travel", "itinerary": "travel", "holiday": "travel",
            "newsletter": "newsletter", "grants": "grant", "funding": "grant", "funder": "grant",
            "proposals": "proposal", "pitch": "proposal", "code": "software", "app": "software",
            "feature": "software", "bug": "software", "study": "learning", "exam": "learning",
            "certification": "learning", "hire": "hiring", "candidate": "hiring", "interview": "hiring",
            "competitor": "competitor", "competitors": "competitor", "price": "pricing", "prices": "pricing"}


def words(text: str) -> set[str]:
    out = set()
    for raw in re.findall(r"[a-z0-9]+", str(text or "").casefold()):
        if raw in STOP or len(raw) < 3:
            continue
        w = SYNONYMS.get(raw, raw)
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(SYNONYMS.get(w, w))
    return out


@lru_cache(maxsize=1)
def bundled() -> dict[str, Any]:
    """The snapshot that ships with the plugin."""
    return json.loads((DATA / "catalog.json").read_text(encoding="utf-8"))


_ACTIVE: dict[str, Any] | None = None


def set_active(doc: dict[str, Any] | None) -> None:
    """Use ``doc`` (already checked by ``catalog_schema``) until told otherwise. ``None`` goes back to the snapshot."""
    global _ACTIVE
    _ACTIVE = doc


def load() -> dict[str, Any]:
    return _ACTIVE if _ACTIVE is not None else bundled()


def bots() -> list[dict[str, Any]]:
    return load()["bots"]


def crews() -> list[dict[str, Any]]:
    return load()["crews"]


def jobs() -> list[dict[str, Any]]:
    return load()["jobs"]


def bot(slug: str) -> dict[str, Any] | None:
    return next((b for b in bots() if b["slug"] == slug), None)


def crew(slug: str) -> dict[str, Any] | None:
    return next((c for c in crews() if c["slug"] == slug), None)


def _score(query: set[str], *texts: str) -> int:
    return len(query & words(" ".join(texts)))


def best_job(text: str) -> tuple[dict[str, Any] | None, int]:
    q = words(text)
    scored = [(_score(q, j["title"], j["title"], j["outcome"]), i, j) for i, j in enumerate(jobs())]
    score, _, job = max(scored, key=lambda s: (s[0], -s[1]))
    return (job, score) if score else (None, 0)


def best_bot(text: str) -> tuple[dict[str, Any] | None, int]:
    q = words(text)
    job, job_score = best_job(text)
    scored = []
    for i, b in enumerate(bots()):
        s = _score(q, b["name"], b["title"], b["summary"], b["category"])
        if job and b["slug"] in job["botSlugs"]:
            s += job_score  # a guide that names this Bot as its lead counts toward it
        scored.append((s, i, b))
    score, _, found = max(scored, key=lambda s: (s[0], -s[1]))
    return (found, score) if score else (None, 0)


def best_member(text: str, crew_: dict[str, Any]) -> dict[str, Any] | None:
    """The crew member whose own job best matches the text: the Bot to start with alone."""
    q = words(text)
    scored = []
    for i, m in enumerate(crew_["members"]):
        b = bot(m["slug"]) or {}
        scored.append((_score(q, m["responsibility"], b.get("title", ""), b.get("summary", "")), -i, m["slug"]))
    score, _, slug = max(scored)
    return bot(slug) if score else bot(crew_["members"][0]["slug"])


def best_crew(text: str) -> tuple[dict[str, Any] | None, int]:
    q = words(text)
    job, job_score = best_job(text)
    scored = []
    for i, c in enumerate(crews()):
        own = _score(q, c["name"], c["summary"])
        s = 2 * own + _score(q, *(m["responsibility"] for m in c["members"]))
        if job and set(job["botSlugs"]) <= {m["slug"] for m in c["members"]}:
            s += job_score
        scored.append((s, own, i, c))
    # A tie goes to the crew whose own name and summary match the job more closely, then to catalog order.
    score, _, _, found = max(scored, key=lambda s: (s[0], s[1], -s[2]))
    return (found, score) if score else (None, 0)


def common_jobs(limit: int = 6) -> list[dict[str, Any]]:
    """Job guides for the quick-start list, each with the Bot that leads it."""
    picks = ("personal-morning-newspaper", "weekly-newsletter", "client-proposal", "grant-opportunity-review",
             "project-launch-plan", "software-feature-build")
    out = []
    for slug in picks[:limit]:
        job = next(j for j in jobs() if j["slug"] == slug)
        lead = bot(job["botSlugs"][0]) if job["botSlugs"] else None
        out.append({"slug": job["slug"], "title": job["title"], "outcome": job["outcome"],
                    "lead": lead["name"] if lead else "",
                    "optional": [bot(s)["name"] for s in job["optionalBotSlugs"] if bot(s)]})
    return out
