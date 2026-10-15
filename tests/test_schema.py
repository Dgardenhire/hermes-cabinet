"""The strict catalog schema: the bundled snapshot passes, and every kind of bad file is refused."""

from __future__ import annotations

import copy
import json
import re
import unittest

from _load import ROOT, sub

schema = sub("catalog_schema")
catalog = sub("catalog")


def good():
    return copy.deepcopy(catalog.bundled())


class Bundled(unittest.TestCase):
    def test_the_snapshot_passes_its_own_schema(self):
        out = schema.validate_catalog(good())
        self.assertEqual(len(out["bots"]), 20)
        self.assertEqual(len(out["crews"]), 11)
        self.assertEqual(len(out["jobs"]), 18)

    def test_validation_is_idempotent(self):
        once = schema.validate_catalog(good())
        self.assertEqual(schema.validate_catalog(once), once)

    def test_every_bot_has_the_fields_an_install_is_built_from(self):
        for b in catalog.bots():
            self.assertTrue(b["role"], b["slug"])
            self.assertTrue(b["inputs"] and b["outputs"], b["slug"])
            self.assertTrue(b["href"].startswith("https://botcabinet.com/bots/"), b["slug"])

    def test_the_contract_example_passes(self):
        text = (ROOT / "docs" / "site-catalog-contract.md").read_text(encoding="utf-8")
        block = re.search(r"```json\n(.*?)\n```", text, re.S).group(1)
        schema.validate_catalog(json.loads(block))

    def test_the_contract_names_every_field(self):
        text = (ROOT / "docs" / "site-catalog-contract.md").read_text(encoding="utf-8")
        for key in (*schema.BOT_REQUIRED, *schema.BOT_OPTIONAL, "schemaVersion", "generatedAt", "siteCommit", "handoffOrder",
                    "approvalActions", "prohibitedActions", "testStatus", "botSlugs", "optionalBotSlugs", "firstTest"):
            self.assertIn(f"`{key}`", text, key)


class Refused(unittest.TestCase):
    def refused(self, mutate, fragment=""):
        doc = good()
        mutate(doc)
        with self.assertRaises(schema.SchemaError) as caught:
            schema.validate_catalog(doc)
        if fragment:
            self.assertIn(fragment, " ".join(caught.exception.problems))
        return caught.exception

    def test_not_an_object(self):
        for bad in ([], "x", None, 3):
            with self.assertRaises(schema.SchemaError):
                schema.validate_catalog(bad)

    def test_wrong_schema_version(self):
        self.refused(lambda d: d.update(schemaVersion=3), "this Cabinet reads version 2")
        self.refused(lambda d: d.update(schemaVersion="2"))
        self.refused(lambda d: d.update(schemaVersion=True))

    def test_unknown_top_level_key(self):
        self.refused(lambda d: d.update(extra=1), "unknown key 'extra'")

    def test_missing_top_level_key(self):
        self.refused(lambda d: d.pop("bots"), "missing 'bots'")

    def test_wrong_origin(self):
        self.refused(lambda d: d.update(origin="https://evil.example"), "origin")

    def test_bad_dates(self):
        for bad in ("2026-13-01", "Oct 4", "2026-10-4", 20261004, ""):
            self.refused(lambda d, b=bad: d.update(generatedAt=b))

    def test_unknown_bot_key(self):
        self.refused(lambda d: d["bots"][0].update(model="expensive"), "unknown key 'model'")

    def test_bot_missing_required(self):
        self.refused(lambda d: d["bots"][0].pop("outcome"), "missing 'outcome'")

    def test_bad_slugs(self):
        for bad in ("Has Space", "../x", "UPPER", "", "a" * 65, "-lead", "x/y", "x\x00"):
            self.refused(lambda d, b=bad: d["bots"][0].update(slug=b))

    def test_duplicate_slug(self):
        self.refused(lambda d: d["bots"][1].update(slug=d["bots"][0]["slug"]), "appears twice")

    def test_too_long_text(self):
        self.refused(lambda d: d["bots"][0].update(name="x" * 61), "limit is 60")
        self.refused(lambda d: d["bots"][0].update(role="x" * 4001), "limit is 4000")

    def test_too_many_items(self):
        self.refused(lambda d: d["bots"][0].update(inputs=["a"] * 13), "limit is 12")

    def test_wrong_types(self):
        self.refused(lambda d: d["bots"][0].update(inputs="not a list"))
        self.refused(lambda d: d["bots"][0].update(name=5))
        self.refused(lambda d: d["bots"][0].update(skills={"a": 1}))
        self.refused(lambda d: d["bots"][0].update(routine="daily"))

    def test_control_and_hidden_characters(self):
        for bad in ("a\x00b", "a\x07b", "evil‮text", "zero​width", "line sep", "bom﻿"):
            self.refused(lambda d, b=bad: d["bots"][0].update(summary=b), "control or hidden")

    def test_newline_only_allowed_in_role(self):
        doc = good()
        doc["bots"][0]["role"] = "Line one.\nLine two."
        schema.validate_catalog(doc)
        self.refused(lambda d: d["bots"][0].update(summary="two\nlines"), "one line")

    def test_carriage_return_is_refused_even_in_role(self):
        self.refused(lambda d: d["bots"][0].update(role="a\r\nb"), "control or hidden")

    def test_bot_href_must_be_on_the_site(self):
        for bad in ("http://botcabinet.com/bots/x/", "https://evil.example/bots/x/", "https://botcabinet.com.evil.example/",
                    "https://user:pw@botcabinet.com/", "javascript:alert(1)", "https://botcabinet.com/a b"):
            self.refused(lambda d, b=bad: d["bots"][0].update(href=b))

    def test_credit_href_must_be_https_without_a_login(self):
        def credit(href):
            return lambda d: d["bots"][0].update(credits=[{"line": "Inspired by someone", "href": href}])
        self.refused(credit("http://example.com/"))
        self.refused(credit("https://user:pw@example.com/"))
        doc = good()
        doc["bots"][0]["credits"] = [{"line": "Inspired by someone", "href": "https://x.ai/page"}]
        schema.validate_catalog(doc)

    def test_crew_member_must_be_a_bot_in_the_file(self):
        self.refused(lambda d: d["crews"][0]["members"][0].update(slug="ghost"), "not a Bot in this catalog")

    def test_job_bots_must_exist(self):
        self.refused(lambda d: d["jobs"][0].update(botSlugs=["ghost"]), "not a Bot in this catalog")

    def test_crew_limits(self):
        self.refused(lambda d: d["crews"][0].update(members=[]), "1 to 12")
        self.refused(lambda d: d["crews"][0].update(handoffOrder=[]), "1 to 20")

    def test_empty_bot_list(self):
        self.refused(lambda d: d.update(bots=[], crews=[], jobs=[]))

    def test_too_many_bots(self):
        def many(d):
            d["bots"] = [dict(d["bots"][0], slug=f"b{i}") for i in range(301)]
            d["crews"], d["jobs"] = [], []
        self.refused(many, "1 to 300")

    def test_every_problem_is_listed_not_just_the_first(self):
        err = self.refused(lambda d: (d["bots"][0].update(name="x" * 99), d["bots"][1].update(slug="BAD")))
        self.assertGreaterEqual(len(err.problems), 2)

    def test_status_words(self):
        self.refused(lambda d: d["bots"][0].update(test={"status": "Not Tested!", "line": ""}))
        self.refused(lambda d: d["crews"][0].update(testStatus="x y"))


class Normalised(unittest.TestCase):
    def test_optional_fields_are_filled_in(self):
        doc = good()
        for k in ("audience", "inputs", "outputs", "examples", "firstMission", "approvals", "offLimits", "worksWith", "role",
                  "skills", "routine", "riskLevel", "credits", "href", "test"):
            doc["bots"][0].pop(k)
        out = schema.validate_catalog(doc)["bots"][0]
        self.assertEqual(out["inputs"], [])
        self.assertIsNone(out["routine"])
        self.assertEqual(out["href"], f"https://botcabinet.com/bots/{out['slug']}/")

    def test_surrounding_space_is_trimmed(self):
        doc = good()
        doc["bots"][0]["name"] = "  Scout  "
        self.assertEqual(schema.validate_catalog(doc)["bots"][0]["name"], "Scout")


if __name__ == "__main__":
    unittest.main()
