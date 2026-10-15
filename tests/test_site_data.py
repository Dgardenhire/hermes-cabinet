"""The botcabinet.com picture inside the desktop plugin: desktop/src/site-data.js and data/site.json.

Built by tools/make_site_data.py from the site repository at one commit. These tests check that every
Bot carries a portrait, a test status and credit fields; that the status is exactly what the site's own
evidence says (no invented test claims); and, when the site checkout is on this machine, that the
snapshot still matches the site at the commit it names (read with `git show`, never written).
"""

import json
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE_JSON = json.loads((ROOT / "data" / "site.json").read_text(encoding="utf-8"))
MODULE = (ROOT / "desktop" / "src" / "site-data.js").read_text(encoding="utf-8")
BUNDLE = ROOT / "desktop" / "plugin.js"
# A local checkout of the botcabinet.com site, if you have one (the comparison is skipped otherwise).
SITE_REPO = Path(os.environ.get("CABINET_SITE_REPO") or "/nonexistent-bot-cabinet-site")


def module_site():
    line = next(l for l in MODULE.splitlines() if l.startswith("export const SITE = "))
    return json.loads(line[len("export const SITE = "):])


def module_images():
    return dict(re.findall(r'^  "([^"]+)": "(data:image/(?:jpeg|png);base64,[A-Za-z0-9+/=]+)",$', MODULE, flags=re.M))


class ModuleMatchesSnapshot(unittest.TestCase):
    def test_module_equals_site_json(self):
        self.assertEqual(module_site(), SITE_JSON)

    def test_twenty_bots_and_eleven_crews(self):
        self.assertEqual(len(SITE_JSON["bots"]), 20)
        self.assertEqual(len(SITE_JSON["crews"]), 11)


class EveryBot(unittest.TestCase):
    def test_portrait_status_and_credit_fields(self):
        images = module_images()
        for b in SITE_JSON["bots"]:
            with self.subTest(b["slug"]):
                self.assertIn(b["portrait"], images, "portrait not embedded")
                self.assertIn(b["test"]["status"], {"tested", "not-tested"})
                self.assertTrue(b["test"]["line"])
                self.assertIsInstance(b["credits"], list)
                self.assertIsInstance(b["learnMore"], list)
                for c in b["credits"]:
                    self.assertTrue(c["creator"] and c["line"] and c["href"].startswith("https://"))
                self.assertTrue(b["href"].startswith("https://botcabinet.com/bots/"))
                self.assertTrue(b["summary"] and b["outcome"] and b["inputs"] and b["outputs"])

    def test_images_are_small_and_embedded(self):
        images = module_images()
        for key in ("doors", "drafting", "wordmark"):
            self.assertIn(key, images)
        for key, uri in images.items():
            self.assertLess(len(uri), 260_000, key)

    def test_known_credits(self):
        bots = {b["slug"]: b for b in SITE_JSON["bots"]}
        self.assertEqual(bots["daily-newspaper"]["credits"][0]["creator"], "Karen X. Cheng")
        self.assertEqual(bots["ops"]["credits"][0]["creator"], "Muse at Work")
        for l in bots["planner"]["learnMore"]:
            self.assertTrue(l["href"].startswith("https://www.hermesbible.com/flows/") and l["by"])


class NoInventedTestClaims(unittest.TestCase):
    """Tested means a recorded task run on the site, and nothing else."""

    def test_tested_only_with_a_recorded_run(self):
        for b in SITE_JSON["bots"]:
            with self.subTest(b["slug"]):
                runs = [t for t in b["test"]["tested"] if t["what"] != "Hermes import check"]
                if b["test"]["status"] == "tested":
                    self.assertTrue(runs, "tested without a recorded task run")
                    self.assertTrue(all(t.get("href", "").startswith("https://botcabinet.com/") for t in runs))
                else:
                    self.assertEqual(runs, [], "a task run listed on a not-tested Bot")
                self.assertTrue(b["test"]["notTested"], "every Bot shows what is still untested")

    def test_crew_status_labels_follow_the_site(self):
        labels = {"not-tested": "Not tested", "failed-acceptance": "Failed acceptance", "import-test-passed": "Passed"}
        for c in SITE_JSON["crews"]:
            self.assertEqual(c["testLabel"], labels.get(c["testStatus"], c["testLabel"]))
        self.assertFalse([c for c in SITE_JSON["crews"] if c["testStatus"] == "import-test-passed"])


@unittest.skipUnless((SITE_REPO / ".git").exists(), "the botcabinet.com site checkout is not on this machine")
class SameAsTheSite(unittest.TestCase):
    """Re-derive every status straight from the site's files at the snapshot's commit."""

    @classmethod
    def setUpClass(cls):
        cls.ref = SITE_JSON["siteCommit"].split()[0]

    def show(self, path):
        return subprocess.run(["git", "-C", str(SITE_REPO), "show", f"{self.ref}:{path}"],
                              capture_output=True, text=True, check=True).stdout

    def test_bot_statuses_equal_the_sites_evidence(self):
        evidence = self.show("src/data/bot-release-evidence.ts")
        reproduced_src = evidence.split("REPRODUCED_BOT_EVIDENCE", 1)[1].split("};", 1)[0]
        runtime_src = evidence.split("BOT_RUNTIME_EVIDENCE", 1)[1].split("\n};", 1)[0]
        reproduced = set(re.findall(r'(?m)^\s*"?([a-z0-9-]+)"?:\s*\{\s*hermesVersion', reproduced_src))
        runtime = set(re.findall(r'(?m)^\s*"([a-z0-9-]+)":\s*\{', runtime_src))
        api = json.loads(self.show("public/api/v2/bots.json"))
        self.assertEqual([b["slug"] for b in api["bots"]], [b["slug"] for b in SITE_JSON["bots"]])
        for b in SITE_JSON["bots"]:
            with self.subTest(b["slug"]):
                expected = "tested" if b["slug"] in reproduced | runtime else "not-tested"
                self.assertEqual(b["test"]["status"], expected)
        portraits = {b["slug"]: b["portraitUrl"] for b in api["bots"]}
        for b in SITE_JSON["bots"]:
            self.assertEqual(b["portrait"], portraits[b["slug"]])

    def test_crew_statuses_equal_the_bundles(self):
        for c in SITE_JSON["crews"]:
            kit = json.loads(self.show(f"public/downloads/crew-kits/bundles/{c['slug']}.json"))
            self.assertEqual(c["testStatus"], kit.get("runtimeStatus", "not-tested"), c["slug"])
            self.assertEqual([s["owner"] for s in c["steps"]], [h["owner"] for h in kit["handoffOrder"]])

    def test_inspired_by_credits_equal_starter_bots(self):
        starters = self.show("src/data/starter-bots.ts")
        for creator in re.findall(r'creator"?:\s*"([^"]+)"', starters):
            self.assertTrue(any(c["creator"] == creator for b in SITE_JSON["bots"] for c in b["credits"]), creator)


class Bundle(unittest.TestCase):
    def test_bundle_under_one_and_a_half_megabytes(self):
        self.assertLess(BUNDLE.stat().st_size, 1_536_000)

    def test_no_network_images(self):
        js = BUNDLE.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"src:\s*[\"']https?://", js))
        self.assertIsNone(re.search(r"url\(\s*[\"']?https?://", js))


if __name__ == "__main__":
    unittest.main()
