"""The page backend for the live catalog, adding, crews, kits and undo, through api.py and through the routes."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from _hermes_fake import fake_delete, gateway_create, make_foreign_profile, wipe_profiles
from _load import ROOT, sub

api = sub("api")
catalog = sub("catalog")
hermes = sub("hermes_bridge")
installer = sub("installer")
sync = sub("sync")
schema = sub("catalog_schema")
# A made-up key, built from parts so no scanner mistakes this file for one holding a real key.
FAKE_KEY = "sk" + "-" + "abcdefghijklmnopqrstuvwxyz" + "123456"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("CABINET_DATA_DIR")
        os.environ["CABINET_DATA_DIR"] = self._tmp.name
        wipe_profiles()
        catalog.set_active(None)
        sync._STATE.update({"source": None, "updatedAt": None, "checkedAt": None, "error": None})
        patcher = mock.patch.object(hermes, "delete_profile", side_effect=fake_delete)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("CABINET_DATA_DIR", None)
        else:
            os.environ["CABINET_DATA_DIR"] = self._saved
        self._tmp.cleanup()
        catalog.set_active(None)
        wipe_profiles()


class CatalogView(Base):
    def test_the_page_gets_bots_crews_and_where_they_came_from(self):
        out = api.catalog_view()
        self.assertEqual(len(out["bots"]), 20)
        self.assertEqual(len(out["crews"]), 11)
        self.assertEqual(out["status"]["source"], "bundled")
        self.assertEqual(out["status"]["label"], "Showing the saved copy from Oct 15")

    def test_the_long_role_text_stays_on_the_backend(self):
        for b in api.catalog_view()["bots"]:
            self.assertNotIn("role", b)
            self.assertIsInstance(b["skills"], list)
            if b["skills"]:
                self.assertIsInstance(b["skills"][0], str)

    def test_refresh_reports_the_live_copy(self):
        live = json.loads(json.dumps(catalog.bundled()))
        live["generatedAt"] = "2026-10-16"
        live["bots"][0]["summary"] = "Fresh from the site."
        with mock.patch.object(sync, "fetch", return_value=schema.validate_catalog(live)):
            out = api.catalog_refresh()
        self.assertEqual(out["status"]["label"], "Updated Oct 16 from botcabinet.com")
        self.assertEqual(out["bots"][0]["summary"], "Fresh from the site.")

    def test_refresh_when_the_site_has_no_file_yet(self):
        with mock.patch.object(sync, "fetch", side_effect=sync.SyncError(sync.NOT_FOUND)):
            out = api.catalog_refresh()
        self.assertEqual(out["status"]["source"], "bundled")
        self.assertIn("couldn't find", out["status"]["error"])
        self.assertEqual(out["status"]["tone"], "warn")

    def test_a_live_bot_the_snapshot_never_had_can_be_added(self):
        live = json.loads(json.dumps(catalog.bundled()))
        live["generatedAt"] = "2026-10-16"
        live["bots"].append({"slug": "brand-new", "name": "Brand New", "title": "A newly published Bot", "category": "writing",
                             "summary": "Does a new thing.", "outcome": "Do the new thing", "role": "You are Brand New. Do the new thing and nothing else.",
                             "inputs": ["A request"], "outputs": ["A result"]})
        with mock.patch.object(sync, "fetch", return_value=schema.validate_catalog(live)):
            api.catalog_refresh()
        plan = api.bot_plan("brand-new", {})
        self.assertTrue(plan["ready"])
        self.assertIn("You are Brand New", plan["soul"])


class AddThroughTheApi(Base):
    def test_plan_create_confirm_and_list(self):
        plan = api.bot_plan("scout", {})
        gateway_create(plan["create"]["params"])
        out = api.install_confirm({"planId": plan["planId"]})
        self.assertEqual(out["install"]["profile"], "scout")
        self.assertEqual([b["profile"] for b in api.my_bots()["bots"]], ["scout"])

    def test_edits_come_in_through_the_body(self):
        plan = api.bot_plan("scout", {"edits": {"profile": "my-scout", "displayName": "My Scout"}})
        self.assertEqual(plan["profile"], "my-scout")

    def test_junk_bodies_are_survivable(self):
        for body in (None, "x", 5, [], {"edits": "no"}, {"planId": 5}):
            api.bot_plan("scout", body)
        for body in (None, "x", {}, {"planId": None}, {"planId": ["a"]}):
            with self.assertRaises(api.ApiError) as caught:
                api.install_confirm(body)
            self.assertEqual(caught.exception.status_code, 404)

    def test_oversize_body(self):
        with self.assertRaises(api.ApiError) as caught:
            api.bot_plan("scout", {"edits": {"soul": "x" * 300_000}})
        self.assertEqual(caught.exception.status_code, 413)

    def test_remove_through_the_api(self):
        plan = api.bot_plan("scout", {})
        gateway_create(plan["create"]["params"])
        iid = api.install_confirm({"planId": plan["planId"]})["install"]["id"]
        self.assertTrue(api.remove_preview("bot", str(iid))["canRemove"])
        with self.assertRaises(api.ApiError) as caught:
            api.remove("bot", iid, {})
        self.assertEqual(caught.exception.status_code, 400)
        out = api.remove("bot", iid, {"confirm": True})
        self.assertEqual(out["result"], "removed")

    def test_forget_through_the_api(self):
        plan = api.bot_plan("scout", {})
        gateway_create(plan["create"]["params"])
        iid = api.install_confirm({"planId": plan["planId"]})["install"]["id"]
        (hermes.profiles_root() / "scout").rename(hermes.profiles_root() / "gone")
        api.forget(iid)
        self.assertEqual(api.my_bots()["bots"], [])


class CrewsThroughTheApi(Base):
    def test_set_up_a_crew(self):
        plan = api.crew_setup("personal-planning-desk", {})
        made = []
        for m in plan["members"]:
            gateway_create(m["create"]["params"])
            made.append(m["profile"])
        out = api.crew_confirm({"planId": plan["planId"], "created": made})
        self.assertTrue(out["ok"])
        with mock.patch.object(hermes, "kanban_available", return_value=True):
            cards = api.crew_cards_plan(out["crew"]["id"])
        self.assertTrue(cards["requiresConfirm"])
        with self.assertRaises(api.ApiError):
            api.crew_cards_create(out["crew"]["id"], {})
        self.assertEqual(api.remove("crew", out["crew"]["id"], {"confirm": True})["mybots"]["crews"], [])

    def test_crew_choices_travel_in_the_body(self):
        make_foreign_profile("planner")
        plan = api.crew_setup("personal-planning-desk", {"choices": {"planner": {"mode": "create"}}})
        self.assertEqual([m for m in plan["members"] if m["slug"] == "planner"][0]["profile"], "planner-2")

    def test_junk_created_list(self):
        plan = api.crew_setup("personal-planning-desk", {})
        out = api.crew_confirm({"planId": plan["planId"], "created": "not a list"})
        self.assertFalse(out["ok"])


class KitsThroughTheApi(Base):
    FIELDS = {"botName": "Lookout", "jobOutcome": "Watch pricing pages weekly", "approvalBoundaries": "Ask before contacting anyone"}

    def test_save_plan_and_remove(self):
        kit = api.kit_save({"fields": self.FIELDS, "job": "Watch pricing"})["kit"]
        self.assertEqual(kit["name"], "Lookout")
        plan = api.kit_plan(kit["id"], {})
        self.assertTrue(plan["ready"])
        self.assertEqual(plan["profile"], "lookout")
        gateway_create(plan["create"]["params"])
        installed = installer.confirm(plan["planId"])
        self.assertEqual(installed["install"]["kind"], "lab")
        self.assertEqual(len(api.my_bots()["kits"]), 1)  # the kit stays, reusable
        make_name = api.kit_plan(kit["id"], {})
        self.assertEqual(make_name["profile"], "lookout-2")
        api.remove("kit", kit["id"], {"confirm": True})
        self.assertEqual(api.my_bots()["kits"], [])

    def test_a_kit_needs_a_real_name(self):
        for fields in ({"botName": "New bot", "jobOutcome": "x"}, {"jobOutcome": "x"}, "x", None):
            with self.assertRaises(api.ApiError):
                api.kit_save({"fields": fields})

    def test_unknown_kit(self):
        with self.assertRaises(api.ApiError) as caught:
            api.kit_plan(999, {})
        self.assertEqual(caught.exception.status_code, 404)


class LabPlanIsNowTheSamePath(Base):
    JOB = "Every Monday, summarize what changed on three competitors' pricing pages"

    def plan(self, body_extra=None, **changes):
        fields = {f["id"]: f["value"] for f in api.lab_start({"job": self.JOB})["fields"]}
        fields.update(changes)
        return api.lab_plan({"fields": fields, **(body_extra or {})})

    def test_a_ready_plan_is_kept_and_creatable(self):
        out = self.plan(botName="Lookout")
        self.assertTrue(out["ready"])
        self.assertTrue(out["planId"])
        self.assertEqual(out["create"]["params"]["no_skills"], True)
        self.assertTrue(out["where"].replace("\\", "/").endswith("/profiles/lookout"), out["where"])
        gateway_create(out["create"]["params"])
        recorded = installer.confirm(out["planId"])
        self.assertEqual(recorded["install"]["profile"], "lookout")

    def test_a_taken_name_is_a_problem_with_a_suggestion(self):
        make_foreign_profile("lookout")
        out = self.plan(botName="Lookout")
        self.assertFalse(out["ready"])
        self.assertEqual(out["suggestion"], "lookout-2")
        self.assertIsNone(out["create"])
        self.assertIsNone(out["planId"])

    def test_the_profile_can_be_typed(self):
        out = self.plan({"profile": "pricing-watch"}, botName="Lookout")
        self.assertEqual(out["profile"], "pricing-watch")

    def test_an_unsafe_typed_profile(self):
        for bad in ("../x", "a/b", "hermes", "Has Space"):
            out = self.plan({"profile": bad}, botName="Lookout")
            self.assertFalse(out["ready"], bad)
            self.assertIsNone(out["create"])

    def test_a_secret_in_a_field_blocks_creation(self):
        out = self.plan(botName="Lookout", toolsIntegrations="use " + FAKE_KEY + " for the API")
        self.assertFalse(out["ready"])
        self.assertNotIn(FAKE_KEY, json.dumps(out["problems"]))

    def test_the_plan_is_too_big(self):
        with self.assertRaises(api.ApiError) as caught:
            api.lab_plan({"fields": {"botName": "x" * 300_000}})
        self.assertEqual(caught.exception.status_code, 413)

    def test_the_old_contract_still_holds(self):
        out = self.plan(botName="Lookout")
        for key in ("ready", "missing", "profile", "reserved", "description", "soul", "create"):
            self.assertIn(key, out)
        self.assertNotIn("model", out["create"]["params"])


class Routes(unittest.TestCase):
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
        spec = importlib.util.spec_from_file_location("bc2_plugin_api_install", ROOT / "dashboard" / "plugin_api.py")
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.HTTPException = HTTPException
        # The routes load the plugin as their own copy of the package (the way Hermes does), so patches go on that copy.
        cls.dash_hermes = importlib.import_module("bot_cabinet_v2_dashboard_package.hermes_bridge")
        cls.dash_sync = importlib.import_module("bot_cabinet_v2_dashboard_package.sync")
        cls.dash_catalog = importlib.import_module("bot_cabinet_v2_dashboard_package.catalog")

    @classmethod
    def tearDownClass(cls):
        if cls._saved is None:
            sys.modules.pop("fastapi", None)
        else:
            sys.modules["fastapi"] = cls._saved

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch.dict(os.environ, {"CABINET_DATA_DIR": self._tmp.name})
        self._patch.start()
        wipe_profiles()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()
        wipe_profiles()

    def route(self, method, path):
        return self.mod.router.routes[(method, path)]

    def test_catalog_routes(self):
        self.assertEqual(len(self.route("GET", "/catalog")()["bots"]), 20)

    def test_the_whole_add_and_undo_through_the_routes(self):
        plan = self.route("POST", "/bots/{slug}/plan")("scout", {})
        gateway_create(plan["create"]["params"])
        out = self.route("POST", "/install/confirm")({"planId": plan["planId"]})
        iid = out["install"]["id"]
        self.assertEqual(self.route("GET", "/mybots")()["bots"][0]["profile"], "scout")
        self.assertTrue(self.route("GET", "/remove/{kind}/{item_id}")("bot", iid)["canRemove"])
        with mock.patch.object(self.dash_hermes, "delete_profile", side_effect=fake_delete):
            self.route("POST", "/remove/{kind}/{item_id}")("bot", iid, {"confirm": True})
        self.assertEqual(self.route("GET", "/mybots")()["bots"], [])

    def test_errors_become_http_errors(self):
        cases = [(("POST", "/bots/{slug}/plan"), ("ghost", {}), 404), (("POST", "/install/confirm"), ({"planId": "x"},), 404),
                 (("POST", "/crews/{slug}/setup"), ("ghost", {}), 404), (("GET", "/crews/{crew_id}/cards"), (999,), 404),
                 (("POST", "/crews/{crew_id}/cards"), (999, {}), 400), (("GET", "/remove/{kind}/{item_id}"), ("bot", 999), 404),
                 (("POST", "/remove/{kind}/{item_id}"), ("bot", 999, {}), 400), (("POST", "/forget/{item_id}"), (999,), 404),
                 (("POST", "/kits"), ({"fields": "x"},), 422), (("POST", "/kits/{kit_id}/plan"), (999, {}), 404)]
        for key, args, status in cases:
            with self.assertRaises(self.HTTPException, msg=str(key)) as caught:
                self.route(*key)(*args)
            self.assertEqual(caught.exception.status_code, status, key)

    def test_the_refresh_route_never_raises_for_a_missing_file(self):
        self.dash_catalog.set_active(None)
        with mock.patch.object(self.dash_sync, "fetch", side_effect=self.dash_sync.SyncError("nope")):
            out = self.route("POST", "/catalog/refresh")()
        self.assertEqual(out["status"]["source"], "bundled")


class SiteCatalogTool(unittest.TestCase):
    def run_tool(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "tools" / "make_site_catalog.py"), *args], capture_output=True, text=True)

    def test_it_emits_exactly_the_snapshot_and_cabinet_accepts_it(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "public" / "catalog.json"
            run = self.run_tool("--out", str(out))
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(out.read_text(encoding="utf-8"), (ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))
            schema.validate_catalog(json.loads(out.read_text(encoding="utf-8")))
            self.assertEqual(self.run_tool("--check", str(out)).returncode, 0)

    def test_check_explains_what_cabinet_would_refuse(self):
        with tempfile.TemporaryDirectory() as t:
            bad = json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))
            bad["bots"][0]["slug"] = "BAD"
            bad["bots"][1]["model"] = "x"
            path = Path(t) / "catalog.json"
            path.write_text(json.dumps(bad), encoding="utf-8")
            run = self.run_tool("--check", str(path))
            self.assertEqual(run.returncode, 1)
            self.assertIn("would refuse", run.stdout)
            self.assertIn("unknown key 'model'", run.stdout)

    def test_check_on_a_missing_or_unreadable_file(self):
        self.assertEqual(self.run_tool("--check", "/no/such/file.json").returncode, 1)
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "x.json"
            path.write_text("{ nope")
            self.assertEqual(self.run_tool("--check", str(path)).returncode, 1)

    def test_the_default_output_folder_is_not_committed(self):
        self.assertIn("site-catalog/", (ROOT / ".gitignore").read_text(encoding="utf-8"))

    def test_the_snapshot_and_the_site_data_agree(self):
        site = json.loads((ROOT / "data" / "site.json").read_text(encoding="utf-8"))
        snap = json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(site["siteCommit"], snap["siteCommit"])
        self.assertEqual([b["slug"] for b in site["bots"]], [b["slug"] for b in snap["bots"]])
        self.assertEqual([c["slug"] for c in site["crews"]], [c["slug"] for c in snap["crews"]])


if __name__ == "__main__":
    unittest.main()
