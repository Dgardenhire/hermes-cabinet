"""The checks on every name and every instruction that can become part of a Hermes profile."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _load import sub

safety = sub("safety")
catalog = sub("catalog")
installer = sub("installer")


class ProfileNames(unittest.TestCase):
    def test_good_names(self):
        for name in ("scout", "daily-newspaper", "a", "x_y-z", "bot2", "0lead", "a" * 64):
            self.assertEqual(safety.profile_name(name), name)

    def test_upper_case_is_lowered_not_refused(self):
        self.assertEqual(safety.profile_name("  Scout  "), "scout")

    def test_path_tricks_are_refused_not_tidied(self):
        for bad in ("../x", "..", "a/b", "a\\b", "/etc/passwd", "~root", "~/x", "x/../y", "a..b", "x\x00y", ".hidden/..", "..\\..\\x"):
            with self.assertRaises(safety.Unsafe, msg=repr(bad)):
                safety.profile_name(bad)

    def test_other_shapes_are_refused(self):
        for bad in ("", "   ", "has space", "-lead", "_lead", ".dot", "a" * 65, "emoji☃", "semi;colon", "$(x)", "a,b", "tab\tname",
                    "new\nline", "café"):
            with self.assertRaises(safety.Unsafe, msg=repr(bad)):
                safety.profile_name(bad)

    def test_reserved_names(self):
        for bad in ("default", "Default", "hermes", "root", "sudo", "test", "tmp", "cabinet", "con", "nul"):
            with self.assertRaises(safety.Unsafe, msg=bad):
                safety.profile_name(bad)

    def test_not_text(self):
        for bad in (None, 5, ["a"], {"a": 1}, b"scout"):
            with self.assertRaises(safety.Unsafe):
                safety.profile_name(bad)

    def test_the_error_suggests_a_safe_name(self):
        with self.assertRaises(safety.Unsafe) as caught:
            safety.profile_name("Client Intake")
        self.assertIn("client-intake", caught.exception.message)

    def test_suggestions_are_always_valid(self):
        for display in ("Client Intake", "Café Manager", "../../etc", "  ", "###", "Zähler 3", "中文", "A" * 200, "x/y\\z",
                        "Default", "Scout!"):
            name = safety.suggest_profile_name(display)
            self.assertRegex(name, safety.PROFILE_ID.pattern, display)
            self.assertNotIn("..", name)

    def test_suggestion_of_nothing_usable(self):
        self.assertEqual(safety.suggest_profile_name("中文"), "new-bot")

    def test_every_catalog_bot_gets_a_valid_default_name(self):
        for b in catalog.bots():
            safety.profile_name(safety.suggest_profile_name(b["name"]))


class DisplayAndDescription(unittest.TestCase):
    def test_display_name_is_tidied(self):
        self.assertEqual(safety.display_name("  Daily   Newspaper "), "Daily Newspaper")

    def test_display_name_limits(self):
        with self.assertRaises(safety.Unsafe):
            safety.display_name("x" * 61)
        with self.assertRaises(safety.Unsafe):
            safety.display_name("!!!")
        with self.assertRaises(safety.Unsafe):
            safety.display_name("")
        with self.assertRaises(safety.Unsafe):
            safety.display_name(7)

    def test_hidden_characters_in_names(self):
        for bad in ("Scout‮", "Sc​out", "Sc\x00out", "Sc\x07out"):
            with self.assertRaises(safety.Unsafe, msg=repr(bad)):
                safety.display_name(bad)

    def test_description_drops_the_closing_period_and_caps(self):
        self.assertEqual(safety.description("Finds topics."), "Finds topics")
        with self.assertRaises(safety.Unsafe):
            safety.description("x" * 201)
        self.assertEqual(safety.description("  one   line  "), "one line")


class Instructions(unittest.TestCase):
    GOOD = "# Scout\n\nYou are Scout. Find timely topics and return a short list.\n"

    def test_good_text_is_kept_and_ends_with_one_newline(self):
        self.assertEqual(safety.instructions(self.GOOD), self.GOOD)
        self.assertEqual(safety.instructions("You are Scout. Find topics for me.\n\n\n"), "You are Scout. Find topics for me.\n")

    def test_windows_newlines_are_normalised(self):
        self.assertEqual(safety.instructions("# A\r\n\r\nYou are Scout. Find topics.\r\n"), "# A\n\nYou are Scout. Find topics.\n")

    def test_too_short_or_empty(self):
        for bad in ("", "   ", "hi", "\n\n"):
            with self.assertRaises(safety.Unsafe):
                safety.instructions(bad)

    def test_not_text(self):
        for bad in (None, 5, ["x"], b"bytes"):
            with self.assertRaises(safety.Unsafe):
                safety.instructions(bad)

    def test_size_cap_is_in_bytes(self):
        safety.instructions("x" * (safety.MAX_SOUL_BYTES - 10))
        with self.assertRaises(safety.Unsafe):
            safety.instructions("x" * (safety.MAX_SOUL_BYTES + 1))
        with self.assertRaises(safety.Unsafe):  # multi-byte characters count by bytes, not characters
            safety.instructions("é" * (safety.MAX_SOUL_BYTES // 2 + 10))

    def test_control_and_hidden_characters(self):
        for bad in ("You are Scout.\x00 Find topics.", "You are Scout.\x1b[31m red", "You are Scout. ‮evil", "You are ​Scout. Find",
                    "You are Scout. ﻿Find topics", "You are Scout. Find topics here"):
            with self.assertRaises(safety.Unsafe, msg=repr(bad)):
                safety.instructions(bad)

    def test_tabs_and_newlines_are_fine(self):
        safety.instructions("You are Scout.\n\t- find topics\n\t- rank them\n")

    def test_secrets_are_refused(self):
        samples = {
            "an API key": "Use this key: sk-abcdefghijklmnopqrstuvwxyz123456 for calls.",
            "a GitHub token": "token ghp_abcdefghijklmnopqrstuvwxyz0123456789 ok",
            "an AWS key": "AWS id AKIAABCDEFGHIJKLMNOP is the one.",
            "a Slack token": "slack xoxb-1234567890-abcdefghij here",
            "a Google key": "google AIzaSyA-abcdefghijklmnopqrstuvwxyz01234 key",
            "a private key": "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
            "a bearer token": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789",
            "a login token": "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkw.abcdefghijklmnop",
            "a password or key": "password: hunter2hunter2hunter2",
        }
        for what, text in samples.items():
            with self.assertRaises(safety.Unsafe, msg=what) as caught:
                safety.instructions("You are Scout. Do the job.\n" + text)
            self.assertIn(what, caught.exception.message)

    def test_the_refusal_never_repeats_the_secret(self):
        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        with self.assertRaises(safety.Unsafe) as caught:
            safety.instructions(f"You are Scout. Key {secret}")
        self.assertNotIn(secret, caught.exception.message)
        self.assertNotIn("abcdefghijklmnop", str(caught.exception))

    def test_ordinary_talk_about_passwords_is_fine(self):
        safety.instructions("You are Scout. Never ask for a password. Never store a token or a secret key. Ask before using an API key.")
        safety.instructions("You are Scout. If the token: is missing, ask the owner.")

    def test_every_catalog_bot_builds_safe_instructions(self):
        for b in catalog.bots():
            text = installer.bot_soul(b)
            self.assertEqual(safety.instructions(text), text, b["slug"])
            self.assertIsNone(safety.contains_secret(text), b["slug"])
            self.assertLess(len(text.encode("utf-8")), safety.MAX_SOUL_BYTES)

    def test_every_crew_member_builds_safe_instructions(self):
        for c in catalog.crews():
            for m in c["members"]:
                text = installer.bot_soul(catalog.bot(m["slug"]), installer.crew_context(c, m["slug"]))
                self.assertEqual(safety.instructions(text), text, f"{c['slug']}/{m['slug']}")


class Paths(unittest.TestCase):
    def test_inside(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "profiles"
            root.mkdir()
            (root / "ok").mkdir()
            self.assertTrue(safety.inside(root / "ok", root))
            self.assertFalse(safety.inside(root / ".." / "x", root))
            self.assertFalse(safety.inside(Path(t) / "other", root))

    def test_a_symlink_out_of_the_root_is_not_inside(self):
        with tempfile.TemporaryDirectory() as t, tempfile.TemporaryDirectory() as elsewhere:
            root = Path(t) / "profiles"
            root.mkdir()
            (root / "sneaky").symlink_to(elsewhere)
            self.assertFalse(safety.inside(root / "sneaky", root))

    def test_body_size(self):
        self.assertEqual(safety.body_size({"a": "b"}), len('{"a": "b"}'))
        self.assertGreater(safety.body_size(object()), safety.MAX_BODY_BYTES)  # unserialisable counts as too big


if __name__ == "__main__":
    unittest.main()
