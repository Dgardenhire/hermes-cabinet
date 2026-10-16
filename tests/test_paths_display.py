"""paths.display: the page shows paths under your home folder as ~/..., everything else as it is."""
import os
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


@unittest.skipUnless(os.name == "nt", "Windows paths")
class WindowsDisplayTest(unittest.TestCase):
    def test_under_home_reads_tilde_with_forward_slashes(self):
        self.assertEqual(paths.display(str(Path.home() / "AppData" / "Local" / "hermes" / "profiles" / "scout")),
                         "~/AppData/Local/hermes/profiles/scout")

    def test_case_and_slash_direction_do_not_matter(self):
        self.assertEqual(paths.display(str(Path.home()).upper().replace("\\", "/") + "/x/y"), "~/x/y")

    def test_another_drive_is_unchanged(self):
        other = "Q:\\hermes\\profiles\\scout"
        self.assertEqual(paths.display(other), other)


if __name__ == "__main__":
    unittest.main()
