"""Hand-over: Cabinet notices Hermes's own Bots catalog (NousResearch/hermes-agent#119260), says so, and stays out of its lane.

The desktop page's signal is the gateway answering ``bots.catalog`` (tests/test_desktop.py). The Python side asks
``hermes_cli.bot_catalog`` directly, offline, and reads the markers Hermes's catalog leaves in a profile it added.
"""

from __future__ import annotations

import os
import tempfile
import types
import unittest
from unittest import mock

from _hermes_fake import gateway_create, hermes_bots_install, make_foreign_profile, wipe_profiles
from _load import sub

hermes = sub("hermes_bridge")
api = sub("api")
installer = sub("installer")
catalog = sub("catalog")


def fake_catalog_module(names=None, *, loader=True, raises=None):
    mod = types.ModuleType("hermes_cli.bot_catalog")
    calls = []
    if loader:
        def load_bot_catalog(catalog_dir=None, *, include_live=True):
            calls.append(include_live)
            if raises:
                raise raises
            return [types.SimpleNamespace(name=n) for n in names or []]
        mod.load_bot_catalog = load_bot_catalog
    return mod, calls


class Detect(unittest.TestCase):
    def detect_with(self, mod):
        def fake_import(name, *a, **k):
            if name == "hermes_cli.bot_catalog" and mod is not None:
                return mod
            raise ImportError(name)

        with mock.patch.object(hermes.importlib, "import_module", side_effect=fake_import):
            return hermes.detect_core_catalog()

    def test_no_hermes_at_all(self):
        out = self.detect_with(None)
        self.assertEqual(out, {"available": False, "via": None, "detail": "This Hermes has no Bots catalog of its own yet.", "names": []})

    def test_the_real_catalog_module_is_noticed_with_its_names(self):
        mod, calls = fake_catalog_module(["research-analyst", "inbox-triage", "research-analyst"])
        out = self.detect_with(mod)
        self.assertTrue(out["available"])
        self.assertEqual(out["via"], "hermes_cli.bot_catalog")
        self.assertEqual(out["names"], ["inbox-triage", "research-analyst"])
        self.assertEqual(calls, [False])  # offline: Cabinet never makes Hermes fetch its live feed

    def test_a_module_without_the_loader_is_not_a_catalog(self):
        mod, _ = fake_catalog_module(loader=False)
        self.assertFalse(self.detect_with(mod)["available"])

    def test_a_loader_that_fails_means_no_catalog(self):
        mod, _ = fake_catalog_module(raises=ValueError("bad catalog"))
        out = self.detect_with(mod)
        self.assertFalse(out["available"])
        self.assertEqual(out["names"], [])

    def test_lookalike_module_names_no_longer_count(self):
        # The old guess matched any bot_store / bots_hub / catalog_bots module. Only the real module path counts now.
        looked = []

        def fake_import(name, *a, **k):
            looked.append(name)
            raise ImportError(name)

        with mock.patch.object(hermes.importlib, "import_module", side_effect=fake_import):
            self.assertFalse(hermes.detect_core_catalog()["available"])
        self.assertEqual(looked, ["hermes_cli.bot_catalog"])

    def test_the_real_hermes_if_present(self):
        # Documents the world this was built against: Hermes main has no bot_catalog yet. When it lands this fails on
        # purpose, which is the moment to re-check the hand-over against what shipped.
        try:
            import hermes_cli  # noqa: F401
        except ImportError:
            self.skipTest("no Hermes here")
        self.assertFalse(hermes.detect_core_catalog()["available"])


class Sandbox(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("CABINET_DATA_DIR")
        os.environ["CABINET_DATA_DIR"] = self._tmp.name
        wipe_profiles()
        catalog.set_active(None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("CABINET_DATA_DIR", None)
        else:
            os.environ["CABINET_DATA_DIR"] = self._saved
        self._tmp.cleanup()
        wipe_profiles()


class Markers(Sandbox):
    def test_a_blueprint_names_the_catalog_entry(self):
        d = hermes_bots_install("research-analyst", "analyst")
        self.assertEqual(hermes.hermes_catalog_name(d), "research-analyst")

    def test_provenance_alone_is_enough(self):
        d = hermes_bots_install("inbox-triage", blueprint=False)
        self.assertEqual(hermes.hermes_catalog_name(d), "inbox-triage")

    def test_a_top_level_kind_counts_too(self):
        d = make_foreign_profile("helper")
        (d / "profile.yaml").write_text("kind: bot-catalog\nname: design-critic\n")
        self.assertEqual(hermes.hermes_catalog_name(d), "design-critic")

    def test_an_unreadable_blueprint_still_marks_the_profile(self):
        d = make_foreign_profile("odd")
        (d / "bot-blueprint.yaml").write_text("::: not yaml :::\n")
        self.assertEqual(hermes.hermes_catalog_name(d), "")

    def test_an_ordinary_profile_and_a_cabinet_profile_are_not_marked(self):
        self.assertIsNone(hermes.hermes_catalog_name(make_foreign_profile("mine")))
        plan = installer.plan_bot("scout")
        self.assertIsNone(hermes.hermes_catalog_name(gateway_create(plan["create"]["params"])))

    def test_another_provenance_kind_is_not_the_catalog(self):
        d = make_foreign_profile("cloned")
        (d / "profile.yaml").write_text("provenance:\n  kind: clone\n  catalog_name: scout\n")
        self.assertIsNone(hermes.hermes_catalog_name(d))

    def test_installs_map_catalog_names_to_profiles(self):
        hermes_bots_install("research-analyst", "analyst")
        hermes_bots_install("scout")
        make_foreign_profile("planner")
        self.assertEqual(hermes.hermes_catalog_installs(), {"research-analyst": "analyst", "scout": "scout"})


class DuplicateGuard(Sandbox):
    def test_my_bots_lists_cabinet_bots_hermes_bots_already_added(self):
        hermes_bots_install("scout", "scout-hermes")
        hermes_bots_install("research-analyst")  # not one of Cabinet's Bots: not Cabinet's business
        out = installer.my_bots()
        self.assertEqual([(h["slug"], h["profile"]) for h in out["hermesBots"]], [("scout", "scout-hermes")])
        self.assertEqual(out["bots"], [])  # never recorded as Cabinet's own

    def test_the_catalog_view_tells_the_page(self):
        hermes_bots_install("planner", "my-planner")
        self.assertEqual(api.catalog_view()["hermesAdded"], {"planner": "my-planner"})

    def test_nothing_added_through_hermes_bots(self):
        make_foreign_profile("scout")
        self.assertEqual(api.catalog_view()["hermesAdded"], {})
        self.assertEqual(installer.my_bots()["hermesBots"], [])


class InThePage(unittest.TestCase):
    def test_the_catalog_view_carries_the_answer(self):
        out = api.catalog_view()
        self.assertIn("handover", out)
        self.assertEqual(set(out["handover"]), {"available", "via", "detail", "names"})

    def test_the_catalog_view_carries_what_is_available(self):
        out = api.catalog_view()
        self.assertEqual(set(out["installer"]), {"hermes", "kanban"})

    def test_when_core_has_one_the_page_is_told(self):
        found = {"available": True, "via": "hermes_cli.bot_catalog", "detail": "This Hermes ships its own Bots catalog.", "names": ["scout"]}
        with mock.patch.object(api.hermes_bridge, "detect_core_catalog", return_value=found):
            self.assertTrue(api.catalog_view()["handover"]["available"])


if __name__ == "__main__":
    unittest.main()
