"""paths.display: the page shows paths under your home folder as ~/..., everything else as it is."""
import unittest
from pathlib import Path

from _load import sub

paths = sub("paths")


class DisplayTest(unittest.TestCase):
    def test_under_home_reads_tilde(self):
        self.assertEqual(paths.display(Path.home() / ".hermes" / "profiles" / "scout"), "~/.hermes/profiles/scout")

    def test_home_itself(self):
        self.assertEqual(paths.display(Path.home()), "~")

    def test_outside_home_unchanged(self):
        self.assertEqual(paths.display("/opt/hermes/profiles/scout"), "/opt/hermes/profiles/scout")

    def test_prefix_lookalike_unchanged(self):
        self.assertEqual(paths.display(str(Path.home()) + "-other/x"), str(Path.home()) + "-other/x")


if __name__ == "__main__":
    unittest.main()
