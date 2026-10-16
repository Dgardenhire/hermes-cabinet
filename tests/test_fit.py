"""The Fit Test engine. The first class ports the site's own test cases (src/lib/bot-fit-test.test.ts at
0e914a7) one for one; the rest cover the rules table and the answer trace that are new here."""

from __future__ import annotations

import itertools
import unittest

from _load import sub

fit = sub("fit")

JOB = "Turn approved interviews into a concise briefing."


def answers(**overrides):
    a = dict(fit.EMPTY_ANSWERS)
    a["result"] = JOB
    a.update(overrides)
    return a


def rec(**overrides):
    return fit.recommend(answers(**overrides))


class SiteCases(unittest.TestCase):
    def test_assignment_for_a_result_needed_once(self):
        r = rec(frequency="once", needsContinuingContext=False)
        self.assertEqual(r["kind"], "assignment")
        self.assertEqual(r["label"], "Assignment")
        self.assertIn(JOB, r["owns"])
        self.assertNotIn("workshopDraft", r)

    def test_skill_when_the_method_repeats_without_its_own_context(self):
        r = rec(frequency="repeat", needsContinuingContext=False)
        self.assertEqual(r["kind"], "skill")
        self.assertIn("SKILL.md", r["nextAction"])

    def test_ready_routine_only_with_manual_test_and_controls(self):
        r = rec(frequency="scheduled", workProvenManually=True, trigger="Every weekday at 7:00 a.m.",
                failurePlan="Stop and send the owner an error summary.", costLimit="$1 per run.",
                shutdown="Disable the schedule in Hermes.")
        self.assertEqual(r["kind"], "routine")
        self.assertIn("Trigger: Every weekday at 7:00 a.m.", r["owns"])
        self.assertNotIn("graduation", r)

    def test_skill_when_scheduled_work_has_not_succeeded_by_hand(self):
        r = rec(frequency="scheduled", workProvenManually=False, trigger="Every weekday at 7:00 a.m.",
                failurePlan="Stop and notify the owner.", costLimit="$1 per run.", shutdown="Disable the schedule.")
        self.assertEqual(r["kind"], "skill")
        self.assertIn("before scheduling it", r["summary"])
        self.assertIn("Do it by hand and get a good result at least once.", r["graduation"])

    def test_skill_names_every_missing_routine_control(self):
        r = rec(frequency="scheduled", workProvenManually=True)
        self.assertEqual(r["kind"], "skill")
        self.assertEqual(r["graduation"], ["Say what starts it: a time or an event.",
                                           "Say what should happen if it fails.", "Set the most it may spend each time.",
                                           "Say how you will stop it."])

    def test_bot_for_distinct_repeated_work_with_continuing_context(self):
        r = rec(frequency="repeat", needsContinuingContext=True, overlapsExistingRole="no",
                access="Approved interview transcripts\nBrand guide", approvals="Approve every external publication")
        self.assertEqual(r["kind"], "bot")
        d = r["workshopDraft"]
        self.assertEqual(d["jobOutcome"], JOB)
        self.assertEqual(d["inputsContext"], "Approved interview transcripts\nBrand guide")
        self.assertEqual(d["toolsIntegrations"], "Approved interview transcripts\nBrand guide")
        self.assertEqual(d["approvalBoundaries"], "Approve every external publication")
        self.assertIn("small sample", d["firstRunTest"])

    def test_scheduled_bot_keeps_its_controls_everywhere(self):
        r = rec(frequency="scheduled", needsContinuingContext=True, overlapsExistingRole="no",
                trigger="Every weekday at 7:00 a.m.", failurePlan="Stop and send the owner an error summary.",
                costLimit="$3 per run.", shutdown="Disable the schedule after two failures.")
        md = fit.to_markdown(r)
        self.assertEqual(r["kind"], "bot")
        self.assertEqual(r["operatingControls"], {"trigger": "Every weekday at 7:00 a.m.",
                                                  "failureResponse": "Stop and send the owner an error summary.",
                                                  "costLimit": "$3 per run.",
                                                  "shutdownMethod": "Disable the schedule after two failures."})
        for line in ("**Trigger:** Every weekday at 7:00 a.m.", "**Failure response:** Stop and send the owner an error summary.",
                     "**Cost limit:** $3 per run.", "**Shutdown:** Disable the schedule after two failures."):
            self.assertIn(line, md)
        for line in ("- Trigger: Every weekday at 7:00 a.m.", "- Failure response: Stop and send the owner an error summary.",
                     "- Cost limit: $3 per run.", "- Shutdown: Disable the schedule after two failures."):
            self.assertIn(line, r["workshopDraft"]["reviewCriteria"])

    def test_no_duplicate_bot_when_the_job_already_belongs_to_one(self):
        r = rec(frequency="repeat", needsContinuingContext=True, overlapsExistingRole="yes")
        self.assertEqual(r["kind"], "skill")
        self.assertIn("already have a Bot for this", r["why"])
        self.assertIn("duplicate", " ".join(r["excludes"]))

    def test_skill_while_overlap_is_unresolved(self):
        r = rec(frequency="repeat", needsContinuingContext=True, overlapsExistingRole="unsure")
        self.assertEqual(r["kind"], "skill")
        self.assertIn("Check for overlap", r["why"])

    def test_crew_when_distinct_roles_hand_work_on(self):
        r = rec(frequency="repeat", needsContinuingContext=True, needsMultipleSpecialists=True)
        self.assertEqual(r["kind"], "crew")
        self.assertIn("Crew", r["nextAction"])

    def test_documented_precedence(self):
        common = dict(frequency="scheduled", needsContinuingContext=True, overlapsExistingRole="no",
                      workProvenManually=True, trigger="Every morning.", failurePlan="Stop.", costLimit="$1.",
                      shutdown="Disable it.")
        self.assertEqual(rec(**common, needsMultipleSpecialists=True)["kind"], "crew")
        self.assertEqual(rec(**common)["kind"], "bot")
        self.assertEqual(rec(**{**common, "needsContinuingContext": False})["kind"], "routine")
        self.assertEqual(rec(**{**common, "frequency": "repeat", "needsContinuingContext": False, "trigger": ""})["kind"], "skill")
        self.assertEqual(rec(**{**common, "frequency": "once", "needsContinuingContext": False})["kind"], "assignment")

    def test_explicit_choice_keeps_the_routine_gate(self):
        self.assertEqual(rec(frequency="once", override="bot", needsContinuingContext=False)["kind"], "bot")
        unready = rec(frequency="scheduled", override="routine")
        self.assertEqual(unready["kind"], "skill")
        self.assertTrue(unready["graduation"])

    def test_blank_answers_invent_no_job(self):
        r = fit.recommend(dict(fit.EMPTY_ANSWERS))
        self.assertEqual(r["kind"], "assignment")
        self.assertEqual(r["owns"], ["Define one concrete result before starting."])
        self.assertIn("Write down one concrete result", r["firstTest"])


class FormCompleteness(unittest.TestCase):
    def test_a_skipped_branch_answer_blocks_the_form(self):
        for key in fit.BRANCH_QUESTIONS:
            with self.subTest(key=key):
                self.assertFalse(fit.is_complete({**answers(), key: None}))

    def test_explicit_no_and_not_sure_count_as_answers(self):
        self.assertTrue(fit.is_complete(answers()))

    def test_whitespace_job_is_not_an_answer(self):
        self.assertFalse(fit.is_complete(answers(result="  \n ")))

    def test_every_frequency_is_accepted(self):
        for f in ("once", "repeat", "scheduled"):
            self.assertTrue(fit.is_complete(answers(frequency=f)))

    def test_truthy_lookalikes_are_not_answers(self):
        for bad in (1, 0, "true", "yes", ""):
            with self.subTest(bad=bad):
                self.assertIn("needsContinuingContext", fit.missing_answers(answers(needsContinuingContext=bad)))

    def test_normalize_names_what_is_missing(self):
        with self.assertRaises(fit.AnswerError) as caught:
            fit.normalize({"result": "x", "frequency": "once"})
        self.assertEqual(set(caught.exception.missing),
                         {"needsContinuingContext", "needsMultipleSpecialists", "workProvenManually", "overlapsExistingRole"})

    def test_normalize_rejects_an_unknown_override(self):
        with self.assertRaises(fit.AnswerError):
            fit.normalize({**answers(), "override": "agency"})


class Downloads(unittest.TestCase):
    def test_each_kind_has_its_file(self):
        for kind, (name, heading) in fit.ARTIFACTS.items():
            with self.subTest(kind=kind):
                extra = dict(frequency="scheduled", workProvenManually=True, trigger="Every Friday.",
                             failurePlan="Stop and report the error.", costLimit="$2 per run.",
                             shutdown="Disable the schedule.") if kind == "routine" else {}
                r = rec(override=kind, needsMultipleSpecialists=kind == "crew", **extra)
                self.assertEqual(r["kind"], kind)
                md = fit.to_markdown(r)
                for part in (f"# {heading}", "## First test", "## Done check", JOB):
                    self.assertIn(part, md)

    def test_skill_download_keeps_access_and_approvals(self):
        md = fit.to_markdown(rec(frequency="repeat", access="Approved interview transcripts",
                                 approvals="A person approves the final briefing"))
        self.assertIn("## Reusable process", md)
        self.assertIn("## Minimum access", md)
        self.assertIn("A person approves the final briefing", md)


class RulesTable(unittest.TestCase):
    """Every outcome is reachable through the table, and the table is the decision."""

    def all_answer_sets(self):
        for f, ctx, multi, proven, overlap, controls in itertools.product(
                ("once", "repeat", "scheduled"), (True, False), (True, False), (True, False), ("yes", "no", "unsure"),
                (True, False)):
            filled = dict(trigger="Every Friday.", failurePlan="Stop.", costLimit="$1.", shutdown="Turn it off.") if controls else {}
            yield answers(frequency=f, needsContinuingContext=ctx, needsMultipleSpecialists=multi,
                          workProvenManually=proven, overlapsExistingRole=overlap, **filled)

    def test_all_five_outcomes_are_reachable_without_an_override(self):
        seen = {fit.decide(a).kind for a in self.all_answer_sets()}
        self.assertEqual(seen, set(fit.KIND_ORDER))

    def test_each_rule_fires_somewhere(self):
        fired = {fit.decide(a).rule for a in self.all_answer_sets()}
        self.assertEqual(fired, {r.id for r in fit.RULES})

    def test_the_last_rule_has_no_conditions(self):
        self.assertEqual(fit.RULES[-1].conditions, ())

    def test_rules_name_only_real_questions(self):
        for rule in fit.RULES:
            for cond in rule.conditions:
                self.assertIn(cond.question, fit.QUESTION_BY_ID)
                self.assertTrue(cond.yes and cond.no)

    def test_kinds_have_plain_titles_without_periods(self):
        for kind in fit.KIND_ORDER:
            self.assertFalse(fit.KINDS[kind]["title"].endswith("."))
            self.assertFalse(fit.KINDS[kind]["meaning"].endswith("."))

    def test_answer_sets_covered(self):
        self.assertEqual(sum(1 for _ in self.all_answer_sets()), 3 * 2 * 2 * 2 * 3 * 2)


class Trace(unittest.TestCase):
    """Each answer is shown with the question it came from, and whether the result depended on it."""

    def rows(self, **overrides):
        a = answers(**overrides)
        return {r["id"]: r for r in fit.trace(a, fit.recommend(a)["decision"])}

    def test_every_row_carries_its_question_text(self):
        rows = self.rows(frequency="repeat")
        for qid, row in rows.items():
            self.assertEqual(row["question"], fit.QUESTION_BY_ID[qid].text)
            self.assertTrue(row["answer"])

    def test_crew_depends_only_on_the_specialists_answer(self):
        rows = self.rows(needsMultipleSpecialists=True)
        self.assertEqual({k for k, r in rows.items() if r["mattered"]}, {"needsMultipleSpecialists"})
        self.assertEqual(rows["needsMultipleSpecialists"]["answer"], "Yes")
        self.assertIn("crew", rows["needsMultipleSpecialists"]["effect"])

    def test_bot_depends_on_four_answers(self):
        rows = self.rows(frequency="repeat", needsContinuingContext=True, overlapsExistingRole="no")
        self.assertEqual({k for k, r in rows.items() if r["mattered"]},
                         {"needsMultipleSpecialists", "frequency", "needsContinuingContext", "overlapsExistingRole"})
        self.assertFalse(rows["workProvenManually"]["mattered"])

    def test_once_explains_why_it_is_not_a_bot(self):
        rows = self.rows(frequency="once")
        self.assertEqual(rows["frequency"]["answer"], "Once")
        self.assertIn("once", rows["frequency"]["effect"].lower())
        self.assertFalse(rows["needsContinuingContext"]["mattered"])

    def test_winning_rule_explains_its_own_answers(self):
        rows = self.rows(frequency="repeat", needsContinuingContext=False)
        self.assertEqual(rows["frequency"]["effect"], "It repeats when you ask, so saved steps help each time")

    def test_routine_marks_its_controls_as_used(self):
        rows = self.rows(frequency="scheduled", workProvenManually=True, trigger="Every Friday.",
                         failurePlan="Stop.", costLimit="$1.", shutdown="Turn it off.")
        for key in ("frequency", "workProvenManually", "trigger", "failurePlan", "costLimit", "shutdown"):
            self.assertTrue(rows[key]["mattered"], key)

    def test_blank_optional_details_are_left_out(self):
        rows = self.rows()
        self.assertNotIn("trigger", rows)
        self.assertNotIn("access", rows)

    def test_override_is_explained(self):
        a = answers(override="bot")
        d = fit.recommend(a)["decision"]
        self.assertEqual(d["rule"], "override")
        self.assertIn("yourself", d["checks"][0]["effect"])


class TextHelpers(unittest.TestCase):
    def test_split_planning_items(self):
        self.assertEqual(fit.split_planning_items("- One\n2) Two; one\n\n• Three"), ["One", "Two", "Three"])

    def test_escape_markdown(self):
        self.assertEqual(fit.escape_markdown("a*b_[c]#"), r"a\*b\_\[c\]\#")


if __name__ == "__main__":
    unittest.main()
