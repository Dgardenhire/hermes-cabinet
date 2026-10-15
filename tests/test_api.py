"""The page view models, the catalog match, credits and the dashboard routes."""

from __future__ import annotations

import re
import sys
import types
import unittest
from pathlib import Path

from _load import ROOT, sub

api = sub("api")
catalog = sub("catalog")
fit = sub("fit")


def body(**overrides):
    b = {"result": "Plan a three-day trip to Santa Fe for four people under $1,500", "frequency": "once",
         "needsContinuingContext": False, "needsMultipleSpecialists": True, "workProvenManually": False,
         "overlapsExistingRole": "no"}
    b.update(overrides)
    return b


class Catalog(unittest.TestCase):
    def test_snapshot_counts(self):
        self.assertEqual(len(catalog.bots()), 20)
        self.assertEqual(len(catalog.crews()), 11)
        self.assertEqual(len(catalog.jobs()), 18)

    def test_trip_matches_personal_planning_desk(self):
        crew, score = catalog.best_crew("Plan a three-day trip to Santa Fe for four people under $1,500")
        self.assertEqual(crew["slug"], "personal-planning-desk")
        self.assertGreater(score, 0)

    def test_grant_matches_grant_pursuit_desk(self):
        crew, _ = catalog.best_crew("Find grants we qualify for and draft the applications")
        self.assertEqual(crew["slug"], "grant-pursuit-desk")

    def test_newsletter_matches_writer(self):
        b, _ = catalog.best_bot("Draft my weekly newsletter")
        self.assertEqual(b["slug"], "writer")

    def test_nonsense_matches_nothing(self):
        self.assertEqual(catalog.best_bot("zzqx vvbn")[0], None)
        self.assertEqual(catalog.best_crew("zzqx vvbn")[0], None)

    def test_words_drop_filler_and_fold_synonyms(self):
        self.assertEqual(catalog.words("Plan a vacation for the kids"), {"plan", "travel", "kid"})


class FitForm(unittest.TestCase):
    def test_form_lists_every_question_with_choices(self):
        form = api.fit_form()
        ids = [q["id"] for q in form["questions"]]
        self.assertEqual(ids[:6], ["result", *fit.BRANCH_QUESTIONS])
        self.assertEqual(form["required"], ["result", *fit.BRANCH_QUESTIONS])
        freq = next(q for q in form["questions"] if q["id"] == "frequency")
        self.assertEqual([c["label"] for c in freq["choices"]], ["Once", "Again and again, when I ask", "On a schedule"])

    def test_common_jobs_carry_complete_answers(self):
        for job in api.fit_form()["commonJobs"]:
            with self.subTest(job=job["slug"]):
                self.assertTrue(fit.is_complete({**fit.EMPTY_ANSWERS, **job["answers"]}))
                self.assertTrue(job["lead"])

    def test_common_jobs_reach_more_than_one_outcome(self):
        kinds = {api.fit_answer(job["answers"])["kind"] for job in api.fit_form()["commonJobs"]}
        self.assertGreaterEqual(len(kinds), 3)

    def test_morning_newspaper_credits_karen_x_cheng(self):
        job = next(j for j in api.fit_form()["commonJobs"] if j["slug"] == "personal-morning-newspaper")
        self.assertIn("Karen X. Cheng", job["credit"]["line"])


class FitAnswer(unittest.TestCase):
    def test_crew_answer_names_the_kit_and_a_smaller_start(self):
        out = api.fit_answer(body())
        self.assertEqual(out["kind"], "crew")
        self.assertEqual(out["title"], "A crew")
        self.assertEqual(out["match"]["crew"]["slug"], "personal-planning-desk")
        self.assertEqual(out["next"]["action"], "crew")
        self.assertEqual([k["kind"] for k in out["kinds"] if k["active"]], ["crew"])
        self.assertEqual(out["match"]["smaller"]["slug"], "planner")

    def test_a_crew_answer_with_no_close_kit_still_leads_somewhere(self):
        # "Plan a crew" used to do nothing when no kit matched; now it opens the Crew Kits list.
        out = api.fit_answer(body(result="zzqx vlorp"))
        self.assertEqual(out["kind"], "crew")
        self.assertIsNone(out["match"]["crew"])
        self.assertEqual(out["next"]["action"], "crews")
        self.assertEqual(out["next"]["label"], "Browse the Crew Kits")

    def test_every_answer_shows_its_question(self):
        out = api.fit_answer(body())
        for row in out["answers"]:
            self.assertEqual(row["question"], fit.QUESTION_BY_ID[row["id"]].text)
        self.assertEqual([r["id"] for r in out["dependedOn"]], ["needsMultipleSpecialists"])
        self.assertEqual({r["id"] for r in out["notUsed"]},
                         {"frequency", "needsContinuingContext", "workProvenManually", "overlapsExistingRole"})

    def test_each_outcome_has_a_next_step(self):
        cases = {
            "assignment": body(needsMultipleSpecialists=False),
            "skill": body(needsMultipleSpecialists=False, frequency="repeat"),
            "routine": body(needsMultipleSpecialists=False, frequency="scheduled", workProvenManually=True,
                            trigger="Fridays at 9", failurePlan="Stop and tell me", costLimit="$1", shutdown="Turn it off"),
            "bot": body(needsMultipleSpecialists=False, frequency="repeat", needsContinuingContext=True),
            "crew": body(),
        }
        for kind, b in cases.items():
            with self.subTest(kind=kind):
                out = api.fit_answer(b)
                self.assertEqual(out["kind"], kind)
                self.assertTrue(out["next"]["label"])
                self.assertFalse(out["next"]["label"].endswith("."))
                self.assertFalse(out["summary"].endswith("."))
                self.assertFalse(out["firstTest"].endswith("."))
                self.assertTrue(out["learnMore"])
                if out["next"]["action"] == "chat":
                    self.assertIn("Santa Fe", out["next"]["draft"])

    def test_bot_answer_carries_the_lab_draft(self):
        out = api.fit_answer(body(needsMultipleSpecialists=False, frequency="repeat", needsContinuingContext=True))
        self.assertEqual(out["next"]["action"], "lab")
        self.assertEqual(out["workshopDraft"]["botName"], "New Bot")

    def test_missing_answers_are_a_422_that_names_them(self):
        with self.assertRaises(api.ApiError) as caught:
            api.fit_answer({"result": "x"})
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("frequency", caught.exception.detail["missing"])

    def test_text_must_be_text(self):
        with self.assertRaises(api.ApiError):
            api.fit_answer(body(result=["not", "text"]))

    def test_download_is_the_site_format(self):
        out = api.fit_answer(body())
        self.assertEqual(out["download"]["fileName"], "crew-blueprint.md")
        self.assertTrue(out["download"]["markdown"].endswith("Created with Bot Cabinet's Bot Fit Test."))

    def test_submitted_answers_round_trip(self):
        b = body(needsMultipleSpecialists=False, frequency="repeat")
        self.assertEqual(api.fit_answer(api.fit_answer(b)["submitted"])["kind"], "skill")


class CrewPlan(unittest.TestCase):
    def test_owner_person_becomes_you(self):
        plan = api.crew_plan("personal-planning-desk")
        self.assertEqual([s["owner"] for s in plan["steps"]], ["Planner + you", "Researcher", "Coach", "You"])
        self.assertEqual([s["you"] for s in plan["steps"]], [True, False, False, True])
        self.assertEqual(plan["tested"], "Crew test not run yet")
        self.assertTrue(any("Shann" in l["by"] for l in plan["learnMore"]))

    def test_failed_kit_says_so(self):
        self.assertEqual(api.crew_plan("publishing-desk")["tested"], "Failed its published crew test")

    def test_unknown_crew_is_404(self):
        with self.assertRaises(api.ApiError) as caught:
            api.crew_plan("nope")
        self.assertEqual(caught.exception.status_code, 404)


class BotLab(unittest.TestCase):
    JOB = "Every Monday, summarize what changed on three competitors' pricing pages"

    def test_start_labels_each_field_by_source(self):
        out = api.lab_start({"job": self.JOB})
        fields = {f["id"]: f for f in out["fields"]}
        self.assertEqual(fields["jobOutcome"]["value"], self.JOB)
        self.assertEqual(fields["jobOutcome"]["source"], "yours")
        self.assertEqual(fields["botName"]["source"], "placeholder")
        self.assertEqual(fields["approvalBoundaries"]["source"], "standard")
        self.assertIn(self.JOB, out["hermesRequest"])

    def test_start_needs_a_sentence(self):
        with self.assertRaises(api.ApiError):
            api.lab_start({"job": "   "})

    def plan(self, **changes):
        fields = {f["id"]: f["value"] for f in api.lab_start({"job": self.JOB})["fields"]}
        fields.update(changes)
        return api.lab_plan({"fields": fields})

    def test_placeholder_name_blocks_create(self):
        out = self.plan()
        self.assertFalse(out["ready"])
        self.assertEqual([m["id"] for m in out["missing"]], ["botName"])

    def test_named_plan_is_ready_and_creates_without_a_model(self):
        out = self.plan(botName="Lookout")
        self.assertTrue(out["ready"])
        self.assertEqual(out["profile"], "lookout")
        params = out["create"]["params"]
        self.assertEqual(out["create"]["method"], "profiles.create")
        self.assertNotIn("model", params)
        self.assertNotIn("provider", params)
        self.assertTrue(params["no_alias"])
        self.assertIn("You are Lookout", params["soul"])
        self.assertRegex(out["profile"], r"^[a-z0-9][a-z0-9_-]{0,63}$")

    def test_empty_required_field_blocks_create(self):
        out = self.plan(botName="Lookout", firstRunTest="  ")
        self.assertFalse(out["ready"])
        self.assertEqual(out["missing"][0]["label"], "First test")

    def test_reserved_names_are_flagged(self):
        self.assertTrue(self.plan(botName="Default")["reserved"])

    def test_unknown_fields_are_dropped(self):
        out = api.lab_plan({"fields": {"botName": "Lookout", "model": "expensive-one"}})
        self.assertNotIn("expensive-one", str(out["create"]))


class Credits(unittest.TestCase):
    def test_daily_newspaper_carries_its_credit_wherever_it_appears(self):
        card = api._bot_card(catalog.bot("daily-newspaper"))
        self.assertIn("Karen X. Cheng", card["credit"]["line"])
        out = api.fit_answer(body(result="Make me a morning newspaper from my calendar", needsMultipleSpecialists=False,
                                  frequency="repeat", needsContinuingContext=True))
        self.assertEqual(out["match"]["bot"]["slug"], "daily-newspaper")
        self.assertIn("Karen X. Cheng", out["match"]["bot"]["credit"]["line"])

    def test_hermes_bible_links_name_their_authors(self):
        for links in api.LEARN_MORE.values():
            for link in links:
                self.assertTrue(link["by"])
                if "hermesbible" in link["href"]:
                    self.assertIn("on the Hermes Bible", link["by"])


class Routes(unittest.TestCase):
    """dashboard/plugin_api.py, loaded with a stand-in FastAPI when the real one isn't installed."""

    @classmethod
    def setUpClass(cls):
        cls._saved = sys.modules.get("fastapi")
        fake = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code, detail=None):
                super().__init__(detail)
                self.status_code, self.detail = status_code, detail

        class APIRouter:
            def __init__(self):
                self.routes = {}

            def _add(self, method, path):
                def deco(fn):
                    self.routes[(method, path)] = fn
                    return fn
                return deco

            def get(self, path):
                return self._add("GET", path)

            def post(self, path):
                return self._add("POST", path)

        fake.APIRouter, fake.HTTPException = APIRouter, HTTPException
        sys.modules["fastapi"] = fake
        import importlib.util
        spec = importlib.util.spec_from_file_location("bc2_plugin_api", ROOT / "dashboard" / "plugin_api.py")
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.HTTPException = HTTPException

    @classmethod
    def tearDownClass(cls):
        if cls._saved is None:
            sys.modules.pop("fastapi", None)
        else:
            sys.modules["fastapi"] = cls._saved

    def test_route_table(self):
        self.assertEqual(set(self.mod.router.routes), {
            ("GET", "/fit"), ("POST", "/fit"), ("GET", "/crew/{slug}"), ("POST", "/lab/start"), ("POST", "/lab/plan"),
            ("GET", "/catalog"), ("POST", "/catalog/refresh"), ("POST", "/bots/{slug}/plan"), ("POST", "/install/confirm"),
            ("POST", "/crews/{slug}/setup"), ("POST", "/crews/confirm"), ("GET", "/crews/{crew_id}/cards"),
            ("POST", "/crews/{crew_id}/cards"), ("POST", "/kits"), ("POST", "/kits/{kit_id}/plan"), ("GET", "/mybots"),
            ("GET", "/remove/{kind}/{item_id}"), ("POST", "/remove/{kind}/{item_id}"), ("POST", "/forget/{item_id}")})

    def test_post_fit(self):
        self.assertEqual(self.mod.router.routes[("POST", "/fit")](body())["kind"], "crew")

    def test_errors_become_http_errors(self):
        with self.assertRaises(self.HTTPException) as caught:
            self.mod.router.routes[("POST", "/fit")]({})
        self.assertEqual(caught.exception.status_code, 422)


class Manifest(unittest.TestCase):
    def test_declares_what_register_registers(self):
        text = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^name: bot-cabinet-v2$")
        self.assertRegex(text, r"(?m)^provides_tools: \[\]$")
        self.assertRegex(text, r"(?m)^provides_hooks: \[\]$")
        pkg = sys.modules["bot_cabinet_v2"]
        self.assertEqual(pkg.TOOLS, ())
        self.assertEqual(pkg.HOOKS, ())

        class Ctx:
            calls = []

            def __getattr__(self, name):
                return lambda *a, **k: self.calls.append(name)

        ctx = Ctx()
        pkg.register(ctx)
        self.assertEqual(ctx.calls, [])


if __name__ == "__main__":
    unittest.main()
