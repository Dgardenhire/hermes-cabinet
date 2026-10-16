"""The Fit Test: turn a job into the smallest setup that does it.

A port of Bot Cabinet's Bot Fit Test (botcabinet.com, ``src/lib/bot-fit-test.ts`` at site commit
0e914a7, by Damon Gardenhire, MIT). The decision order and the routine readiness gate are the site's
own. The summaries, reasons, first tests, done checks and readiness steps shown on the page were
rewritten in plain English on Oct 16, 2026; the downloadable markdown keeps the site's other wording.
Two things are added here:

- ``RULES``: the decision order written as a table, one row per rule, each naming the questions it
  reads. ``decide`` walks the table and records every question it actually looked at, so the page can
  show each answer next to the question it came from and say how it changed the result.
- ``KINDS``: a plain name and a one-line meaning for each of the five outcomes (DESIGN.md S2).

Pure Python, standard library only. No Hermes, no network, no model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

KIND_ORDER = ("assignment", "skill", "routine", "bot", "crew")

KINDS: dict[str, dict[str, str]] = {
    "assignment": {"title": "Ask Hermes once", "meaning": "A single request in a chat", "label": "Assignment"},
    "skill": {"title": "Save it as a skill", "meaning": "Steps Hermes follows each time you ask", "label": "Skill"},
    "routine": {"title": "Run it on a schedule", "meaning": "A Bot's job that runs at a set time", "label": "Routine"},
    "bot": {"title": "One Bot", "meaning": "One specialist with its own ongoing chat", "label": "Bot"},
    "crew": {"title": "A crew", "meaning": "Several Bots, each with its own job, passing work in order", "label": "Crew"},
}

# ---------------------------------------------------------------- questions --

@dataclass(frozen=True)
class Question:
    id: str
    text: str
    choices: tuple[tuple[Any, str], ...] = ()   # (value, plain label); empty for free text
    required: bool = True
    help: str = ""


QUESTIONS: tuple[Question, ...] = (
    Question("result", "What do you need done?", help="Describe the result you want, in a sentence"),
    Question("frequency", "How often will you need it?",
             (("once", "Once"), ("repeat", "Again and again, when I ask"), ("scheduled", "On a schedule"))),
    Question("needsContinuingContext", "Should it remember past work?", ((True, "Yes"), (False, "No"))),
    Question("needsMultipleSpecialists", "Does it take more than one specialist?", ((True, "Yes"), (False, "No"))),
    Question("workProvenManually", "Have you done it successfully by hand at least once?", ((True, "Yes"), (False, "No"))),
    Question("overlapsExistingRole", "Do you already have a Bot for this?",
             (("yes", "Yes"), ("no", "No"), ("unsure", "Not sure"))),
    # Optional details. They never change which outcome you get, except that a schedule needs all
    # four controls (trigger, failure plan, cost limit, shutdown) before it can be a routine.
    Question("trigger", "When should it start?", required=False, help="For example, every weekday at 7 a.m."),
    Question("failurePlan", "What should happen if it fails?", required=False, help="For example, stop and tell me what went wrong"),
    Question("costLimit", "What is the most it may spend each time?", required=False, help="For example, $1 per run"),
    Question("shutdown", "How will you stop it?", required=False, help="For example, turn off the schedule in Hermes"),
    Question("access", "What may it use?", required=False, help="One item per line. Leave blank for the minimum"),
    Question("approvals", "What must it ask you before doing?", required=False, help="One item per line"),
)
QUESTION_BY_ID = {q.id: q for q in QUESTIONS}
BRANCH_QUESTIONS = ("frequency", "needsContinuingContext", "needsMultipleSpecialists", "workProvenManually",
                    "overlapsExistingRole")
TEXT_FIELDS = ("result", "access", "approvals", "trigger", "failurePlan", "costLimit", "shutdown")
ROUTINE_CONTROLS = ("trigger", "failurePlan", "costLimit", "shutdown")

EMPTY_ANSWERS: dict[str, Any] = {
    "result": "", "frequency": "once", "needsContinuingContext": False, "needsMultipleSpecialists": False,
    "workProvenManually": False, "overlapsExistingRole": "unsure", "access": "", "approvals": "", "trigger": "",
    "failurePlan": "", "costLimit": "", "shutdown": "", "override": "auto",
}


class AnswerError(ValueError):
    """An answer is missing or isn't one of the question's choices. ``missing`` names the questions."""

    def __init__(self, message: str, missing: list[str] | None = None):
        super().__init__(message)
        self.missing = missing or []


def clean_inline(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def split_planning_items(value: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    for item in re.split(r"\n+|;", text):
        item = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", item).strip()
        if item and item.casefold() not in seen:
            seen.add(item.casefold())
            out.append(item)
    return out


def is_complete(answers: dict[str, Any]) -> bool:
    return not missing_answers(answers)


def missing_answers(answers: dict[str, Any]) -> list[str]:
    """Questions still unanswered. An explicit No counts as an answer; a skipped question doesn't."""
    missing = []
    if not clean_inline(answers.get("result")):
        missing.append("result")
    for qid in BRANCH_QUESTIONS:
        allowed = [value for value, _ in QUESTION_BY_ID[qid].choices]
        value = answers.get(qid)
        if value is None or not any(value is a or (type(value) is type(a) and value == a) for a in allowed):
            missing.append(qid)
    return missing


def normalize(raw: dict[str, Any], *, require_all: bool = True) -> dict[str, Any]:
    """Validate a request body into a full answer set. Raises AnswerError naming what's missing."""
    if not isinstance(raw, dict):
        raise AnswerError("Send the answers as an object.")
    answers = dict(EMPTY_ANSWERS)
    for key in TEXT_FIELDS:
        value = raw.get(key, "")
        if value is not None and not isinstance(value, str):
            raise AnswerError(f"{QUESTION_BY_ID[key].text} needs text.", [key])
        answers[key] = (value or "")[:4000]
    for key in BRANCH_QUESTIONS:
        answers[key] = raw.get(key)
    override = raw.get("override", "auto") or "auto"
    if override != "auto" and override not in KINDS:
        raise AnswerError("That isn't one of the five setups.", ["override"])
    answers["override"] = override
    if require_all:
        missing = missing_answers(answers)
        if missing:
            names = ", ".join(QUESTION_BY_ID[m].text for m in missing)
            raise AnswerError(f"Answer every question first: {names}", missing)
    return answers


def answer_label(qid: str, value: Any) -> str:
    q = QUESTION_BY_ID[qid]
    for choice, label in q.choices:
        if value is choice or (type(value) is type(choice) and value == choice):
            return label
    return clean_inline(value) or "Not given"


# ---------------------------------------------------------------- rules --

@dataclass(frozen=True)
class Condition:
    question: str
    test: Callable[[dict[str, Any]], bool]
    yes: str   # how this answer mattered when the condition holds
    no: str    # how it mattered when it doesn't


def _controls_ready(a: dict[str, Any]) -> bool:
    return not routine_graduation(a)


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str
    plain: str                     # the rule, in one plain sentence
    conditions: tuple[Condition, ...] = field(default_factory=tuple)
    graduation: bool = False       # the result carries the steps still needed before a schedule


RECURRING = ("repeat", "scheduled")

# Deliberately ordered (site comment): one recommendation should identify the smallest form that can
# own the work without hiding real handoffs. The first rule whose conditions all hold wins.
RULES: tuple[Rule, ...] = (
    Rule("crew", "crew", "If the job takes more than one specialist, it's a crew",
         (Condition("needsMultipleSpecialists", lambda a: a["needsMultipleSpecialists"] is True,
                    "More than one specialist, so the work is split across a crew",
                    "One specialist is enough, so it isn't a crew"),)),
    Rule("bot", "bot", "If it repeats, should remember past work, and no Bot of yours already does it, it's one Bot",
         (Condition("frequency", lambda a: a["frequency"] in RECURRING,
                    "It repeats, so a lasting Bot can pay off", "Needed once, so a lasting Bot isn't worth it"),
          Condition("needsContinuingContext", lambda a: a["needsContinuingContext"] is True,
                    "It should remember past work, which a Bot's ongoing chat does",
                    "It doesn't need to remember past work, so it doesn't need its own Bot"),
          Condition("overlapsExistingRole", lambda a: a["overlapsExistingRole"] == "no",
                    "None of your Bots does this yet, so a new one won't duplicate a role",
                    "You may already have a Bot for this, so the method belongs with that Bot"))),
    Rule("routine", "routine", "If it runs on a schedule and is ready to run unattended, it's a routine",
         (Condition("frequency", lambda a: a["frequency"] == "scheduled",
                    "It runs on a schedule", "It doesn't run on a schedule"),
          Condition("workProvenManually", lambda a: a["workProvenManually"] is True,
                    "You've done it by hand, so the method is proven",
                    "It hasn't worked by hand yet, so it isn't ready to run on its own"),
          Condition("trigger", _controls_ready,
                    "Its start time, failure plan, cost limit and shutdown are all set",
                    "Its start time, failure plan, cost limit or shutdown is still missing"))),
    Rule("schedule-not-ready", "skill", "If it runs on a schedule but isn't ready yet, prove it as a skill first",
         (Condition("frequency", lambda a: a["frequency"] == "scheduled",
                    "It runs on a schedule, but isn't ready to run on its own yet", "It doesn't run on a schedule"),),
         graduation=True),
    Rule("repeat", "skill", "If it repeats when you ask, save the steps as a skill",
         (Condition("frequency", lambda a: a["frequency"] == "repeat",
                    "It repeats when you ask, so saved steps help each time", "It doesn't repeat"),)),
    Rule("once", "assignment", "Anything else is a single request", ()),
)


def routine_graduation(a: dict[str, Any]) -> list[str]:
    steps = []
    if not a.get("workProvenManually"):
        steps.append("Do it by hand and get a good result at least once.")
    if not clean_inline(a.get("trigger")):
        steps.append("Say what starts it: a time or an event.")
    if not clean_inline(a.get("failurePlan")):
        steps.append("Say what should happen if it fails.")
    if not clean_inline(a.get("costLimit")):
        steps.append("Set the most it may spend each time.")
    if not clean_inline(a.get("shutdown")):
        steps.append("Say how you will stop it.")
    return steps


@dataclass
class Decision:
    kind: str
    rule: str
    graduation: list[str] | None
    checks: list[dict[str, Any]]   # every condition evaluated, in order


def decide(answers: dict[str, Any]) -> Decision:
    """Walk RULES in order. Records each condition looked at, so every answer that mattered is shown."""
    if answers.get("override", "auto") != "auto":
        kind = answers["override"]
        check = {"rule": "override", "question": "override", "held": True,
                 "effect": f"You chose {KINDS[kind]['title'].lower()} yourself"}
        if kind == "routine":
            grad = routine_graduation(answers)
            if grad:
                check["effect"] = "You chose a schedule, but it isn't ready to run on its own yet"
                return Decision("skill", "override", grad, [check])
        return Decision(kind, "override", None, [check])

    checks: list[dict[str, Any]] = []
    for rule in RULES:
        fired = True
        for cond in rule.conditions:
            held = bool(cond.test(answers))
            checks.append({"rule": rule.id, "question": cond.question, "held": held,
                           "effect": cond.yes if held else cond.no})
            if not held:
                fired = False
                break
        if fired:
            grad = routine_graduation(answers) if rule.graduation else None
            return Decision(rule.kind, rule.id, grad or None, checks)
    raise AssertionError("the last rule always fires")  # pragma: no cover


# ---------------------------------------------------------------- the site's wording --

def first_test(kind: str, result: str, has_result: bool) -> str:
    if not has_result:
        return "Write down one concrete result, then ask Hermes for a small version of it once."
    return {
        "assignment": f"Ask for a small version first: {result}",
        "skill": f"Do it by hand on one small example: {result}",
        "routine": f"Run it once by hand before you turn on its schedule: {result}",
        "bot": f"Give the Bot a small sample of the work and check what it sends back: {result}",
        "crew": f"Give the crew a small sample and check every handoff: {result}",
    }[kind]


SHARED_DONE = [
    "You got the result you asked for, and you can use it.",
    "It used only the information and access you allowed.",
    "It asked you before every action on the approval list.",
]
DONE_EXTRA = {
    "assignment": "You have checked the result, and the job is over.",
    "skill": "The same steps and check work on a second example.",
    "routine": "The start time, failure plan, cost limit and shutdown all work as written.",
    "bot": "Each new request in its chat builds on the last, and the job stays the same size.",
    "crew": "Each Bot does its part and passes the right material to the next.",
}


def done_checks(kind: str) -> list[str]:
    return [*SHARED_DONE, DONE_EXTRA[kind]]


def operating_controls(a: dict[str, Any]) -> dict[str, str] | None:
    controls = {"trigger": clean_inline(a.get("trigger")), "failureResponse": clean_inline(a.get("failurePlan")),
                "costLimit": clean_inline(a.get("costLimit")), "shutdownMethod": clean_inline(a.get("shutdown"))}
    return controls if any(controls.values()) else None


WORKSHOP_FIELDS = ("botName", "jobOutcome", "inputsContext", "outputsDeliverables", "cadenceTrigger",
                   "toolsIntegrations", "approvalBoundaries", "firstRunTest", "audienceSuccess", "accessSensitive",
                   "prohibitedUncertainty", "continuityMemory", "reviewCriteria", "profileTitle",
                   "profileDescription", "roleInstructions")
WORKSHOP_REQUIRED = WORKSHOP_FIELDS[:8]


def workshop_draft(a: dict[str, Any], test: str, controls: dict[str, str] | None) -> dict[str, str]:
    """The site's no-model Bot Lab starting point (``workshopDraft``): your sentence plus standard wording."""
    access = str(a.get("access") or "").strip()
    approvals = str(a.get("approvals") or "").strip()
    if a["frequency"] == "scheduled":
        cadence = clean_inline(a.get("trigger")) or "Set a schedule once the first test works."
    elif a["frequency"] == "repeat":
        cadence = "When you ask it, in its chat."
    else:
        cadence = "When you give it the work."
    review = ["Check the result against the requested outcome, source material, access limits, and approval rules."]
    if controls:
        review += ["Automation controls:",
                   f"- Trigger: {controls['trigger'] or 'Not defined'}",
                   f"- Failure response: {controls['failureResponse'] or 'Not defined'}",
                   f"- Cost limit: {controls['costLimit'] or 'Not defined'}",
                   f"- Shutdown: {controls['shutdownMethod'] or 'Not defined'}"]
    draft = {key: "" for key in WORKSHOP_FIELDS}
    draft.update({
        "botName": "New Bot",
        "jobOutcome": clean_inline(a.get("result")),
        "inputsContext": access or "The information, examples and rules you give it for the first test.",
        "outputsDeliverables": clean_inline(a.get("result")) or "A draft result for review.",
        "cadenceTrigger": cadence,
        "toolsIntegrations": access or "Only what you give it in its chat.",
        "approvalBoundaries": approvals or "Sending, publishing, buying or deleting anything, or changing another system.",
        "firstRunTest": test,
        "audienceSuccess": "The person who requested the work confirms that the result is accurate, useful, and complete.",
        "accessSensitive": "Begin with sample or low-risk material and grant only the access required for the job.",
        "prohibitedUncertainty": "Stop when information is missing, sources disagree, or it needs your approval. Say what the problem is and ask what to do.",
        "continuityMemory": "Your confirmed preferences and decisions, and useful context about this job from earlier chats.",
        "reviewCriteria": "\n".join(review),
    })
    return draft


def _overlap_note(a: dict[str, Any]) -> str:
    if a["overlapsExistingRole"] == "yes":
        return " You already have a Bot for this, so save the steps with that Bot."
    if a["overlapsExistingRole"] == "unsure":
        return " Check for overlap with your Bots before you make a new one."
    return ""


def recommend(answers: dict[str, Any]) -> dict[str, Any]:
    """The full recommendation, with the site's wording. ``answers`` must already be normalized."""
    decision = decide(answers)
    kind = decision.kind
    has_result = bool(clean_inline(answers.get("result")))
    result = clean_inline(answers.get("result")) or "Define one concrete result before starting."
    test = first_test(kind, result, has_result)
    overlap = _overlap_note(answers)
    access = split_planning_items(answers.get("access")) or ["Only the information and tools needed for the first test."]
    approvals = split_planning_items(answers.get("approvals")) or [
        "Sending or publishing anything", "Buying anything", "Deleting anything", "Changing another system"]
    rec: dict[str, Any] = {"kind": kind, "label": KINDS[kind]["label"], "owns": [result], "access": access,
                           "approvals": approvals, "firstTest": test, "doneCheck": done_checks(kind)}
    if kind == "assignment":
        rec.update(summary="Ask Hermes once",
                   why="You need one result, and the job is over once you have checked it.",
                   excludes=["Ongoing ownership of nearby work",
                             "A continuing identity, schedule, or separate body of context"],
                   nextAction="Write the assignment brief and run it once.")
    elif kind == "skill":
        proof = bool(decision.graduation)
        rec.update(summary="Save it as a skill and test it by hand before scheduling it" if proof else "Save the steps as a skill you can reuse",
                   why=(f"This should run on a schedule, but the steps and its schedule details aren't ready yet.{overlap}"
                        if proof else
                        f"You will do this again, so saved steps help each time. A Bot with its own chat would add little.{overlap}"),
                   owns=[result, "The repeatable steps and quality standard for producing the result"],
                   excludes=["A duplicate Bot role or separate continuing conversation",
                             "Automatic scheduling until the method and operating controls pass a manual test"],
                   nextAction="Draft a SKILL.md file and test it on one real example.")
        if decision.graduation:
            rec["graduation"] = decision.graduation
    elif kind == "routine":
        controls = operating_controls(answers) or {}
        rec.update(summary="Put the tested steps on a schedule",
                   why="It has worked by hand, and its start time, failure plan, cost limit and shutdown are all set.",
                   owns=[result, f"Trigger: {controls.get('trigger', '')}"],
                   excludes=["Work outside the tested method",
                             "Changes to the trigger, access, or limits without a new review"],
                   nextAction="Create the routine plan, run it once on demand, and then enable the trigger.",
                   routineControls=controls, operatingControls=controls)
    elif kind == "bot":
        controls = operating_controls(answers)
        rec.update(summary="Give this job to a Bot of its own",
                   why=f"The job repeats and goes better with its own chat, instructions and memory of past work.{overlap}",
                   excludes=["Nearby work that belongs to another role",
                             "Automatic schedules that have not passed a manual test"],
                   nextAction="Open the prefilled Bot Lab Blueprint and review every field.",
                   workshopDraft=workshop_draft(answers, test, controls))
        if controls:
            rec["operatingControls"] = controls
    else:
        rec.update(summary="Split the job across a crew, with a clear handoff at each step",
                   why="Different specialists each do one part of the job and pass their work to the next.",
                   owns=[result, "The order of work, handoff requirements, and final human review"],
                   excludes=["Extra roles that do not produce a distinct result",
                             "Unsupervised handoffs without a named output and acceptance check"],
                   nextAction="Browse the Crew Kits and choose the closest starting plan. Edit each job, handoff, and approval point.")
    rec["decision"] = {"rule": decision.rule, "checks": decision.checks}
    return rec


def trace(answers: dict[str, Any], decision: dict[str, Any]) -> list[dict[str, Any]]:
    """Every question with your answer, whether the result depended on it, and how.

    One row per question in QUESTIONS order (blank optional details are left out). A question the
    winning rule read carries that rule's reason; any other question the rules looked at carries the
    reason from the first rule it ruled out.
    """
    decision_checks = decision["checks"]
    effects: dict[str, str] = {}
    for check in decision_checks:
        if check["question"] in QUESTION_BY_ID and check["question"] not in effects:
            effects[check["question"]] = check["effect"]
    for check in decision_checks:
        if check["rule"] == decision["rule"] and check["question"] in QUESTION_BY_ID:
            effects[check["question"]] = check["effect"]
    rows = []
    for q in QUESTIONS:
        value = answers.get(q.id)
        if not q.required and not clean_inline(value):
            continue
        rows.append({"id": q.id, "question": q.text, "answer": answer_label(q.id, value),
                     "value": value, "mattered": q.id in effects, "effect": effects.get(q.id, "")})
    if any(c["rule"] == "routine" and c["question"] == "trigger" for c in decision_checks):
        # (The "trigger" check stands for all four routine controls.)
        # The readiness check reads all four controls; mark the ones you filled in as part of it.
        for row in rows:
            if row["id"] in ROUTINE_CONTROLS and not row["mattered"]:
                row["mattered"] = True
                row["effect"] = effects.get("trigger", "")
    return rows


# ---------------------------------------------------------------- download --

ARTIFACTS = {
    "assignment": ("assignment-brief.md", "Assignment brief"),
    "skill": ("SKILL.md", "Reusable skill"),
    "routine": ("routine-plan.md", "Routine plan"),
    "bot": ("bot-blueprint-start.md", "Bot Blueprint starting point"),
    "crew": ("crew-blueprint.md", "Crew blueprint"),
}


def escape_markdown(value: str) -> str:
    return re.sub(r"([\\`*_\[\]<>#+!|])", r"\\\1", value)


def _md_list(items: list[str]) -> str:
    return "\n".join(f"- {escape_markdown(i)}" for i in items)


def _controls_md(c: dict[str, str]) -> list[str]:
    return [f"- **Trigger:** {escape_markdown(c.get('trigger') or 'Not defined')}",
            f"- **Failure response:** {escape_markdown(c.get('failureResponse') or 'Not defined')}",
            f"- **Cost limit:** {escape_markdown(c.get('costLimit') or 'Not defined')}",
            f"- **Shutdown:** {escape_markdown(c.get('shutdownMethod') or 'Not defined')}"]


def to_markdown(rec: dict[str, Any]) -> str:
    kind = rec["kind"]
    specific: list[str]
    if kind == "assignment":
        specific = ["## Assignment steps", "", "1. Supply the approved information and rules.",
                    "2. Produce the requested result as a draft.", "3. Stop at the listed approval points.",
                    "4. End the assignment after a person reviews the result."]
    elif kind == "skill":
        specific = ["## Reusable process", "", "1. Confirm the requested result and approved inputs.",
                    "2. Follow the same steps and quality standard each time.",
                    "3. Return a draft with sources, assumptions, and open questions.",
                    "4. Stop at every approval point.", "5. Record improvements only after a person reviews the result."]
        if rec.get("graduation"):
            specific += ["", "## Before turning this into a Routine", "", _md_list(rec["graduation"])]
    elif kind == "routine":
        specific = ["## Operating controls", "", *_controls_md(rec["routineControls"]), "", "## Activation sequence", "",
                    "1. Run the complete process once on demand.", "2. Review the result and the failure path.",
                    "3. Confirm the cost limit and shutdown method.", "4. Enable the trigger after the review passes."]
    elif kind == "bot":
        specific = ["## Bot Lab handoff", "",
                    "Use this result to prefill Bot Lab. Review the Bot name, job, inputs, output, access, approval rules, and first test before creating a Hermes profile."]
        if rec.get("operatingControls"):
            specific += ["", "## Operating controls", "", *_controls_md(rec["operatingControls"])]
    else:
        specific = ["## Crew handoffs", "", "1. Name each distinct job and its owner.",
                    "2. Define the exact output each role passes forward.",
                    "3. Add an acceptance check for every handoff.", "4. Keep the final release decision with a person."]
    return "\n".join([
        f"# {ARTIFACTS[kind][1]}", "", f"**Best fit:** {rec['label']}", f"**Plan:** {rec['summary']}", "",
        "## Why this fits", "", escape_markdown(rec["why"]), "", "## What this form owns", "", _md_list(rec["owns"]), "",
        "## Keep elsewhere", "", _md_list(rec["excludes"]), "", "## Minimum access", "", _md_list(rec["access"]), "",
        "## Approval points", "", _md_list(rec["approvals"]), "", *specific, "", "## First test", "",
        escape_markdown(rec["firstTest"]), "", "## Done check", "", _md_list(rec["doneCheck"]), "", "## Next action", "",
        escape_markdown(rec["nextAction"]), "", "---", "Created with Bot Cabinet's Bot Fit Test.",
    ])
