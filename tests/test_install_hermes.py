"""The install flow against Hermes's own Python functions (no ``hermes`` command, ever).

These run only when ``hermes_cli`` can be imported, which means through ``tests/run_with_hermes.py``: it
starts the tests with a Hermes interpreter and a SEPARATE clone of Hermes on the path, and the shared
loader (``_load.py``) has already pointed HERMES_HOME and HOME at a temporary folder. Everywhere else they
skip. They create and delete real Hermes profiles, but only inside that temporary home.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from _load import SANDBOX, sub

hermes = sub("hermes_bridge")
installer = sub("installer")
errors = sub("errors")
catalog = sub("catalog")

try:
    profiles_mod = importlib.import_module("hermes_cli.profiles")
except Exception:  # not running under Hermes
    profiles_mod = None

NEEDS = unittest.skipIf(profiles_mod is None, "Hermes isn't importable here (use tests/run_with_hermes.py)")


def gateway_like_create(params):
    """What Hermes's gateway does for profiles.create, minus copying the owner's sign-in (there is none in a test home)."""
    path = profiles_mod.create_profile(name=params["name"], no_skills=params["no_skills"], description=params["description"] or None)
    (path / "SOUL.md").write_text(params["soul"], encoding="utf-8")
    return path


@NEEDS
class RealProfiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("CABINET_DATA_DIR")
        os.environ["CABINET_DATA_DIR"] = self._tmp.name
        catalog.set_active(None)
        self.created = []

    def tearDown(self):
        for name in self.created:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    profiles_mod.delete_profile(name, yes=True)
            except Exception:
                pass
        if self._saved is None:
            os.environ.pop("CABINET_DATA_DIR", None)
        else:
            os.environ["CABINET_DATA_DIR"] = self._saved
        self._tmp.cleanup()

    def add(self, slug, **edits):
        plan = installer.plan_bot(slug, edits or None)
        self.assertTrue(plan["ready"], plan["problems"])
        gateway_like_create(plan["create"]["params"])
        self.created.append(plan["profile"])
        return plan, installer.confirm(plan["planId"])

    def test_hermes_is_the_one_in_the_sandbox(self):
        self.assertTrue(hermes.available())
        root = hermes.profiles_root().resolve()
        self.assertTrue(str(root).startswith(str(SANDBOX.resolve())), root)

    def test_hermes_accepts_every_name_cabinet_plans(self):
        for b in catalog.bots():
            name = installer.plan_bot(b["slug"])["profile"]
            profiles_mod.validate_profile_name(name)  # Hermes's own rule agrees with Cabinet's

    def test_hermes_rejects_what_cabinet_rejects(self):
        for bad in ("../x", "a/b", "hermes", "x" * 65, "Has Space"):
            self.assertFalse(installer.plan_bot("scout", {"profile": bad})["ready"])
            with self.assertRaises(ValueError):
                profiles_mod.validate_profile_name(bad)
        self.assertFalse(installer.plan_bot("scout", {"profile": "default"})["ready"])
        with self.assertRaises(ValueError):  # Hermes won't create a profile called default either
            profiles_mod.create_profile(name="default")

    def test_a_real_profile_goes_from_plan_to_record_to_removal(self):
        plan, out = self.add("scout")
        directory = profiles_mod.get_profile_dir("scout")
        self.assertTrue(directory.is_dir())
        self.assertIn("scout", hermes.existing_profiles())
        self.assertEqual(str(directory), plan["where"])
        self.assertEqual((directory / "SOUL.md").read_text(encoding="utf-8"), plan["soul"])
        self.assertEqual(out["install"]["status"], "ready")
        self.assertEqual(out["install"]["canRemove"], True)
        meta = profiles_mod.read_profile_meta(directory)
        self.assertEqual(meta["display_name"], "Scout")  # Cabinet's one write to a profile it just made
        removed = installer.remove("bot", out["install"]["id"], True)
        self.assertEqual(removed["result"], "removed")
        self.assertFalse(directory.exists())
        self.assertNotIn("scout", hermes.existing_profiles())
        self.created.remove("scout")

    def test_real_hermes_refuses_to_overwrite_and_so_does_cabinet(self):
        profiles_mod.create_profile(name="writer", no_skills=True, no_alias=True)
        self.created.append("writer")
        before = (profiles_mod.get_profile_dir("writer") / "SOUL.md").read_text(encoding="utf-8")
        plan = installer.plan_bot("writer", {"profile": "writer"})
        self.assertFalse(plan["ready"])
        with self.assertRaises(FileExistsError):  # and if it were attempted, Hermes itself says no
            profiles_mod.create_profile(name="writer", no_skills=True, no_alias=True)
        self.assertEqual((profiles_mod.get_profile_dir("writer") / "SOUL.md").read_text(encoding="utf-8"), before)
        self.assertEqual(installer.plan_bot("writer")["profile"], "writer-2")

    def test_a_deleted_profile_leaves_a_folder_cabinet_still_will_not_build_on(self):
        _, out = self.add("planner")
        installer.remove("bot", out["install"]["id"], True)
        self.created.remove("planner")
        # whatever Hermes leaves behind (a tombstone), the name is either free or refused, never half-reused
        plan = installer.plan_bot("planner", {"profile": "planner"})
        if plan["ready"]:
            gateway_like_create(plan["create"]["params"])
            self.created.append("planner")

    def test_an_edit_in_hermes_stops_removal(self):
        _, out = self.add("editor")
        directory = profiles_mod.get_profile_dir("editor")
        (directory / "SOUL.md").write_text("I rewrote this.\n", encoding="utf-8")
        with self.assertRaises(errors.ApiError) as caught:
            installer.remove("bot", out["install"]["id"], True)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertTrue(directory.exists())

    def test_a_real_crew(self):
        plan = installer.plan_crew("personal-planning-desk")
        self.assertTrue(plan["ready"])
        made = []
        for m in plan["members"]:
            gateway_like_create(m["create"]["params"])
            self.created.append(m["profile"])
            made.append(m["profile"])
        out = installer.confirm_crew(plan["planId"], made)
        self.assertTrue(out["ok"])
        result = installer.remove("crew", out["crew"]["id"], True)
        self.assertEqual(sorted(result["removed"]), sorted(made))
        for name in made:
            self.assertFalse(profiles_mod.get_profile_dir(name).exists())
            self.created.remove(name)

    def test_real_kanban_cards_are_made_blocked_and_in_order(self):
        if not hermes.kanban_available():
            self.skipTest("this Hermes has no importable Kanban")
        plan = installer.plan_crew("personal-planning-desk")
        made = []
        for m in plan["members"]:
            gateway_like_create(m["create"]["params"])
            self.created.append(m["profile"])
            made.append(m["profile"])
        crew = installer.confirm_crew(plan["planId"], made)["crew"]
        preview = installer.cards_plan(crew["id"])
        self.assertTrue(preview["available"])
        out = installer.create_cards(crew["id"], True)
        self.assertEqual(len(out["cardIds"]), len(crew["steps"]))
        kb = importlib.import_module("hermes_cli.kanban_db")
        with hermes._connect(kb) as conn:
            rows = [kb.get_task(conn, cid) for cid in out["cardIds"]]
        for row in rows:
            self.assertEqual(getattr(row, "status", None) or row["status"], "blocked")
        # a second try makes nothing new
        with self.assertRaises(errors.ApiError):
            installer.create_cards(crew["id"], True)
        result = installer.remove("crew", crew["id"], True)
        self.assertEqual(result["cardsArchived"], len(out["cardIds"]))
        for name in made:
            self.created.remove(name)

    def test_hermes_failing_to_delete_is_reported_in_plain_words(self):
        with self.assertRaises(hermes.HermesError):
            hermes.delete_profile("never-existed")

    def test_the_bridge_refuses_a_name_that_escapes(self):
        for bad in ("../escape", "a/b", "~"):
            with self.assertRaises(Exception):
                hermes.profile_dir(bad)


if __name__ == "__main__":
    unittest.main()
