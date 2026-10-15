"""View models for Cabinet's pages. The desktop half only renders what these return.

- ``fit_form()``: the Fit Test questions, the common jobs and the five possible answers.
- ``fit_answer(body)``: one answer, with every question it depended on and the closest published match.
- ``lab_start(body)`` / ``lab_plan(body)``: Bot Lab's draft plan and the profile it would create.
- ``catalog_view()`` / ``catalog_refresh()``: the catalog the page draws on, and where it came from.
- ``bot_plan`` / ``crew_setup`` / ``install_confirm`` / ``my_bots`` / ``remove`` ...: adding, setting up and undoing
  (the logic is in ``installer``).

Every function takes plain dicts and returns plain dicts, so the tests need no Hermes and no FastAPI.
``ApiError`` carries the HTTP status the dashboard route should answer with.
"""

from __future__ import annotations

import re
from typing import Any

from . import catalog, fit, hermes_bridge, installer, safety, sync
from .errors import ApiError


# ---------------------------------------------------------------- credits --
# The rule: adapted work carries its original creator's credit on every surface it appears on.

FIT_TEST_CREDIT = {"line": "Based on the Bot Fit Test from botcabinet.com", "href": "https://botcabinet.com/"}

CREDITS = {
    "bot:daily-newspaper": {"line": "Inspired by Karen X. Cheng's The Morning Newspaper",
                            "href": "https://x.ai/bot/marketplace/bots/the-morning-newspaper"},
    "job:personal-morning-newspaper": {"line": "Inspired by Karen X. Cheng's The Morning Newspaper",
                                       "href": "https://x.ai/bot/marketplace/bots/the-morning-newspaper"},
    "bot:ops": {"line": "Its Diagnose an AI result skill is adapted from Diagnose My Agent's Mistake on Muse at Work",
                "href": "https://museatwork.app/#w=1d0661a5-1cf7-4013-9aff-9cf605bcbe8c"},
}

HERMES_BIBLE = "https://www.hermesbible.com"
LEARN_MORE = {
    "assignment": [{"title": "Bot Mode", "by": "Hermes docs", "href": "https://hermes-agent.nousresearch.com/docs/user-guide/bot-mode"}],
    "skill": [{"title": "20 Hermes Agent Workflows You'll Want to Steal Immediately", "by": "Tony Simons, on the Hermes Bible",
               "href": f"{HERMES_BIBLE}/flows/20-hermes-agent-workflows-to-steal"}],
    "routine": [{"title": "Hermes Flightplan #1", "by": "witcheer, on the Hermes Bible",
                 "href": f"{HERMES_BIBLE}/flows/hermes-flightplan-1-always-on-telegram-agent"}],
    "bot": [{"title": "I'm Not Sharing My SOUL.md", "by": "Tony Simons, on the Hermes Bible",
             "href": f"{HERMES_BIBLE}/flows/soul-md-operating-contract-template"}],
    "crew": [{"title": "How to Become a Hermes Agent Operator", "by": "Shann³, on the Hermes Bible",
              "href": f"{HERMES_BIBLE}/flows/hermes-agent-operator-control-room-fleet"},
             {"title": "Build a 3-Agent Research Department", "by": "YanXbt, on the Hermes Bible",
              "href": f"{HERMES_BIBLE}/flows/3-agent-research-department-notebooklm-obsidian"}],
}
COMMUNITY_FLOWS = {"label": "Find more community flows", "by": "Guides by Hermes users on the Hermes Bible",
                   "href": f"{HERMES_BIBLE}/flows"}

# Starting answers for the common jobs. Cabinet's own judgment, shown in full and changeable before
# you press the button; the job guides on botcabinet.com don't publish these answers.
PRESETS: dict[str, dict[str, Any]] = {
    "personal-morning-newspaper": {"result": "Make me a one-page morning newspaper from my calendar, messages and one interest",
                                   "frequency": "repeat", "needsContinuingContext": True, "needsMultipleSpecialists": False,
                                   "workProvenManually": False, "overlapsExistingRole": "no"},
    "weekly-newsletter": {"result": "Draft my weekly newsletter from the notes and links I collect",
                          "frequency": "repeat", "needsContinuingContext": True, "needsMultipleSpecialists": False,
                          "workProvenManually": True, "overlapsExistingRole": "unsure"},
    "client-proposal": {"result": "Turn a discovery call transcript into a client proposal",
                        "frequency": "repeat", "needsContinuingContext": False, "needsMultipleSpecialists": True,
                        "workProvenManually": True, "overlapsExistingRole": "no"},
    "grant-opportunity-review": {"result": "Check whether one grant opportunity is worth applying for",
                                 "frequency": "once", "needsContinuingContext": False, "needsMultipleSpecialists": False,
                                 "workProvenManually": False, "overlapsExistingRole": "no"},
    "project-launch-plan": {"result": "Plan the launch of a new project with owners, dates and decisions",
                            "frequency": "once", "needsContinuingContext": False, "needsMultipleSpecialists": False,
                            "workProvenManually": False, "overlapsExistingRole": "no"},
    "software-feature-build": {"result": "Build one software feature from a written requirement through review",
                               "frequency": "repeat", "needsContinuingContext": True, "needsMultipleSpecialists": True,
                               "workProvenManually": True, "overlapsExistingRole": "no"},
}


def _title(text: str) -> str:
    """Titles and buttons carry no closing period (copy rule)."""
    return re.sub(r"[.\s]+$", "", str(text or ""))


def fit_form() -> dict[str, Any]:
    questions = []
    for q in fit.QUESTIONS:
        questions.append({"id": q.id, "text": q.text, "help": q.help, "required": q.required,
                          "choices": [{"value": v, "label": label} for v, label in q.choices]})
    jobs = []
    for job in catalog.common_jobs():
        jobs.append({**job, "answers": PRESETS[job["slug"]], "credit": CREDITS.get(f"job:{job['slug']}")})
    return {
        "questions": questions,
        "required": ["result", *fit.BRANCH_QUESTIONS],
        "routineDetails": list(fit.ROUTINE_CONTROLS),
        "commonJobs": jobs,
        "kinds": [{"kind": k, **{x: fit.KINDS[k][x] for x in ("title", "meaning")}} for k in fit.KIND_ORDER],
        "communityFlows": COMMUNITY_FLOWS,
        "credit": FIT_TEST_CREDIT,
        "catalog": {"bots": len(catalog.bots()), "crews": len(catalog.crews()), "siteCommit": catalog.load()["siteCommit"]},
    }


def _bot_card(b: dict[str, Any] | None) -> dict[str, Any] | None:
    if not b:
        return None
    return {"slug": b["slug"], "name": b["name"], "title": b["title"], "summary": _title(b["summary"]),
            "href": f"https://botcabinet.com/bots/{b['slug']}/", "credit": CREDITS.get(f"bot:{b['slug']}")}


def crew_card(c: dict[str, Any] | None) -> dict[str, Any] | None:
    if not c:
        return None
    tested = {"not-tested": "Crew test not run yet", "failed-acceptance": "Failed its published crew test",
              "passed": "Passed its published crew test"}.get(c["testStatus"], "Crew test not run yet")
    members = []
    for m in c["members"]:
        members.append({"slug": m["slug"], "name": m["name"], "initial": m["name"][:1],
                        "responsibility": _title(m["responsibility"]), "credit": CREDITS.get(f"bot:{m['slug']}")})
    steps = []
    for i, step in enumerate(c["handoffOrder"], 1):
        owner = re.sub(r"\bperson\b", "you", step["owner"], flags=re.I)
        owner = owner[:1].upper() + owner[1:]
        steps.append({"n": i, "step": step["timing"], "owner": owner, "action": _title(step["action"]),
                      "you": bool(re.search(r"\bperson\b", step["owner"], re.I))})
    return {"slug": c["slug"], "name": c["name"], "summary": _title(c["summary"]), "members": members,
            "steps": steps, "approvals": c["approvalActions"], "offLimits": c["prohibitedActions"],
            "testStatus": c["testStatus"], "tested": tested, "href": f"https://botcabinet.com/crew-kits/{c['slug']}/"}


def _match(kind: str, text: str) -> dict[str, Any]:
    job, _ = catalog.best_job(text)
    found_bot, _ = catalog.best_bot(text)
    found_crew, _ = catalog.best_crew(text)
    out: dict[str, Any] = {"job": {"slug": job["slug"], "title": job["title"]} if job else None,
                           "bot": None, "crew": None, "smaller": None}
    if kind == "crew":
        out["crew"] = crew_card(found_crew)
        out["smaller"] = _bot_card(catalog.best_member(text, found_crew) if found_crew else found_bot)
    elif kind == "bot":
        out["bot"] = _bot_card(found_bot)
    else:
        # A skill, routine or single request can still run on a published Bot later, but only name one
        # when the match is more than a single shared word.
        found_bot, score = catalog.best_bot(text)
        out["bot"] = _bot_card(found_bot) if score >= 2 else None
    return out


def _ask_text(rec: dict[str, Any], answers: dict[str, Any]) -> str:
    """The message sent in a new chat when you press the answer's button."""
    result = fit.clean_inline(answers["result"])
    if rec["kind"] == "assignment":
        return result
    if rec["kind"] == "skill":
        return ("Help me save this as a reusable skill (a SKILL.md file) that you can follow each time I ask.\n\n"
                f"The job: {result}\n\nAsk me what you need to know first. Then draft the steps, the quality check, "
                "and what you must ask me before doing. Test it with me on one small example before saving it.")
    if rec["kind"] == "routine":
        c = rec["routineControls"]
        return ("Help me set up this routine. Run it once now, on demand, so I can check the result before we turn "
                f"the schedule on.\n\nThe job: {result}\nWhen it runs: {c['trigger']}\nIf it fails: {c['failureResponse']}\n"
                f"Cost limit: {c['costLimit']}\nHow to stop it: {c['shutdownMethod']}")
    return result


def _next_step(rec: dict[str, Any], answers: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    kind = rec["kind"]
    if kind == "assignment":
        return {"action": "chat", "label": "Ask Hermes now", "draft": _ask_text(rec, answers),
                "note": "Opens a new chat and sends your words to Hermes"}
    if kind == "skill":
        return {"action": "chat", "label": "Draft the skill with Hermes", "draft": _ask_text(rec, answers),
                "note": "Opens a new chat and asks Hermes to draft the skill with you"}
    if kind == "routine":
        return {"action": "chat", "label": "Test it once with Hermes", "draft": _ask_text(rec, answers),
                "note": "Opens a new chat that runs it once by hand. Turn on the schedule in Scheduled jobs once you trust the result"}
    if kind == "bot":
        return {"action": "lab", "label": "Plan this Bot in Bot Lab",
                "note": "Starts a plan from your answers. You review every field before anything is made"}
    if match.get("crew"):
        return {"action": "crew", "label": "See the crew plan", "note": "Who does what, in what order, and where it stops for you"}
    return {"action": "crews", "label": "Browse the Crew Kits",
            "note": "No published crew is close to this job. Start from the nearest kit, or plan each bot on its own in Bot Lab"}


def fit_answer(body: dict[str, Any]) -> dict[str, Any]:
    try:
        answers = fit.normalize(body or {})
    except fit.AnswerError as exc:
        raise ApiError(422, {"message": str(exc), "missing": exc.missing}) from exc
    rec = fit.recommend(answers)
    kind = rec["kind"]
    match = _match(kind, answers["result"])
    rows = fit.trace(answers, rec["decision"])
    file_name, _ = fit.ARTIFACTS[kind]
    return {
        "result": fit.clean_inline(answers["result"]),
        "kind": kind,
        "title": fit.KINDS[kind]["title"],
        "meaning": fit.KINDS[kind]["meaning"],
        "summary": _title(rec["summary"]),
        "why": rec["why"],
        "rule": rec["decision"]["rule"],
        "answers": rows,
        "dependedOn": [r for r in rows if r["mattered"]],
        "notUsed": [r for r in rows if not r["mattered"] and r["id"] in fit.BRANCH_QUESTIONS],
        "graduation": [_title(s) for s in rec.get("graduation", [])],
        "controls": rec.get("operatingControls"),
        "firstTest": _title(rec["firstTest"]),
        "doneCheck": [_title(s) for s in rec["doneCheck"]],
        "owns": rec["owns"], "excludes": rec["excludes"], "access": [_title(x) for x in rec["access"]],
        "approvals": [_title(x) for x in rec["approvals"]],
        "nextAction": rec["nextAction"],
        "next": _next_step(rec, answers, match),
        "match": match,
        "workshopDraft": rec.get("workshopDraft"),
        "kinds": [{"kind": k, "title": fit.KINDS[k]["title"], "meaning": fit.KINDS[k]["meaning"], "active": k == kind}
                  for k in fit.KIND_ORDER],
        "learnMore": LEARN_MORE[kind],
        "download": {"fileName": file_name, "markdown": fit.to_markdown(rec)},
        "credit": FIT_TEST_CREDIT,
        "submitted": {k: answers[k] for k in (*fit.TEXT_FIELDS, *fit.BRANCH_QUESTIONS)},
    }


def crew_plan(slug: str) -> dict[str, Any]:
    card = crew_card(catalog.crew(slug))
    if not card:
        raise ApiError(404, "No crew by that name")
    card["learnMore"] = LEARN_MORE["crew"]
    return card


# ---------------------------------------------------------------- Bot Lab --

LAB_FIELDS: tuple[tuple[str, str, str], ...] = (
    # (field, plain label, what it's for)
    ("botName", "Name", "What you'll call it in your Bots list"),
    ("jobOutcome", "Its job", "The one result it owns"),
    ("inputsContext", "You give it", "What it needs from you each time"),
    ("outputsDeliverables", "You get back", "What it hands you"),
    ("cadenceTrigger", "When it works", "When you ask, or on a schedule after it passes its first test"),
    ("toolsIntegrations", "What it may use", "Files, sites or tools it's allowed to touch"),
    ("approvalBoundaries", "Asks you before", "Anything it must stop and check with you first"),
    ("firstRunTest", "First test", "A small job that proves it works"),
    ("prohibitedUncertainty", "When it's unsure", "What it does when information is missing"),
    ("continuityMemory", "What it remembers", "What it keeps between chats"),
)
LAB_REQUIRED = fit.WORKSHOP_REQUIRED


def _profile_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", str(name or "").casefold()).strip("-_")
    return slug[:64] or "new-bot"


def lab_start(body: dict[str, Any]) -> dict[str, Any]:
    """A draft plan from one sentence, or from a Fit Test answer. No model is used: the fields are the
    site's standard wording around your own words, and each one says which it is."""
    body = body or {}
    sentence = fit.clean_inline(body.get("job"))
    if not sentence:
        raise ApiError(422, {"message": "Describe the job in a sentence first.", "missing": ["job"]})
    answers = dict(fit.EMPTY_ANSWERS)
    answers.update({"result": sentence, "frequency": "repeat", "needsContinuingContext": True,
                    "overlapsExistingRole": "no"})
    for key in ("frequency", "access", "approvals", "trigger", "failurePlan", "costLimit", "shutdown"):
        if isinstance(body.get(key), str) and body.get(key):
            answers[key] = body[key]
    rec = fit.recommend({**answers, "override": "bot"})
    draft = rec["workshopDraft"]
    yours = {"jobOutcome", "outputsDeliverables"} | ({"inputsContext", "toolsIntegrations"} if answers["access"] else set()) \
        | ({"approvalBoundaries"} if answers["approvals"] else set()) \
        | ({"cadenceTrigger"} if answers["frequency"] == "scheduled" and answers["trigger"] else set())
    fields = []
    for key, label, purpose in LAB_FIELDS:
        value = draft.get(key, "")
        if key == "firstRunTest":
            value = _title(value)
        source = "yours" if key in yours else ("placeholder" if key == "botName" else "standard")
        fields.append({"id": key, "label": label, "purpose": purpose, "value": value, "source": source,
                       "required": key in LAB_REQUIRED})
    closest, score = catalog.best_bot(sentence)
    return {"job": sentence, "fields": fields, "closest": _bot_card(closest) if score >= 2 else None,
            "learnMore": LEARN_MORE["bot"],
            "hermesRequest": lab_hermes_request(sentence)}


def lab_hermes_request(sentence: str) -> str:
    keys = ", ".join(label for _, label, _ in LAB_FIELDS)
    return ("Help me plan a new Bot. Ask me anything you need to know first, then give me a plan with one short "
            f"line for each of these: {keys}.\n\nThe job: {sentence}\n\nKeep it to one job. Name what it must ask me "
            "before doing, and a small first test that proves it works.")


def soul_text(plan: dict[str, str]) -> str:
    """The instructions the new Bot follows (its SOUL.md), written from the plan you accepted."""
    def line(key: str) -> str:
        return fit.clean_inline(plan.get(key, ""))
    parts = [f"# {line('botName')}", "", f"You are {line('botName')}. You have one job: {line('jobOutcome')}", ""]
    rows = [("What you are given", "inputsContext"), ("What you hand back", "outputsDeliverables"),
            ("When you work", "cadenceTrigger"), ("What you may use", "toolsIntegrations"),
            ("Ask before", "approvalBoundaries"), ("When you are unsure", "prohibitedUncertainty"),
            ("What you remember", "continuityMemory"), ("Your first test", "firstRunTest")]
    for heading, key in rows:
        if line(key):
            parts += [f"## {heading}", "", str(plan.get(key, "")).strip(), ""]
    parts += ["## Limits", "", "Do only this job. Never bypass an approval, an access limit, or an instruction to stop.", ""]
    return "\n".join(parts)


def lab_plan(body: dict[str, Any]) -> dict[str, Any]:
    """Check an accepted plan and show exactly what Create would make. Writes nothing. When it is ready the
    plan is also kept (``planId``) so the profile Hermes makes can be recorded and undone from My bots."""
    body = body or {}
    if safety.body_size(body) > safety.MAX_BODY_BYTES:
        raise ApiError(413, "That plan is too long.")
    plan = body.get("fields")
    if not isinstance(plan, dict):
        raise ApiError(422, {"message": "Send the plan's fields.", "missing": list(LAB_REQUIRED)})
    plan = {k: str(v) for k, v in plan.items() if k in fit.WORKSHOP_FIELDS and isinstance(v, str)}
    missing = [k for k in LAB_REQUIRED if not fit.clean_inline(plan.get(k))]
    name = fit.clean_inline(plan.get("botName"))
    if name.casefold() == "new bot":
        missing.append("botName")
    labels = {k: label for k, label, _ in LAB_FIELDS}
    typed = body.get("profile") if isinstance(body.get("profile"), str) and body.get("profile").strip() else None
    profile = typed.strip().lower() if typed else _profile_name(name)
    description = _title(plan.get("jobOutcome", ""))[:200]
    soul = soul_text(plan)
    out: dict[str, Any] = {
        "ready": not missing,
        "missing": [{"id": k, "label": labels.get(k, k)} for k in dict.fromkeys(missing)],
        "profile": profile,
        "reserved": profile in safety.RESERVED,
        "description": description,
        "soul": soul,
        "problems": [], "planId": None, "where": None, "suggestion": None, "signInNote": None, "modelNote": None,
        # The exact gateway call. No model is passed: Hermes copies your current model, and any change
        # goes through profiles.configure and its cost guard (fact-check F5).
        "create": {"method": "profiles.create",
                   "params": {"name": profile, "description": description, "soul": soul, "no_alias": True, "no_skills": True}},
    }
    if not missing:
        checked = installer.plan_lab(display=name, description=description, soul=soul, profile=profile, job=fit.clean_inline(plan.get("jobOutcome", "")))
        out.update({"ready": checked["ready"], "problems": checked["problems"], "planId": checked["planId"], "where": checked["where"],
                    "suggestion": checked["suggestion"], "profile": checked["profile"] or profile, "soul": checked["soul"] or soul,
                    "description": checked["description"] or description, "signInNote": checked["signInNote"],
                    "modelNote": checked["modelNote"], "create": checked["create"] or out["create"]})
        if not checked["ready"]:
            out["create"] = None
    else:
        out["create"] = None
    return out


# ---------------------------------------------------------------- the live catalog --

def _bot_for_page(b: dict[str, Any]) -> dict[str, Any]:
    keep = ("slug", "name", "title", "category", "summary", "audience", "outcome", "inputs", "outputs", "examples", "firstMission",
            "approvals", "offLimits", "worksWith", "riskLevel", "credits", "href", "test", "routine")
    out = {k: b[k] for k in keep}
    out["skills"] = [s["name"] for s in b.get("skills", [])]
    return out


def catalog_view() -> dict[str, Any]:
    """What the page draws on: the Bots and Crew Kits of the catalog in use, and where that catalog came from."""
    doc = catalog.load()
    # hermesAdded: Cabinet's Bots that Hermes's own Bots catalog already added (slug -> profile), so no second copy is offered.
    return {"status": sync.status(), "bots": [_bot_for_page(b) for b in doc["bots"]], "crews": doc["crews"],
            "handover": hermes_bridge.detect_core_catalog(),
            "hermesAdded": {b["slug"]: b["profile"] for b in installer.hermes_bots_added()},
            "installer": {"hermes": hermes_bridge.available(), "kanban": hermes_bridge.kanban_available()}}


def catalog_refresh() -> dict[str, Any]:
    sync.refresh()
    return catalog_view()


# ---------------------------------------------------------------- adding, setting up, undoing --

def _guard_body(body: Any) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    if safety.body_size(body) > safety.MAX_BODY_BYTES:
        raise ApiError(413, {"message": "That's too much text."})
    return body


def bot_plan(slug: str, body: dict[str, Any]) -> dict[str, Any]:
    sync.status()  # make sure the catalog in use is loaded
    return installer.plan_bot(slug, _guard_body(body).get("edits"))


def kit_plan(kit_id: int, body: dict[str, Any]) -> dict[str, Any]:
    body = _guard_body(body)
    kit = installer.store().get_kit(int(kit_id))
    if not kit or kit["status"] != "active":
        raise ApiError(404, {"message": "Cabinet has no record of that kit."})
    fields = kit["fields"]
    name = body.get("displayName") if isinstance(body.get("displayName"), str) else fields.get("botName", kit["name"])
    profile = body.get("profile") if isinstance(body.get("profile"), str) and body.get("profile") else None
    return installer.plan_lab(display=name, description=_title(fields.get("jobOutcome", ""))[:200], soul=soul_text(fields),
                              profile=profile, job=kit["job"] or "")


def kit_save(body: dict[str, Any]) -> dict[str, Any]:
    body = _guard_body(body)
    fields = body.get("fields")
    if not isinstance(fields, dict):
        raise ApiError(422, {"message": "Send the plan's fields."})
    fields = {k: v for k, v in fields.items() if k in fit.WORKSHOP_FIELDS and isinstance(v, str)}
    name = fit.clean_inline(fields.get("botName"))
    if not name or name.casefold() == "new bot":
        raise ApiError(422, {"message": "Give the plan a name before saving it as a kit."})
    return installer.save_kit(name, fields, fit.clean_inline(body.get("job") or fields.get("jobOutcome", "")))


def install_confirm(body: dict[str, Any]) -> dict[str, Any]:
    body = _guard_body(body)
    # created: did the page's profiles.create call succeed? Only an explicit true lets Cabinet repair what Hermes made.
    return installer.confirm(str(body.get("planId") or ""), created=body.get("created") is True)


def crew_setup(slug: str, body: dict[str, Any]) -> dict[str, Any]:
    sync.status()
    return installer.plan_crew(slug, _guard_body(body).get("choices"))


def crew_confirm(body: dict[str, Any]) -> dict[str, Any]:
    body = _guard_body(body)
    created = body.get("created")
    return installer.confirm_crew(str(body.get("planId") or ""), created if isinstance(created, list) else [])


def crew_cards_plan(crew_id: int) -> dict[str, Any]:
    return installer.cards_plan(int(crew_id))


def crew_cards_create(crew_id: int, body: dict[str, Any]) -> dict[str, Any]:
    return installer.create_cards(int(crew_id), _guard_body(body).get("confirm"))


def my_bots() -> dict[str, Any]:
    return installer.my_bots()


def remove_preview(kind: str, item_id: int) -> dict[str, Any]:
    return installer.removal_preview(kind, int(item_id))


def remove(kind: str, item_id: int, body: dict[str, Any]) -> dict[str, Any]:
    return installer.remove(kind, int(item_id), _guard_body(body).get("confirm"))


def forget(install_id: int) -> dict[str, Any]:
    return installer.forget(int(install_id))
