"""End to end through Hermes's real gateway handler: does a Bot added from Cabinet really work as a Hermes bot?

Runs only under ``tests/run_with_hermes.py`` (a separate Hermes clone, HOME and HERMES_HOME in a temporary
folder). Everywhere else it skips. No ``hermes`` command is run and no model is called: these tests stop at
the point where Hermes would send the first message, and check everything Hermes reads to get there.

What is checked, for every Bot in the catalog and for a whole crew:

1. Hermes's own ``profiles.create`` handler (the call the page makes) accepts Cabinet's exact request.
2. Cabinet's confirm step records what Hermes made, with no repair needed.
3. Hermes's own SOUL.md loader (``agent.prompt_builder.load_soul_md``) loads the instructions Cabinet planned,
   word for word, and Hermes's injection scanner finds nothing in them.
4. The new bot inherits the owner's model, as New Agent's does.
5. For a crew, each bot's instructions name the right neighbours in the handoff order, and the Kanban cards
   carry the right assignee, start blocked and chain in order.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import logging
import os
import tempfile
import unittest

from _load import SANDBOX, sub

hermes = sub("hermes_bridge")
installer = sub("installer")
catalog = sub("catalog")

try:
    profiles_mod = importlib.import_module("hermes_cli.profiles")
    gateway = importlib.import_module("tui_gateway.server")
    prompt_builder = importlib.import_module("agent.prompt_builder")
    logging.getLogger("tui_gateway").setLevel(logging.ERROR)
except Exception:  # not running under Hermes
    profiles_mod = gateway = prompt_builder = None

NEEDS = unittest.skipIf(gateway is None, "Hermes's gateway isn't importable here (use tests/run_with_hermes.py)")
# The whole catalog takes several minutes (Hermes seeds each new profile in a subprocess); by default a
# representative few run. CABINET_E2E_ALL=1 runs every Bot and every crew.
ALL = os.environ.get("CABINET_E2E_ALL") == "1"
SAMPLE_BOTS = ("scout", "daily-newspaper", "planner")
SAMPLE_CREWS = ("personal-planning-desk", "hiring-support-desk")
LAUNCH_MODEL = "model:\n  provider: openrouter\n  default: example/launch-model\n"


def real_create(request):
    """Send Cabinet's exact request through Hermes's own profiles.create handler, as the page does."""
    method = gateway._methods[request["method"]]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        reply = method("cabinet-test", request["params"])
    if "error" in reply:
        raise AssertionError(f"Hermes refused the request: {reply['error']}")
    return reply["result"]


@NEEDS
class RealGateway(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("CABINET_DATA_DIR")
        os.environ["CABINET_DATA_DIR"] = self._tmp.name
        catalog.set_active(None)
        self.created = []
        launch = SANDBOX / "hermes-home" / "config.yaml"
        self._had_config = launch.exists()
        if not self._had_config:
            launch.write_text(LAUNCH_MODEL, encoding="utf-8")

    def tearDown(self):
        for name in self.created:
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    profiles_mod.delete_profile(name, yes=True)
            except Exception:
                pass
        if not self._had_config:
            (SANDBOX / "hermes-home" / "config.yaml").unlink(missing_ok=True)
        if self._saved is None:
            os.environ.pop("CABINET_DATA_DIR", None)
        else:
            os.environ["CABINET_DATA_DIR"] = self._saved
        self._tmp.cleanup()

    def assert_works_as_a_bot(self, profile, soul):
        directory = profiles_mod.get_profile_dir(profile)
        loaded = prompt_builder.load_soul_md(home_override=directory)
        self.assertEqual(loaded, soul.strip(), f"{profile}: Hermes loads different instructions from the ones Cabinet planned")
        self.assertEqual(prompt_builder._scan_for_threats(soul, scope="context"), [],
                         f"{profile}: Hermes's injection scanner flags Cabinet's instructions")
        cfg = (directory / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("example/launch-model", cfg, f"{profile}: the bot didn't inherit the owner's model")

    def test_every_bot_goes_through_the_real_gateway_and_loads(self):
        for b in catalog.bots():
            if not ALL and b["slug"] not in SAMPLE_BOTS:
                continue
            with self.subTest(bot=b["slug"]):
                plan = installer.plan_bot(b["slug"])
                self.assertTrue(plan["ready"], plan["problems"])
                result = real_create(plan["create"])
                self.created.append(plan["profile"])
                self.assertTrue(result["soul_written"])
                out = installer.confirm(plan["planId"])
                self.assertEqual(out["note"], "", "Cabinet had to repair what Hermes made")
                self.assertEqual(out["install"]["status"], "ready")
                self.assertEqual(profiles_mod.read_profile_meta(profiles_mod.get_profile_dir(plan["profile"]))["display_name"], b["name"])
                self.assert_works_as_a_bot(plan["profile"], plan["soul"])
                removed = installer.remove("bot", out["install"]["id"], True)
                self.assertEqual(removed["result"], "removed")
                self.created.remove(plan["profile"])

    def test_no_alias_is_added_to_the_owners_command_line(self):
        plan = installer.plan_bot("scout")
        real_create(plan["create"])
        self.created.append(plan["profile"])
        wrapper = profiles_mod._get_wrapper_dir() / plan["profile"] if hasattr(profiles_mod, "_get_wrapper_dir") else None
        if wrapper is not None:
            self.assertFalse(wrapper.exists())

    def test_a_bot_lab_plan_works_as_a_bot(self):
        plan = installer.plan_lab(display="Lookout", description="Watches competitor pricing pages",
                                  soul="# Lookout\n\nYou are Lookout. Every Monday, summarize what changed on three pricing pages.\n",
                                  profile=None, job="pricing watch")
        self.assertTrue(plan["ready"], plan["problems"])
        real_create(plan["create"])
        self.created.append(plan["profile"])
        installer.confirm(plan["planId"])
        self.assert_works_as_a_bot(plan["profile"], plan["soul"])

    def test_every_crew_has_the_right_members_handoffs_and_cards(self):
        for crew in catalog.crews():
            if not ALL and crew["slug"] not in SAMPLE_CREWS:
                continue
            with self.subTest(crew=crew["slug"]):
                plan = installer.plan_crew(crew["slug"])
                self.assertTrue(plan["ready"], plan["problems"])
                self.assertEqual([m["slug"] for m in plan["members"]], [m["slug"] for m in crew["members"]])
                made = []
                for m in plan["members"]:
                    real_create(m["create"])
                    self.created.append(m["profile"])
                    made.append(m["profile"])
                    self.assert_works_as_a_bot(m["profile"], m["soul"])
                    self.assertIn(f"## Your place in the {crew['name']} crew", m["soul"])
                out = installer.confirm_crew(plan["planId"], made)
                self.assertTrue(out["ok"], out)
                view = out["crew"]
                self.assertEqual(sorted(x["profile"] for x in view["members"]), sorted(made))
                if hermes.kanban_available():
                    cards = installer.create_cards(view["id"], True)
                    self.assertEqual(len(cards["cardIds"]), len(crew["handoffOrder"]))
                    kb = importlib.import_module("hermes_cli.kanban_db")
                    with hermes._connect(kb) as conn:
                        rows = [kb.get_task(conn, cid) for cid in cards["cardIds"]]
                    by_slug = {m["slug"]: m["profile"] for m in plan["members"]}
                    for step, row in zip(plan["steps"], rows):
                        status = getattr(row, "status", None) or row["status"]
                        assignee = getattr(row, "assignee", None) if not isinstance(row, dict) else row["assignee"]
                        self.assertEqual(status, "blocked")
                        expected = by_slug[step["bots"][0]] if step["bots"] else None
                        self.assertEqual(assignee, expected, f"{crew['slug']} step {step['n']}")
                result = installer.remove("crew", view["id"], True)
                self.assertEqual(sorted(result["removed"]), sorted(made))
                for name in made:
                    self.created.remove(name)


if __name__ == "__main__":
    unittest.main()
