"""Add a Bot, keep a record, and undo it: plans, recording, and removal, against a stand-in for Hermes."""

from __future__ import annotations

import json
import os
from contextlib import closing
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from _hermes_fake import fake_delete, gateway_create, hermes_bots_install, make_foreign_profile, wipe_profiles
from _load import sub

installer = sub("installer")
hermes = sub("hermes_bridge")
catalog = sub("catalog")
errors = sub("errors")
safety = sub("safety")
store_mod = sub("store")
paths = sub("paths")
sync = sub("sync")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("CABINET_DATA_DIR")
        os.environ["CABINET_DATA_DIR"] = self._tmp.name
        wipe_profiles()
        catalog.set_active(None)
        patcher = mock.patch.object(hermes, "delete_profile", side_effect=fake_delete)
        self.delete = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("CABINET_DATA_DIR", None)
        else:
            os.environ["CABINET_DATA_DIR"] = self._saved
        self._tmp.cleanup()
        wipe_profiles()

    def root(self):
        return hermes.profiles_root()

    def listing(self):
        return sorted(p.name for p in self.root().iterdir())

    def add(self, slug, **edits):
        plan = installer.plan_bot(slug, edits or None)
        self.assertTrue(plan["ready"], plan["problems"])
        gateway_create(plan["create"]["params"])
        return plan, installer.confirm(plan["planId"])

    def api_error(self, fn, *a, **k):
        with self.assertRaises(errors.ApiError) as caught:
            fn(*a, **k)
        return caught.exception


class PlanBot(Base):
    def test_plan_shows_exactly_what_will_be_made(self):
        plan = installer.plan_bot("daily-newspaper")
        self.assertTrue(plan["ready"])
        self.assertEqual(plan["profile"], "daily-newspaper")
        self.assertEqual(plan["displayName"], "Daily Newspaper")
        self.assertEqual(plan["where"], str(self.root() / "daily-newspaper"))
        self.assertEqual(plan["create"]["method"], "profiles.create")
        self.assertEqual(plan["create"]["params"], {"name": "daily-newspaper", "description": plan["description"], "soul": plan["soul"],
                                                    "no_alias": True, "no_skills": True})

    def test_no_model_is_ever_passed(self):
        params = installer.plan_bot("scout")["create"]["params"]
        for key in ("model", "provider", "base_url", "api_key", "mirror_credentials", "share_auth", "clone_from", "clone_all", "env"):
            self.assertNotIn(key, params)
        self.assertIsNone(installer.plan_bot("scout")["model"])

    def test_instructions_come_from_the_catalog_entry(self):
        bot = catalog.bot("daily-newspaper")
        soul = installer.plan_bot("daily-newspaper")["soul"]
        self.assertIn(bot["role"].strip(), soul)  # the published role, word for word
        for item in bot["inputs"] + bot["outputs"] + bot["approvals"] + bot["offLimits"]:
            self.assertIn(item, soul)
        self.assertIn(bot["firstMission"], soul)
        self.assertIn("botcabinet.com", soul)
        self.assertIn("Karen X. Cheng", soul)  # the credit travels into the instructions

    def test_description_is_one_short_line_without_a_closing_period(self):
        for b in catalog.bots():
            d = installer.plan_bot(b["slug"])["description"]
            self.assertLessEqual(len(d), 200)
            self.assertNotIn("\n", d)
            self.assertFalse(d.endswith("."), d)

    def test_every_catalog_bot_plans_cleanly_on_a_fresh_machine(self):
        for b in catalog.bots():
            plan = installer.plan_bot(b["slug"])
            self.assertTrue(plan["ready"], (b["slug"], plan["problems"]))
            self.assertRegex(plan["profile"], r"^[a-z0-9][a-z0-9_-]{0,63}$")

    def test_the_schedule_is_shown_but_never_set(self):
        plan = installer.plan_bot("daily-newspaper")
        self.assertTrue(plan["schedule"]["suggested"])
        self.assertFalse(plan["schedule"]["willSet"])
        self.assertNotIn("schedule", plan["create"]["params"])
        self.assertEqual(set(plan["create"]["params"]), {"name", "description", "soul", "no_alias", "no_skills"})

    def test_skills_are_listed_from_the_catalog(self):
        plan = installer.plan_bot("daily-newspaper")
        self.assertEqual(plan["skills"], ["Daily Newspaper core Skill"])
        self.assertIn("How you work", plan["soul"])

    def test_builtin_skills_can_be_included(self):
        self.assertTrue(installer.plan_bot("scout")["create"]["params"]["no_skills"])
        self.assertFalse(installer.plan_bot("scout", {"noSkills": False})["create"]["params"]["no_skills"])

    def test_unknown_bot(self):
        self.assertEqual(self.api_error(installer.plan_bot, "ghost").status_code, 404)
        self.assertEqual(self.api_error(installer.plan_bot, "../etc").status_code, 404)

    def test_planning_writes_nothing_to_hermes(self):
        before = self.listing()
        installer.plan_bot("scout")
        installer.plan_bot("scout", {"profile": "other"})
        self.assertEqual(self.listing(), before)

    def test_each_ready_plan_is_kept_with_its_own_id(self):
        a, b = installer.plan_bot("scout"), installer.plan_bot("scout")
        self.assertNotEqual(a["planId"], b["planId"])
        self.assertEqual(installer.store().get_plan(a["planId"])["payload"]["profile"], "scout")

    def test_a_plan_that_is_not_ready_is_not_kept_or_creatable(self):
        plan = installer.plan_bot("scout", {"profile": "../x"})
        self.assertFalse(plan["ready"])
        self.assertIsNone(plan["planId"])
        self.assertIsNone(plan["create"])


class NamesAreNeverOverwritten(Base):
    def test_a_taken_name_gets_a_free_suggestion(self):
        make_foreign_profile("scout")
        plan = installer.plan_bot("scout")
        self.assertTrue(plan["ready"])
        self.assertEqual(plan["profile"], "scout-2")
        self.assertIn("scout-2", plan["nameNote"])

    def test_typing_a_taken_name_is_refused(self):
        make_foreign_profile("scout")
        plan = installer.plan_bot("scout", {"profile": "scout"})
        self.assertFalse(plan["ready"])
        self.assertEqual(plan["suggestion"], "scout-2")
        self.assertIn("won't change it", plan["problems"][0]["message"])
        self.assertIsNone(plan["create"])

    def test_the_foreign_profile_is_never_touched(self):
        foreign = make_foreign_profile("scout")
        before = (foreign / "SOUL.md").read_text()
        installer.plan_bot("scout", {"profile": "scout"})
        self.assertEqual((foreign / "SOUL.md").read_text(), before)
        self.assertEqual(installer.store().installs(), [])

    def test_a_leftover_folder_counts_as_taken(self):
        (self.root() / "writer").mkdir()  # no identity file: invisible to Hermes's list, still never built on
        plan = installer.plan_bot("writer", {"profile": "writer"})
        self.assertFalse(plan["ready"])

    def test_case_does_not_get_around_it(self):
        make_foreign_profile("scout")
        self.assertFalse(installer.plan_bot("scout", {"profile": "SCOUT"})["ready"])

    def test_several_suggestions_walk_forward(self):
        for n in ("scout", "scout-2", "scout-3"):
            make_foreign_profile(n)
        self.assertEqual(installer.plan_bot("scout")["profile"], "scout-4")

    def test_default_is_never_available(self):
        plan = installer.plan_bot("scout", {"profile": "default"})
        self.assertFalse(plan["ready"])

    def test_confirm_refuses_a_profile_that_was_there_first(self):
        plan = installer.plan_bot("scout")
        gateway_create(plan["create"]["params"])
        with mock.patch.object(installer, "_born_after", return_value=False):
            err = self.api_error(installer.confirm, plan["planId"])
        self.assertEqual(err.status_code, 409)
        self.assertIn("already there", err.detail["message"])
        self.assertEqual(installer.store().installs(), [])

    def test_born_after_compares_against_the_plan_time(self):
        d = self.root() / "x"
        d.mkdir()
        self.assertTrue(installer._born_after(d, time.time()))
        self.assertFalse(installer._born_after(d, time.time() + 3600))
        self.assertFalse(installer._born_after(self.root() / "missing", 0))


class EditsAreChecked(Base):
    def test_every_kind_of_bad_profile_name(self):
        for bad in ("../x", "a/b", "..", "~", "has space", "", "x" * 65, "hermes", "UPPER CASE", "\x00", "a\\b"):
            plan = installer.plan_bot("scout", {"profile": bad})
            if bad == "":
                self.assertTrue(plan["ready"])  # empty means "pick for me"
                continue
            self.assertFalse(plan["ready"], repr(bad))
            self.assertIsNone(plan["create"])

    def test_non_text_profile_name(self):
        for bad in (5, ["x"], {"a": 1}, True):
            self.assertFalse(installer.plan_bot("scout", {"profile": bad})["ready"])

    def test_display_name_edits(self):
        plan = installer.plan_bot("scout", {"displayName": "My Research Bot"})
        self.assertTrue(plan["ready"])
        self.assertEqual(plan["displayName"], "My Research Bot")
        self.assertEqual(plan["profile"], "my-research-bot")
        for bad in ("x" * 61, "Sc‮out", "!!", 5):
            self.assertFalse(installer.plan_bot("scout", {"displayName": bad})["ready"], repr(bad))

    def test_instruction_edits(self):
        ok = installer.plan_bot("scout", {"soul": "# Scout\n\nYou are Scout. Only ever find topics.\n"})
        self.assertTrue(ok["ready"])
        self.assertEqual(ok["create"]["params"]["soul"], "# Scout\n\nYou are Scout. Only ever find topics.\n")
        for bad in ("", "short", "x" * (safety.MAX_SOUL_BYTES + 1), "You are Scout. Key sk-abcdefghijklmnopqrstuvwxyz123456",
                    "You are Scout.‮ do things", "You are Scout.\x00 do things", 5, None):
            plan = installer.plan_bot("scout", {"soul": bad})
            self.assertFalse(plan["ready"], repr(bad)[:40])
            self.assertIsNone(plan["create"])

    def test_the_refusal_does_not_echo_a_secret(self):
        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        plan = installer.plan_bot("scout", {"soul": f"You are Scout. Use {secret} always."})
        self.assertNotIn(secret, json.dumps(plan["problems"]))

    def test_description_edits(self):
        self.assertTrue(installer.plan_bot("scout", {"description": "My scout"})["ready"])
        self.assertFalse(installer.plan_bot("scout", {"description": "x" * 201})["ready"])
        self.assertFalse(installer.plan_bot("scout", {"description": "a​b"})["ready"])

    def test_oversize_body_is_refused(self):
        err = self.api_error(installer.plan_bot, "scout", {"soul": "x" * (safety.MAX_BODY_BYTES + 1)})
        self.assertEqual(err.status_code, 413)

    def test_unknown_edit_keys_do_nothing(self):
        plan = installer.plan_bot("scout", {"model": "expensive", "provider": "x", "env": {"A": "b"}, "no_alias": False, "clone_from": "default"})
        for key in ("model", "provider", "env", "clone_from"):
            self.assertNotIn(key, plan["create"]["params"])
        self.assertTrue(plan["create"]["params"]["no_alias"])

    def test_edits_that_are_not_a_dict_are_ignored(self):
        for junk in ("x", 5, ["a"], None):
            self.assertTrue(installer.plan_bot("scout", junk)["ready"])

    def test_several_problems_are_all_reported(self):
        plan = installer.plan_bot("scout", {"profile": "../x", "displayName": "!!", "soul": "x"})
        self.assertGreaterEqual(len(plan["problems"]), 3)
        self.assertEqual({p["field"] for p in plan["problems"]} >= {"profile", "displayName", "soul"}, True)

    def test_the_path_never_leaves_the_profiles_folder(self):
        for name in ("scout", "my-bot", "a_b"):
            where = Path(installer.plan_bot("scout", {"profile": name})["where"])
            self.assertEqual(where.parent, self.root())


class ConfirmAndRecord(Base):
    def test_full_flow(self):
        plan, out = self.add("scout")
        self.assertTrue(out["ok"])
        view = out["install"]
        self.assertEqual((view["profile"], view["status"], view["canRemove"]), ("scout", "ready", True))
        self.assertEqual(view["where"], str(self.root() / "scout"))
        self.assertEqual(view["source"]["slug"], "scout")
        mine = installer.my_bots()
        self.assertEqual([b["profile"] for b in mine["bots"]], ["scout"])
        self.assertEqual(mine["history"][-1]["action"], "install")

    def test_confirm_twice_is_the_same_install(self):
        plan, first = self.add("scout")
        again = installer.confirm(plan["planId"])
        self.assertTrue(again["already"])
        self.assertEqual(again["install"]["id"], first["install"]["id"])
        self.assertEqual(len(installer.store().installs()), 1)

    def test_confirm_before_hermes_made_it(self):
        plan = installer.plan_bot("scout")
        err = self.api_error(installer.confirm, plan["planId"])
        self.assertEqual(err.status_code, 409)
        self.assertEqual(installer.store().installs(), [])

    def test_unknown_and_garbage_plan_ids(self):
        for bad in ("", "nope", "../../etc", "x" * 5000, "'; DROP TABLE installs; --"):
            self.assertEqual(self.api_error(installer.confirm, bad).status_code, 404)
        self.assertEqual(installer.store().installs(), [])

    def test_a_crew_plan_id_is_not_accepted_for_a_single_bot(self):
        crew_plan = installer.plan_crew("personal-planning-desk")
        self.assertEqual(self.api_error(installer.confirm, crew_plan["planId"]).status_code, 404)

    def test_expired_plans_are_refused(self):
        plan = installer.plan_bot("scout")
        gateway_create(plan["create"]["params"])
        installer.store().set_plan_status(plan["planId"], "expired")
        self.assertEqual(self.api_error(installer.confirm, plan["planId"]).status_code, 410)

    def test_instructions_hermes_failed_to_save_are_written(self):
        plan = installer.plan_bot("scout")
        gateway_create(plan["create"]["params"], write_soul=False)
        out = installer.confirm(plan["planId"])
        self.assertIn("Cabinet wrote them", out["note"])
        self.assertEqual((self.root() / "scout" / "SOUL.md").read_text(), plan["soul"])

    def test_instructions_hermes_wrote_with_windows_line_endings_are_the_planned_ones(self):
        # Hermes writes SOUL.md in text mode, so on Windows each newline is CRLF on disk. That is still the plan.
        plan = installer.plan_bot("scout")
        target = gateway_create(plan["create"]["params"], write_soul=False)
        (target / "SOUL.md").write_bytes(plan["soul"].replace("\n", "\r\n").encode("utf-8"))
        out = installer.confirm(plan["planId"])
        self.assertEqual(out["note"], "", "Cabinet rewrote instructions that were already the planned ones")
        self.assertEqual(out["install"]["status"], "ready")
        removed = installer.remove("bot", out["install"]["id"], True)
        self.assertEqual(removed["result"], "removed")

    def test_a_lost_reply_with_windows_line_endings_is_still_recorded(self):
        plan = installer.plan_bot("scout")
        target = gateway_create(plan["create"]["params"], write_soul=False)
        (target / "SOUL.md").write_bytes(plan["soul"].replace("\n", "\r\n").encode("utf-8"))
        out = installer.confirm(plan["planId"], created=False)
        self.assertEqual(out["install"]["status"], "ready")

    def test_different_instructions_still_differ_whatever_the_line_endings(self):
        plan = installer.plan_bot("scout")
        target = gateway_create(plan["create"]["params"], write_soul=False)
        (target / "SOUL.md").write_bytes((plan["soul"] + "extra line\n").replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual(self.api_error(installer.confirm, plan["planId"], created=False).status_code, 409)

    def test_instructions_that_cannot_be_written_fail_cleanly(self):
        plan = installer.plan_bot("scout")
        target = gateway_create(plan["create"]["params"], write_soul=False)
        (target / "SOUL.md").unlink()
        (target / "SOUL.md").mkdir()  # a folder where the file should be: the write cannot succeed
        self.assertEqual(self.api_error(installer.confirm, plan["planId"]).status_code, 500)
        self.assertEqual(installer.store().installs(), [])

    def test_a_symlinked_profile_folder_is_refused(self):
        plan = installer.plan_bot("scout")
        elsewhere = Path(self._tmp.name) / "elsewhere"
        elsewhere.mkdir()
        (self.root() / "scout").symlink_to(elsewhere)
        err = self.api_error(installer.confirm, plan["planId"])
        self.assertIn(err.status_code, (409, 422))
        self.assertEqual(installer.store().installs(), [])

    def test_the_fingerprint_is_recorded(self):
        _, out = self.add("scout")
        inst = installer.store().get_install(out["install"]["id"])
        fp = inst["fingerprint"]
        self.assertEqual(len(fp["soul"]), 64)
        self.assertEqual(len(fp["config"]), 64)
        self.assertEqual(fp["skills"], [])
        self.assertEqual(inst["catalog_commit"], catalog.load()["siteCommit"])

    def test_nothing_secret_is_recorded(self):
        _, out = self.add("scout")
        dump = json.dumps(installer.store().get_install(out["install"]["id"]))
        self.assertNotIn(".env", dump)
        db = (Path(self._tmp.name) / "cabinet.db").read_bytes()
        self.assertNotIn(b"api_key", db.lower())

    def test_reconcile_adopts_a_profile_the_app_never_confirmed(self):
        plan = installer.plan_bot("scout")
        gateway_create(plan["create"]["params"])  # the app closed before it called confirm
        mine = installer.my_bots()
        self.assertEqual([b["profile"] for b in mine["bots"]], ["scout"])
        self.assertEqual(installer.store().get_plan(plan["planId"])["status"], "done")

    def test_reconcile_ignores_a_profile_with_other_instructions(self):
        plan = installer.plan_bot("scout")
        gateway_create({**plan["create"]["params"], "soul": "You are someone else entirely. Do other things."})
        self.assertEqual(installer.my_bots()["bots"], [])

    def test_reconcile_ignores_plans_that_never_got_a_profile(self):
        installer.plan_bot("scout")
        self.assertEqual(installer.my_bots()["bots"], [])

    def test_reconcile_does_not_double_record(self):
        plan, _ = self.add("scout")
        installer.my_bots()
        installer.my_bots()
        self.assertEqual(len(installer.store().installs()), 1)

    def test_a_lab_plan_is_recorded_the_same_way(self):
        plan = installer.plan_lab(display="Lookout", description="Watch pricing pages", soul="# Lookout\n\nYou are Lookout. Watch pricing pages weekly.\n",
                                  profile="lookout", job="Watch pricing pages")
        self.assertTrue(plan["ready"])
        gateway_create(plan["create"]["params"])
        out = installer.confirm(plan["planId"])
        self.assertEqual(out["install"]["kind"], "lab")
        self.assertTrue(out["install"]["canRemove"])


class Touched(Base):
    def made(self):
        _, out = self.add("scout")
        return out["install"]["id"], self.root() / "scout"

    def reasons(self, iid):
        return [v for v in installer.my_bots()["bots"] if v["id"] == iid][0]["reasons"]

    def test_fresh_profile_is_untouched(self):
        iid, _ = self.made()
        self.assertEqual(self.reasons(iid), [])

    def test_edited_instructions(self):
        iid, d = self.made()
        (d / "SOUL.md").write_text("changed by me\n")
        self.assertIn("instructions", self.reasons(iid)[0])

    def test_changed_settings(self):
        iid, d = self.made()
        (d / "config.yaml").write_text("model:\n  provider: other\n  default: other\n")
        self.assertTrue(any("settings" in r for r in self.reasons(iid)))

    def test_added_skill(self):
        iid, d = self.made()
        (d / "skills" / "mine").mkdir()
        (d / "skills" / "mine" / "SKILL.md").write_text("x")
        self.assertTrue(any("Skills" in r for r in self.reasons(iid)))

    def test_memories(self):
        iid, d = self.made()
        (d / "memories" / "note.md").write_text("remember this")
        self.assertTrue(any("memories" in r for r in self.reasons(iid)))

    def test_hidden_files_in_memories_do_not_count(self):
        iid, d = self.made()
        (d / "memories" / ".keep").write_text("")
        self.assertEqual(self.reasons(iid), [])

    def test_scheduled_jobs(self):
        iid, d = self.made()
        (d / "cron" / "jobs.json").write_text(json.dumps({"jobs": [{"id": "1"}]}))
        self.assertTrue(any("scheduled" in r for r in self.reasons(iid)))

    def test_an_empty_jobs_file_is_fine(self):
        iid, d = self.made()
        (d / "cron" / "jobs.json").write_text(json.dumps({"jobs": []}))
        self.assertEqual(self.reasons(iid), [])

    def test_unreadable_jobs_file_counts_as_touched(self):
        iid, d = self.made()
        (d / "cron" / "jobs.json").write_text("{ nope")
        self.assertTrue(self.reasons(iid))

    def test_session_files(self):
        iid, d = self.made()
        (d / "sessions" / "s1.json").write_text("{}")
        self.assertTrue(any("chatted" in r for r in self.reasons(iid)))

    def test_a_chat_in_the_state_database(self):
        iid, d = self.made()
        # closing(): a sqlite3 connection's own `with` only commits; left open, Windows can't delete the profile after.
        with closing(sqlite3.connect(d / "state.db")) as db, db:
            db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT)")
            db.execute("INSERT INTO messages(role) VALUES ('assistant')")
        self.assertEqual(self.reasons(iid), [])  # Hermes's own greeting isn't you chatting
        with closing(sqlite3.connect(d / "state.db")) as db, db:
            db.execute("INSERT INTO messages(role) VALUES ('user')")
        self.assertTrue(any("chatted" in r for r in self.reasons(iid)))

    def test_a_state_database_cabinet_cannot_read_is_not_a_reason(self):
        iid, d = self.made()
        (d / "state.db").write_text("not a database")
        self.assertEqual(self.reasons(iid), [])

    def test_a_missing_profile(self):
        iid, d = self.made()
        shutil.rmtree(d)
        view = [v for v in installer.my_bots()["bots"] if v["id"] == iid][0]
        self.assertEqual(view["status"], "missing")
        self.assertTrue(view["canForget"])


class Removal(Base):
    def made(self, slug="scout"):
        _, out = self.add(slug)
        return out["install"]["id"], self.root() / slug

    def test_untouched_profile_is_removed_through_hermes(self):
        iid, d = self.made()
        preview = installer.removal_preview("bot", iid)
        self.assertEqual((preview["canRemove"], preview["will"]), (True, ["scout"]))
        out = installer.remove("bot", iid, True)
        self.assertEqual(out["result"], "removed")
        self.assertFalse(d.exists())
        self.delete.assert_called_once_with("scout")
        self.assertEqual(out["mybots"]["bots"], [])
        self.assertEqual(installer.store().get_install(iid)["status"], "removed")

    def test_a_confirmation_is_required(self):
        iid, d = self.made()
        for flag in (None, False, "true", "yes", 1, 0, {}, []):
            self.assertEqual(self.api_error(installer.remove, "bot", iid, flag).status_code, 400)
        self.assertTrue(d.exists())
        self.delete.assert_not_called()

    def test_an_edited_profile_is_not_removed(self):
        iid, d = self.made()
        (d / "SOUL.md").write_text("I changed this\n")
        preview = installer.removal_preview("bot", iid)
        self.assertFalse(preview["canRemove"])
        self.assertTrue(preview["keep"][0]["why"])
        err = self.api_error(installer.remove, "bot", iid, True)
        self.assertEqual(err.status_code, 409)
        self.assertTrue(err.detail["reasons"])
        self.assertTrue(d.exists())
        self.delete.assert_not_called()

    def test_a_profile_cabinet_did_not_make_cannot_be_removed(self):
        make_foreign_profile("mine")
        for iid in (1, 2, 999, -1):
            self.assertEqual(self.api_error(installer.remove, "bot", iid, True).status_code, 404)
        self.delete.assert_not_called()
        self.assertTrue((self.root() / "mine").exists())

    def test_removal_is_aimed_at_a_record_number_never_a_name(self):
        make_foreign_profile("mine")
        with self.assertRaises(ValueError):
            installer.remove("bot", int("mine"), True)  # the page backend turns the id into a number first
        self.delete.assert_not_called()

    def test_unknown_kind(self):
        self.assertEqual(self.api_error(installer.remove, "profile", 1, True).status_code, 404)

    def test_removed_twice(self):
        iid, _ = self.made()
        installer.remove("bot", iid, True)
        self.assertEqual(self.api_error(installer.remove, "bot", iid, True).status_code, 404)

    def test_a_failed_delete_keeps_the_record(self):
        iid, d = self.made()
        self.delete.side_effect = hermes.HermesError("Hermes couldn't remove it: busy")
        err = self.api_error(installer.remove, "bot", iid, True)
        self.assertEqual(err.status_code, 502)
        self.assertEqual(installer.store().get_install(iid)["status"], "active")

    def test_a_delete_that_leaves_the_folder_is_an_error(self):
        iid, d = self.made()
        self.delete.side_effect = lambda name: None
        self.assertEqual(self.api_error(installer.remove, "bot", iid, True).status_code, 502)
        self.assertEqual(installer.store().get_install(iid)["status"], "active")

    def test_a_profile_deleted_in_hermes_is_just_forgotten(self):
        iid, d = self.made()
        shutil.rmtree(d)
        out = installer.remove("bot", iid, True)
        self.assertEqual(out["result"], "forgotten")
        self.delete.assert_not_called()

    def test_forget_needs_the_profile_to_be_gone(self):
        iid, d = self.made()
        self.assertEqual(self.api_error(installer.forget, iid).status_code, 409)
        shutil.rmtree(d)
        installer.forget(iid)
        self.assertEqual(installer.my_bots()["bots"], [])

    def test_a_tampered_record_pointing_elsewhere_is_never_deleted(self):
        iid, d = self.made()
        other = make_foreign_profile("precious")
        db = sqlite3.connect(Path(self._tmp.name) / "cabinet.db")
        db.execute("UPDATE installs SET path=? WHERE id=?", (str(other), iid))
        db.commit()
        db.close()
        # the fingerprint no longer matches the other folder, so it counts as changed and stays
        with self.assertRaises(errors.ApiError):
            installer.remove("bot", iid, True)
        self.assertTrue(other.exists())
        self.delete.assert_not_called()

    def test_a_record_with_a_matching_fingerprint_but_the_wrong_path_is_released_not_deleted(self):
        iid, d = self.made()
        twin = self.root() / "twin"
        shutil.copytree(d, twin)
        db = sqlite3.connect(Path(self._tmp.name) / "cabinet.db")
        db.execute("UPDATE installs SET path=? WHERE id=?", (str(twin), iid))
        db.commit()
        db.close()
        installer.remove("bot", iid, True)
        self.delete.assert_not_called()
        self.assertTrue(twin.exists() and d.exists())
        self.assertEqual(installer.store().get_install(iid)["status"], "released")

    def test_a_removed_profile_name_can_be_used_again(self):
        iid, _ = self.made()
        installer.remove("bot", iid, True)
        plan = installer.plan_bot("scout")
        self.assertEqual(plan["profile"], "scout")

    def test_removal_is_in_the_history(self):
        iid, _ = self.made()
        installer.remove("bot", iid, True)
        self.assertEqual(installer.my_bots()["history"][-1]["action"], "remove")

    def test_removing_one_bot_leaves_the_others(self):
        a, _ = self.made("scout")
        b, d = self.made("writer")
        installer.remove("bot", a, True)
        self.assertEqual([v["profile"] for v in installer.my_bots()["bots"]], ["writer"])
        self.assertTrue(d.exists())


class Crews(Base):
    SLUG = "personal-planning-desk"

    def crew(self):
        return catalog.crew(self.SLUG)

    def setup_crew(self, choices=None, only=None):
        plan = installer.plan_crew(self.SLUG, choices)
        self.assertTrue(plan["ready"], plan["problems"])
        made = []
        for m in plan["members"]:
            if m["mode"] == "create" and (only is None or m["slug"] in only):
                gateway_create(m["create"]["params"])
                made.append(m["profile"])
        return plan, made

    def test_plan_lists_every_bot_the_order_and_the_stops(self):
        plan = installer.plan_crew(self.SLUG)
        crew = self.crew()
        self.assertEqual([m["slug"] for m in plan["members"]], [m["slug"] for m in crew["members"]])
        self.assertEqual(len(plan["steps"]), len(crew["handoffOrder"]))
        self.assertTrue(any(s["you"] for s in plan["steps"]))  # a point where it stops for the owner
        for m in plan["members"]:
            self.assertEqual(m["mode"], "create")
            self.assertTrue(m["responsibility"])
        self.assertEqual(plan["willCreate"], len(crew["members"]))
        self.assertEqual(plan["willReuse"], 0)

    def test_the_honest_account_is_there(self):
        honest = installer.plan_crew(self.SLUG)["honest"]
        self.assertTrue(any("Run the crew by itself" in s for s in honest["cannot"]))
        self.assertTrue(any("Kanban card" in s for s in honest["can"]))
        self.assertIn("blocked", honest["cards"])

    def test_the_planning_desk_does_not_claim_it_can_run_by_itself(self):
        text = json.dumps(installer.what_hermes_does()).lower()
        self.assertNotIn("runs the crew", text)
        self.assertNotIn("automatically hand", text)

    def test_unknown_crew(self):
        self.assertEqual(self.api_error(installer.plan_crew, "ghost").status_code, 404)

    def test_each_member_is_a_checked_profile_plan(self):
        for m in installer.plan_crew(self.SLUG)["members"]:
            params = m["create"]["params"]
            self.assertEqual(params["name"], m["profile"])
            self.assertNotIn("model", params)
            self.assertTrue(params["no_alias"])
            self.assertEqual(params["soul"], safety.instructions(params["soul"]))

    def test_member_instructions_name_the_crew_and_its_neighbours(self):
        plan = installer.plan_crew(self.SLUG)
        names = [m["name"] for m in plan["members"]]
        mid = plan["members"][1]["soul"]
        self.assertIn(f"Your place in the {plan['crew']['name']} crew", mid)
        self.assertIn(names[0], mid)
        self.assertIn(names[2], mid)
        self.assertIn("Stop and wait for the owner", mid)

    def test_a_bot_you_already_have_is_reused_by_default(self):
        make_foreign_profile("planner")
        plan = installer.plan_crew(self.SLUG)
        planner = [m for m in plan["members"] if m["slug"] == "planner"][0]
        self.assertEqual((planner["mode"], planner["profile"]), ("existing", "planner"))
        self.assertIn("planner", planner["candidates"])
        self.assertEqual(plan["willReuse"], 1)
        self.assertEqual(plan["willCreate"], len(plan["members"]) - 1)

    def test_you_can_choose_to_make_a_new_one_instead(self):
        make_foreign_profile("planner")
        plan = installer.plan_crew(self.SLUG, {"planner": {"mode": "create"}})
        planner = [m for m in plan["members"] if m["slug"] == "planner"][0]
        self.assertEqual((planner["mode"], planner["profile"]), ("create", "planner-2"))

    def test_you_can_pick_a_different_existing_profile(self):
        make_foreign_profile("planner")
        make_foreign_profile("my-organiser")
        plan = installer.plan_crew(self.SLUG, {"planner": {"mode": "existing", "profile": "my-organiser"}})
        planner = [m for m in plan["members"] if m["slug"] == "planner"][0]
        self.assertEqual(planner["profile"], "my-organiser")

    def test_picking_a_profile_that_does_not_exist(self):
        plan = installer.plan_crew(self.SLUG, {"planner": {"mode": "existing", "profile": "ghost"}})
        self.assertFalse(plan["ready"])
        self.assertIsNone(plan["planId"])

    def test_two_bots_cannot_share_one_profile(self):
        make_foreign_profile("planner")
        plan = installer.plan_crew(self.SLUG, {m["slug"]: {"mode": "existing", "profile": "planner"} for m in self.crew()["members"]})
        self.assertFalse(plan["ready"])
        self.assertTrue(any("already used" in p["message"] for p in plan["problems"]))

    def test_a_typed_new_name_is_checked(self):
        slug = self.crew()["members"][0]["slug"]
        plan = installer.plan_crew(self.SLUG, {slug: {"mode": "create", "profile": "../x"}})
        self.assertFalse(plan["ready"])
        make_foreign_profile("taken-name")
        plan = installer.plan_crew(self.SLUG, {slug: {"mode": "create", "profile": "taken-name"}})
        self.assertFalse(plan["ready"])

    def test_two_new_profiles_cannot_get_one_name(self):
        slugs = [m["slug"] for m in self.crew()["members"]]
        plan = installer.plan_crew(self.SLUG, {slugs[0]: {"mode": "create", "profile": "same"}, slugs[1]: {"mode": "create", "profile": "same"}})
        names = [m["profile"] for m in plan["members"]]
        self.assertEqual(len(names), len(set(names)))

    def test_choices_that_are_junk_are_ignored(self):
        for junk in ("x", 5, ["a"], {"planner": "existing"}, {"planner": {"mode": "delete"}}):
            plan = installer.plan_crew(self.SLUG, junk)
            self.assertTrue(plan["ready"])

    def test_planning_a_crew_writes_nothing(self):
        before = self.listing()
        installer.plan_crew(self.SLUG)
        self.assertEqual(self.listing(), before)

    def test_every_published_crew_plans_on_a_fresh_machine(self):
        for c in catalog.crews():
            plan = installer.plan_crew(c["slug"])
            self.assertTrue(plan["ready"], (c["slug"], plan["problems"]))

    def test_full_set_up_and_record(self):
        make_foreign_profile("planner")
        plan, made = self.setup_crew()
        out = installer.confirm_crew(plan["planId"], made)
        self.assertTrue(out["ok"])
        crew = out["crew"]
        self.assertEqual(crew["status"], "active")
        self.assertEqual(sum(1 for m in crew["members"] if m["mode"] == "created"), len(made))
        self.assertEqual(sum(1 for m in crew["members"] if m["mode"] == "existing"), 1)
        mine = installer.my_bots()
        self.assertEqual(len(mine["crews"]), 1)
        self.assertEqual(mine["bots"], [])  # crew members are listed under the crew, not as single bots

    def test_crew_members_cannot_be_removed_one_by_one(self):
        plan, made = self.setup_crew()
        out = installer.confirm_crew(plan["planId"], made)
        member_install = out["crew"]["members"][0]["installId"]
        self.assertEqual(self.api_error(installer.remove, "bot", member_install, True).status_code, 409)

    def test_removing_a_crew_removes_what_cabinet_made_and_keeps_what_was_yours(self):
        mine_already = make_foreign_profile("planner")
        plan, made = self.setup_crew()
        out = installer.confirm_crew(plan["planId"], made)
        result = installer.remove("crew", out["crew"]["id"], True)
        self.assertEqual(sorted(result["removed"]), sorted(made))
        self.assertTrue(mine_already.exists())
        for name in made:
            self.assertFalse((self.root() / name).exists())
        self.assertEqual(result["mybots"]["crews"], [])
        called = sorted(c.args[0] for c in self.delete.call_args_list)
        self.assertEqual(called, sorted(made))
        self.assertNotIn("planner", called)

    def test_a_changed_crew_member_stays(self):
        plan, made = self.setup_crew()
        out = installer.confirm_crew(plan["planId"], made)
        (self.root() / made[0] / "SOUL.md").write_text("mine now\n")
        preview = installer.removal_preview("crew", out["crew"]["id"])
        self.assertEqual([k["profile"] for k in preview["keep"]], [made[0]])
        result = installer.remove("crew", out["crew"]["id"], True)
        self.assertEqual(result["kept"], [made[0]])
        self.assertTrue((self.root() / made[0]).exists())
        self.assertEqual(sorted(result["removed"]), sorted(made[1:]))

    def test_a_partly_made_crew_is_recorded_as_not_finished(self):
        plan, made = self.setup_crew(only={self.crew()["members"][0]["slug"]})
        out = installer.confirm_crew(plan["planId"], made)
        self.assertFalse(out["ok"])
        self.assertEqual(out["crew"]["status"], "incomplete")
        self.assertEqual(len(out["missing"]), len(self.crew()["members"]) - 1)

    def test_finishing_a_crew_picks_up_where_it_left_off(self):
        plan, made = self.setup_crew(only={self.crew()["members"][0]["slug"]})
        first = installer.confirm_crew(plan["planId"], made)
        second_plan = installer.plan_crew(self.SLUG)
        resumed = [m for m in second_plan["members"] if m["resume"]]
        self.assertEqual([m["profile"] for m in resumed], made)
        self.assertTrue(all(m["mode"] == "existing" for m in resumed))
        rest = [m["profile"] for m in second_plan["members"] if m["mode"] == "create"]
        for m in second_plan["members"]:
            if m["mode"] == "create":
                gateway_create(m["create"]["params"])
        out = installer.confirm_crew(second_plan["planId"], rest)
        self.assertEqual(out["crew"]["status"], "active")
        self.assertEqual(installer.store().get_crew(first["crew"]["id"])["status"], "superseded")
        self.assertEqual(sum(1 for m in out["crew"]["members"] if m["mode"] == "created"), len(self.crew()["members"]))
        removed = installer.remove("crew", out["crew"]["id"], True)
        self.assertEqual(len(removed["removed"]), len(self.crew()["members"]))

    def test_a_member_hermes_made_after_the_page_gave_up_still_counts(self):
        # The page's request timed out, so it didn't list the profile, but Hermes finished making it.
        plan, made = self.setup_crew()
        out = installer.confirm_crew(plan["planId"], made[:-1])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["crew"]["status"], "active")
        self.assertIsNotNone(installer.store().active_install_for(made[-1]))

    def test_an_unreported_profile_with_other_instructions_is_not_claimed(self):
        plan, made = self.setup_crew()
        (self.root() / made[-1] / "SOUL.md").write_text("Someone else's bot.\n")
        out = installer.confirm_crew(plan["planId"], made[:-1])
        self.assertFalse(out["ok"])
        self.assertIsNone(installer.store().active_install_for(made[-1]))

    def test_a_reused_profile_comes_with_its_crew_part_to_paste_in(self):
        make_foreign_profile("planner")
        plan = installer.plan_crew(self.SLUG)
        planner = [m for m in plan["members"] if m["slug"] == "planner"][0]
        self.assertEqual(planner["mode"], "existing")
        self.assertIn("## Your place in the Personal Planning Desk crew", planner["brief"])
        self.assertIn("Step 1 of 4 (Define) is yours", planner["brief"])

    def test_confirm_ignores_profiles_the_plan_did_not_name(self):
        make_foreign_profile("not-in-the-plan")
        plan, made = self.setup_crew()
        out = installer.confirm_crew(plan["planId"], made + ["not-in-the-plan"])
        self.assertEqual(installer.store().active_install_for("not-in-the-plan"), None)
        self.assertTrue(out["ok"])

    def test_a_profile_that_was_there_first_is_not_recorded(self):
        plan = installer.plan_crew(self.SLUG)
        for m in plan["members"]:
            gateway_create(m["create"]["params"])
        with mock.patch.object(installer, "_born_after", return_value=False):
            err = self.api_error(installer.confirm_crew, plan["planId"], [m["profile"] for m in plan["members"]])
        self.assertEqual(err.status_code, 409)
        self.assertEqual(installer.store().installs(), [])
        self.assertEqual(installer.store().crews(), [])

    def test_an_existing_profile_that_vanished_is_a_conflict(self):
        d = make_foreign_profile("planner")
        plan, made = self.setup_crew()
        shutil.rmtree(d)
        self.assertEqual(self.api_error(installer.confirm_crew, plan["planId"], made).status_code, 409)

    def test_confirm_crew_twice_and_unknown(self):
        plan, made = self.setup_crew()
        installer.confirm_crew(plan["planId"], made)
        self.assertEqual(self.api_error(installer.confirm_crew, plan["planId"], made).status_code, 409)
        self.assertEqual(self.api_error(installer.confirm_crew, "nope", made).status_code, 404)
        single = installer.plan_bot("scout")
        self.assertEqual(self.api_error(installer.confirm_crew, single["planId"], []).status_code, 404)

    def test_junk_created_lists(self):
        plan, made = self.setup_crew()
        out = installer.confirm_crew(plan["planId"], made + [None, 5, {"a": 1}])
        self.assertTrue(out["ok"])

    def test_a_plan_cannot_be_confirmed_with_nothing_created(self):
        plan = installer.plan_crew(self.SLUG)
        out = installer.confirm_crew(plan["planId"], [])
        self.assertFalse(out["ok"])
        self.assertEqual(out["crew"]["status"], "incomplete")

    def test_all_existing_crew_needs_nothing_made(self):
        for m in self.crew()["members"]:
            make_foreign_profile(m["slug"])
        plan = installer.plan_crew(self.SLUG)
        self.assertEqual(plan["willCreate"], 0)
        out = installer.confirm_crew(plan["planId"], [])
        self.assertTrue(out["ok"])
        installer.remove("crew", out["crew"]["id"], True)
        self.delete.assert_not_called()
        for m in self.crew()["members"]:
            self.assertTrue((self.root() / m["slug"]).exists())


class KanbanCards(Base):
    SLUG = "personal-planning-desk"

    def active_crew(self):
        plan = installer.plan_crew(self.SLUG)
        made = []
        for m in plan["members"]:
            gateway_create(m["create"]["params"])
            made.append(m["profile"])
        return installer.confirm_crew(plan["planId"], made)["crew"]

    def test_the_cards_plan_is_a_preview(self):
        crew = self.active_crew()
        with mock.patch.object(hermes, "kanban_available", return_value=True):
            plan = installer.cards_plan(crew["id"])
        self.assertTrue(plan["requiresConfirm"])
        self.assertFalse(plan["created"])
        self.assertEqual(len(plan["cards"]), len(crew["steps"]))
        self.assertIn("blocked", plan["note"])
        assignees = [c["assignee"] for c in plan["cards"] if c["assignee"]]
        self.assertTrue(set(assignees) <= {m["profile"] for m in crew["members"]})

    def test_cards_are_only_made_after_a_confirmation(self):
        crew = self.active_crew()
        with mock.patch.object(hermes, "kanban_available", return_value=True), \
                mock.patch.object(hermes, "create_cards", return_value=["t1"]) as make:
            for flag in (None, False, "true", "yes", 1, {}, []):
                self.assertEqual(self.api_error(installer.create_cards, crew["id"], flag).status_code, 400)
            make.assert_not_called()

    def test_confirmed_cards_are_made_once_in_order_and_recorded(self):
        crew = self.active_crew()
        with mock.patch.object(hermes, "kanban_available", return_value=True), \
                mock.patch.object(hermes, "create_cards", side_effect=lambda cards: [f"t{i}" for i, _ in enumerate(cards)]) as make:
            out = installer.create_cards(crew["id"], True)
            cards = make.call_args.args[0]
            self.assertEqual([c["title"].split(": ", 1)[1] for c in cards], [s["step"] for s in crew["steps"]])
            self.assertEqual(len({c["key"] for c in cards}), len(cards))
            self.assertTrue(all(c["body"].count("starts blocked") == 1 for c in cards))
            self.assertTrue(any("stops for the owner" in c["body"] for c in cards))
            self.assertEqual(out["cardIds"], [f"t{i}" for i in range(len(cards))])
            self.assertEqual(self.api_error(installer.create_cards, crew["id"], True).status_code, 409)
        self.assertTrue(installer.my_bots()["crews"][0]["cardsMade"])

    def test_no_kanban_means_a_plain_refusal(self):
        crew = self.active_crew()
        with mock.patch.object(hermes, "kanban_available", return_value=False):
            self.assertFalse(installer.cards_plan(crew["id"])["available"])
            self.assertEqual(self.api_error(installer.create_cards, crew["id"], True).status_code, 409)

    def test_a_kanban_failure_is_reported_and_nothing_is_recorded(self):
        crew = self.active_crew()
        with mock.patch.object(hermes, "kanban_available", return_value=True), \
                mock.patch.object(hermes, "create_cards", side_effect=hermes.HermesError("Kanban refused a card: no")):
            self.assertEqual(self.api_error(installer.create_cards, crew["id"], True).status_code, 502)
        self.assertFalse(installer.store().get_crew(crew["id"])["cards"])

    def test_an_unfinished_crew_has_no_cards_yet(self):
        plan = installer.plan_crew(self.SLUG)
        crew = installer.confirm_crew(plan["planId"], [])["crew"]
        with mock.patch.object(hermes, "kanban_available", return_value=True):
            self.assertEqual(self.api_error(installer.create_cards, crew["id"], True).status_code, 409)

    def test_removing_the_crew_archives_its_cards(self):
        crew = self.active_crew()
        with mock.patch.object(hermes, "kanban_available", return_value=True), \
                mock.patch.object(hermes, "create_cards", return_value=["t1", "t2"]):
            installer.create_cards(crew["id"], True)
        with mock.patch.object(hermes, "archive_cards", return_value=2) as archive:
            out = installer.remove("crew", crew["id"], True)
        archive.assert_called_once_with(["t1", "t2"])
        self.assertEqual(out["cardsArchived"], 2)

    def test_unknown_crew_has_no_cards(self):
        self.assertEqual(self.api_error(installer.cards_plan, 999).status_code, 404)


class Kits(Base):
    FIELDS = {"botName": "Lookout", "jobOutcome": "Watch three pricing pages and summarise changes weekly", "inputsContext": "Three links",
              "outputsDeliverables": "A short summary", "cadenceTrigger": "When I ask", "toolsIntegrations": "Web pages only",
              "approvalBoundaries": "Before contacting anyone", "firstRunTest": "Summarise one page", "prohibitedUncertainty": "Say what is missing",
              "continuityMemory": "Nothing"}

    def test_a_kit_is_saved_and_listed(self):
        out = installer.save_kit("Lookout", self.FIELDS, "Watch pricing")
        self.assertTrue(out["ok"])
        mine = installer.my_bots()
        self.assertEqual([k["name"] for k in mine["kits"]], ["Lookout"])
        self.assertEqual(mine["kits"][0]["fields"]["botName"], "Lookout")

    def test_a_kit_is_not_a_profile(self):
        before = self.listing()
        installer.save_kit("Lookout", self.FIELDS, "x")
        self.assertEqual(self.listing(), before)
        self.assertEqual(installer.store().installs(), [])

    def test_removing_a_kit_touches_no_profile(self):
        kid = installer.save_kit("Lookout", self.FIELDS, "x")["kit"]["id"]
        preview = installer.removal_preview("kit", kid)
        self.assertEqual(preview["will"], [])
        installer.remove("kit", kid, True)
        self.delete.assert_not_called()
        self.assertEqual(installer.my_bots()["kits"], [])

    def test_bad_kits_are_refused_with_every_reason(self):
        err = self.api_error(installer.save_kit, "!!", {**self.FIELDS, "firstRunTest": "key sk-abcdefghijklmnopqrstuvwxyz123456"}, "x")
        self.assertEqual(err.status_code, 422)
        self.assertGreaterEqual(len(err.detail["problems"]), 2)
        self.assertEqual(installer.store().kits(), [])

    def test_a_kit_with_a_secret_is_refused_and_the_message_does_not_repeat_it(self):
        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        err = self.api_error(installer.save_kit, "Lookout", {**self.FIELDS, "toolsIntegrations": f"use {secret}"}, "x")
        self.assertNotIn(secret, json.dumps(err.detail))

    def test_oversize_kit(self):
        big = {k: "x" * 900 for k in ("a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8", "a9", "b1", "b2", "b3", "b4", "b5")}
        self.assertEqual(self.api_error(installer.save_kit, "Big", big, "x").status_code, 422)

    def test_non_text_fields_are_dropped(self):
        installer.save_kit("Lookout", {**self.FIELDS, "model": 5, "x" * 50: "long key"}, "x")
        saved = installer.store().kits()[0]["fields"]
        self.assertNotIn("model", saved)
        self.assertNotIn("x" * 50, saved)


class Views(Base):
    def test_my_bots_starts_empty(self):
        mine = installer.my_bots()
        self.assertEqual((mine["bots"], mine["crews"], mine["kits"]), ([], [], []))
        self.assertEqual(mine["where"], str(self.root()))

    def test_a_status_text_for_each_state(self):
        _, out = self.add("scout")
        self.assertEqual(out["install"]["statusText"], "Made by Cabinet, not changed yet")

    def test_everything_is_json_serialisable(self):
        self.add("scout")
        installer.save_kit("Lookout", Kits.FIELDS, "x")
        json.dumps(installer.my_bots())

    def test_a_crew_view_never_lists_released_profiles_as_removable(self):
        plan = installer.plan_crew("personal-planning-desk")
        made = []
        for m in plan["members"]:
            gateway_create(m["create"]["params"])
            made.append(m["profile"])
        crew = installer.confirm_crew(plan["planId"], made)["crew"]
        (self.root() / made[0] / "memories" / "n.md").write_text("x")
        view = installer.my_bots()["crews"][0]
        self.assertEqual(view["changedCount"], 1)


class Soul(unittest.TestCase):
    def test_a_bot_without_a_role_still_gets_a_sensible_one(self):
        bot = dict(catalog.bot("scout"), role="", skills=[], routine=None, credits=[])
        soul = installer.bot_soul(bot)
        self.assertIn("You are Scout", soul)
        self.assertIn("do not run on a schedule", soul)

    def test_credit_lines_are_included(self):
        soul = installer.bot_soul(catalog.bot("ops"))
        self.assertIn("Muse at Work", soul)


class CrewPlacement(unittest.TestCase):
    """A crew member's instructions follow the crew's handoff order, not the order its members are listed in."""

    def soul(self, crew_slug, bot_slug):
        crew = catalog.crew(crew_slug)
        return installer.bot_soul(catalog.bot(bot_slug), installer.crew_context(crew, bot_slug))

    def test_researcher_in_the_planning_desk_sits_between_planner_and_coach(self):
        # Listed Researcher, Planner, Coach; the handoff order is Planner (with you), Researcher, Coach, you.
        soul = self.soul("personal-planning-desk", "researcher")
        self.assertIn("Step 2 of 4 (Research) is yours.", soul)
        self.assertIn("Work reaches you from step 1: Planner and the owner.", soul)
        self.assertIn("Your result goes next to step 3: Coach.", soul)
        self.assertNotIn("hand the result to: Planner", soul)

    def test_the_first_step_says_the_owner_starts_it(self):
        soul = self.soul("personal-planning-desk", "planner")
        self.assertIn("Step 1 of 4 (Define) is yours, together with the owner. It is the first step, so the owner starts it.", soul)

    def test_a_bot_with_two_steps_gets_both(self):
        soul = self.soul("event-operations-desk", "ops")
        self.assertIn("Step 3 of 4 (Readiness) is yours, together with the owner.", soul)
        self.assertIn("Step 4 of 4 (Closeout) is yours, together with the owner.", soul)
        self.assertIn("It is the last step.", soul)

    def test_every_member_of_every_crew_has_a_step_and_the_honest_line(self):
        for crew in catalog.crews():
            for m in crew["members"]:
                ctx = installer.crew_context(crew, m["slug"])
                self.assertTrue(ctx["mine"], f"{crew['slug']}: {m['slug']} has no step")
                soul = installer.bot_soul(catalog.bot(m["slug"]), ctx)
                self.assertIn("Hermes does not pass work between the bots in a crew.", soul)
                self.assertEqual(len(ctx["order"]), len(crew["handoffOrder"]))

    def test_a_hiring_manager_or_a_learner_is_a_person_step(self):
        hiring = installer.handoff_steps(catalog.crew("hiring-support-desk"))
        self.assertTrue(hiring[3]["you"])
        self.assertEqual(hiring[3]["owner"], "You (as hiring manager)")
        learning = installer.handoff_steps(catalog.crew("learning-certification-crew"))
        self.assertTrue(learning[3]["you"])
        self.assertEqual(learning[3]["owner"], "Planner + you")

    def test_every_member_carries_the_crews_own_limits(self):
        # The crew page shows "Asks you before" and "Off limits" for the whole crew; each bot must be told them too.
        for crew in catalog.crews():
            for m in crew["members"]:
                soul = installer.bot_soul(catalog.bot(m["slug"]), installer.crew_context(crew, m["slug"]))
                for item in crew["approvalActions"] + crew["prohibitedActions"]:
                    self.assertIn(f"- {item}", soul, f"{crew['slug']}/{m['slug']} misses {item!r}")

    def test_bots_at_a_step_are_in_the_order_the_step_names_them(self):
        steps = installer.handoff_steps(catalog.crew("hiring-support-desk"))
        self.assertEqual(steps[0]["bots"], ["writer", "planner"])



class NeverTakesOverHermesBots(Base):
    """The 0.3.2 bug: after a failed create, confirm still ran and _verify_created accepted any folder of the planned
    name made after the plan, rewrote its SOUL.md and recorded it as Cabinet's. That could take over a bot Hermes's
    own Bots catalog had just installed under the same name."""

    def soul_of(self, name):
        return (self.root() / name / "SOUL.md").read_text()

    def test_a_failed_create_never_rewrites_instructions(self):
        plan = installer.plan_bot("scout")
        make_foreign_profile("scout", "You are someone else's scout.\n")  # appeared after the plan, not Cabinet's
        err = self.api_error(installer.confirm, plan["planId"], created=False)
        self.assertEqual(err.status_code, 409)
        self.assertEqual(self.soul_of("scout"), "You are someone else's scout.\n")
        self.assertEqual(installer.store().installs(), [])

    def test_a_lost_reply_is_still_recorded_when_the_profile_is_exactly_the_plan(self):
        plan = installer.plan_bot("scout")
        gateway_create(plan["create"]["params"])  # Hermes finished; the reply never came back
        out = installer.confirm(plan["planId"], created=False)
        self.assertEqual(out["install"]["profile"], "scout")
        self.assertEqual(out["note"], "")

    def test_a_lost_reply_with_unsaved_instructions_is_not_repaired(self):
        plan = installer.plan_bot("scout")
        gateway_create(plan["create"]["params"], write_soul=False)
        self.assertEqual(self.api_error(installer.confirm, plan["planId"], created=False).status_code, 409)
        self.assertNotEqual(self.soul_of("scout"), plan["soul"])
        self.assertEqual(installer.store().installs(), [])

    def test_a_hermes_bots_profile_is_never_recorded_or_written_even_after_a_successful_create(self):
        for kw in ({}, {"blueprint": False}, {"provenance": False}):
            with self.subTest(**kw):
                wipe_profiles()
                plan = installer.plan_bot("scout")
                hermes_bots_install("scout", "scout", "Reviewed instructions from Hermes.\n", **kw)
                err = self.api_error(installer.confirm, plan["planId"])  # created defaults to true
                self.assertEqual(err.status_code, 409)
                self.assertTrue(err.detail.get("hermesBots"))
                self.assertIn("Hermes Bots added", err.detail["message"])
                self.assertEqual(self.soul_of("scout"), "Reviewed instructions from Hermes.\n")
                self.assertEqual(installer.store().installs(), [])

    def test_a_hermes_bots_profile_with_the_planned_instructions_is_still_not_cabinets(self):
        plan = installer.plan_bot("scout")
        hermes_bots_install("scout", "scout", plan["soul"])
        self.assertEqual(self.api_error(installer.confirm, plan["planId"], created=False).status_code, 409)
        self.assertEqual(installer.my_bots()["bots"], [])  # reconcile leaves it alone too
        self.assertEqual(installer.store().get_plan(plan["planId"])["status"], "pending")

    def test_a_crew_never_adopts_a_hermes_bots_profile(self):
        plan = installer.plan_crew("personal-planning-desk")
        first = plan["members"][0]
        hermes_bots_install(first["slug"], first["profile"], first["soul"])
        made = []
        for m in plan["members"][1:]:
            gateway_create(m["create"]["params"])
            made.append(m["profile"])
        out = installer.confirm_crew(plan["planId"], made)
        self.assertEqual(out["missing"], [first["name"]])
        recorded = {i["profile"] for i in installer.store().installs()}
        self.assertNotIn(first["profile"], recorded)
        self.assertEqual(self.soul_of(first["profile"]), first["soul"])

    def test_a_crew_member_reported_made_but_carrying_a_blueprint_is_refused(self):
        plan = installer.plan_crew("personal-planning-desk")
        first = plan["members"][0]
        hermes_bots_install(first["slug"], first["profile"], "Hermes's reviewed text.\n")
        err = self.api_error(installer.confirm_crew, plan["planId"], [first["profile"]])
        self.assertEqual(err.status_code, 409)
        self.assertEqual(self.soul_of(first["profile"]), "Hermes's reviewed text.\n")
        self.assertEqual(installer.store().installs(), [])

    def test_the_api_is_strict_unless_the_page_says_its_create_succeeded(self):
        api = sub("api")
        plan = installer.plan_bot("scout")
        gateway_create(plan["create"]["params"], write_soul=False)
        self.assertEqual(self.api_error(api.install_confirm, {"planId": plan["planId"]}).status_code, 409)
        self.assertEqual(self.api_error(api.install_confirm, {"planId": plan["planId"], "created": "yes"}).status_code, 409)
        out = api.install_confirm({"planId": plan["planId"], "created": True})
        self.assertIn("Cabinet wrote them", out["note"])


class GroupChats(unittest.TestCase):
    def test_the_crew_account_points_to_group_chats_without_overclaiming(self):
        honest = installer.what_hermes_does()
        text = honest["groupChat"]
        self.assertIn("New group chat", text)
        self.assertIn("@mention", text)
        self.assertIn("no set order", text)
        self.assertIn("Kanban cards", text)
        whole = json.dumps(honest).lower()
        for claim in ("runs the crew", "automatically hand", "in order in a group chat", "follows the handoff"):
            self.assertNotIn(claim, whole)


if __name__ == "__main__":
    unittest.main()
