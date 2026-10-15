"""Stand-ins for Hermes, so the install flow can be tested without Hermes.

``gateway_create(params)`` does what Hermes's ``profiles.create`` does to the disk for the request Cabinet
prepares: makes the profile folder under the profiles root, with its standard sub-folders, an empty
credentials file, the instructions, a description and a settings file that already holds the model Hermes
inherited. ``fake_delete`` stands in for ``hermes_cli.profiles.delete_profile``.

tests/test_install_hermes.py runs the same flow against Hermes's own functions when they can be imported.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from _load import sub

hermes = sub("hermes_bridge")

DEFAULT_SOUL = "You are Hermes, a helpful assistant.\n"


def gateway_create(params, *, write_soul=True):
    root = hermes.profiles_root()
    root.mkdir(parents=True, exist_ok=True)
    target = root / params["name"]
    if target.exists():
        raise FileExistsError(f"Profile '{params['name']}' already exists")
    target.mkdir()
    for d in ("memories", "sessions", "skills", "skins", "logs", "plans", "workspace", "cron", "home"):
        (target / d).mkdir()
    (target / ".env").write_text("# credentials\n")
    (target / "SOUL.md").write_text(params.get("soul") if write_soul and params.get("soul") else DEFAULT_SOUL)
    (target / "profile.yaml").write_text(f"description: {params.get('description', '')}\n")
    (target / "config.yaml").write_text("model:\n  provider: example\n  default: example-model\n")
    return target


def fake_delete(name):
    target = hermes.profiles_root() / name
    if not target.is_dir():
        raise hermes.HermesError("Hermes couldn't remove it: no such profile")
    shutil.rmtree(target)


def make_foreign_profile(name, soul="You are my own bot. Do my own things.\n"):
    """A profile the owner already had (Cabinet didn't make it)."""
    root = hermes.profiles_root()
    target = root / name
    target.mkdir(parents=True)
    (target / "SOUL.md").write_text(soul)
    (target / "profile.yaml").write_text("description: mine\n")
    return target


def wipe_profiles():
    root = hermes.profiles_root()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)


def hermes_bots_install(catalog_name, profile=None, soul="You are a reviewed Hermes bot.\n", *, blueprint=True, provenance=True):
    """What Hermes's own Bots catalog (NousResearch/hermes-agent#119260, bot_profile_install.py) leaves on disk:
    the profile folder, its SOUL.md, ``bot-blueprint.yaml`` with the frozen catalog entry, and ``profile.yaml`` whose
    ``provenance`` says ``kind: bot-catalog``. Written the way Hermes's atomic_yaml_write lays them out."""
    root = hermes.profiles_root()
    target = root / (profile or catalog_name)
    target.mkdir(parents=True)
    (target / "SOUL.md").write_text(soul)
    (target / "config.yaml").write_text("platform_toolsets:\n  cli:\n  - hermes-cli\n")
    if blueprint:
        (target / "bot-blueprint.yaml").write_text(
            "schema_version: 1\ncatalog_entry:\n"
            f"  name: {catalog_name}\n  version: 1.0.0\n  maintainer: Nous Research\n  tier: official\n"
            "  profile:\n    suggested_name: something-else\n    description: A reviewed bot\n"
            f"  title: {catalog_name.replace('-', ' ').title()}\n")
    meta = "description: A reviewed bot\ndescription_auto: false\ndisplay_name: A Bot\n"
    if provenance:
        meta += (f"provenance:\n  kind: bot-catalog\n  catalog_name: {catalog_name}\n  catalog_version: 1.0.0\n"
                 "  installed_at: '2026-10-06T12:00:00+00:00'\n  source_profile: default\n")
    meta += "setup_state: needs_setup\n"
    (target / "profile.yaml").write_text(meta)
    return target
