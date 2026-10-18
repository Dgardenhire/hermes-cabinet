"""The only place Cabinet touches Hermes's own code.

Everything here is a plain Python call into Hermes (never the ``hermes`` command), and every call is
guarded: when Hermes isn't importable, which is the case in most of Cabinet's tests, the functions say so
or fall back to looking at the folder on disk. That keeps the rest of Cabinet testable without Hermes.

What Cabinet asks of Hermes:

- list the profiles that exist, and find the folder for one (read only);
- delete one profile Cabinet made, through Hermes's own ``delete_profile``, so Hermes cleans up its
  gateway, aliases and bookkeeping the way it does for any profile;
- set a profile's display name (it only ever does this for a profile it just created);
- make and archive Kanban cards, only after the owner confirms (``crews`` flow);
- notice whether this Hermes already ships its own Bots catalog (``detect_core_catalog``), and which profiles that
  catalog added (``hermes_catalog_name``), so Cabinet never records, rewrites or offers a second copy of one.

Creating a profile is NOT here. The desktop page asks Hermes's gateway to do that (``profiles.create``),
the same call New Agent and Bot Lab use, because that call also gives the new profile the owner's
sign-in and model so it can answer. Cabinet only prepares the exact request and then checks the result.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import re
from pathlib import Path
from typing import Any

from . import paths, safety


class HermesError(Exception):
    """Hermes refused or failed. The message is safe to show the owner."""


def _profiles_mod():
    try:
        return importlib.import_module("hermes_cli.profiles")
    except Exception:
        return None


def available() -> bool:
    return _profiles_mod() is not None


def profiles_root() -> Path:
    mod = _profiles_mod()
    if mod is not None:
        try:
            return Path(mod._get_profiles_root())
        except Exception:
            pass
    return paths.hermes_home() / "profiles"


def profile_dir(name: str) -> Path:
    """The folder a profile of this (already checked) name lives in. Refuses to leave the profiles folder."""
    canon = safety.profile_name(name)
    root = profiles_root()
    target = root / canon
    if not safety.inside(target, root) or target.resolve(strict=False).parent != root.resolve(strict=False):
        raise HermesError("That name would put the profile outside Hermes's profiles folder.")
    return target


def existing_profiles() -> list[str]:
    """Every profile name Hermes knows, including ``default``."""
    mod = _profiles_mod()
    names: set[str] = set()
    if mod is not None:
        try:
            names.update(str(n).lower() for n in mod.list_profile_names())
        except Exception:
            pass
    root = profiles_root()
    try:
        for entry in root.iterdir():
            if entry.is_dir() and not entry.name.startswith(".") and safety.PROFILE_ID.match(entry.name):
                names.add(entry.name)
    except OSError:
        pass
    names.add("default")
    return sorted(names)


def name_taken(name: str) -> bool:
    """True for a live profile and also for any leftover folder of that name: never build on top of either."""
    canon = name.lower()
    return canon in existing_profiles() or (profiles_root() / canon).exists()


def free_name(base: str) -> str:
    """``base``, or ``base-2``, ``base-3`` and so on: the first name nothing uses."""
    base = (base or "new-bot")[:60].strip("-_") or "new-bot"
    if not name_taken(base):
        return base
    for i in range(2, 100):
        candidate = f"{base}-{i}"
        if not name_taken(candidate):
            return candidate
    raise HermesError("Couldn't find a free profile name. Pick one yourself.")


def delete_profile(name: str) -> None:
    mod = _profiles_mod()
    if mod is None:
        raise HermesError("Removing a profile needs Hermes itself, and it isn't reachable from here.")
    canon = safety.profile_name(name)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            mod.delete_profile(canon, yes=True)
    except Exception as exc:  # Hermes's own message, trimmed
        raise HermesError(f"Hermes couldn't remove it: {str(exc).splitlines()[0][:200] if str(exc) else exc.__class__.__name__}") from exc


def set_display_name(directory: Path, display: str) -> bool:
    mod = _profiles_mod()
    if mod is None:
        return False
    try:
        mod.write_profile_meta(directory, display_name=display)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ Kanban --

def _kanban():
    try:
        return importlib.import_module("hermes_cli.kanban_db")
    except Exception:
        return None


def kanban_available() -> bool:
    return _kanban() is not None


def _connect(kb):
    """Kanban's connection, from where this Hermes keeps it (it moved; older Hermes still has it on kanban_db)."""
    try:
        mod = importlib.import_module("hermes_cli.kanban_db_connect")
    except ImportError:
        return kb.connect_closing()
    return mod.connect_closing()


def create_cards(cards: list[dict[str, Any]]) -> list[str]:
    """Create the cards in order, each blocked, each after the one before. Returns their ids."""
    kb = _kanban()
    if kb is None:
        raise HermesError("This Hermes has no Kanban board Cabinet can reach.")
    ids: list[str] = []
    try:
        kb.init_db()
        with _connect(kb) as conn:
            previous: str | None = None
            for card in cards:
                task = kb.create_task(conn, title=card["title"], body=card["body"], assignee=card.get("assignee"),
                                      created_by="bot-cabinet-v2", parents=[previous] if previous else (),
                                      initial_status="blocked", idempotency_key=card["key"])
                ids.append(task)
                previous = task
    except Exception as exc:
        raise HermesError(f"Kanban refused a card: {str(exc).splitlines()[0][:200] if str(exc) else exc.__class__.__name__}") from exc
    return ids


def archive_cards(card_ids: list[str]) -> int:
    kb = _kanban()
    if kb is None or not card_ids:
        return 0
    done = 0
    try:
        with _connect(kb) as conn:
            for cid in card_ids:
                try:
                    done += 1 if kb.archive_task(conn, cid) else 0
                except Exception:
                    continue
    except Exception:
        return done
    return done


# ------------------------------------------------------------------ hand-over --
#
# Hermes's own Bots catalog (NousResearch/hermes-agent#119260) installs a bot as a profile with a
# ``bot-blueprint.yaml`` beside its instructions and ``provenance: {kind: bot-catalog, catalog_name: ...}`` in its
# ``profile.yaml``. The desktop page's real signal that the catalog exists is the gateway answering ``bots.catalog``;
# ``detect_core_catalog`` is the Python side's view of the same thing, for callers that have no gateway.

BLUEPRINT_FILE = "bot-blueprint.yaml"
_CATALOG_KIND = re.compile(r"""\bkind\s*:\s*['"]?bot-catalog\b""")


def detect_core_catalog() -> dict[str, Any]:
    """Does this Hermes ship its own Bots catalog? Asks Hermes's ``hermes_cli.bot_catalog`` for its bundled entries.

    Offline only (``include_live=False``): Cabinet never makes Hermes fetch its feed. Any failure means no catalog.
    """
    none = {"available": False, "via": None, "detail": "This Hermes has no Bots catalog of its own yet.", "names": []}
    try:
        mod = importlib.import_module("hermes_cli.bot_catalog")
    except Exception:
        return none
    loader = getattr(mod, "load_bot_catalog", None)
    if not callable(loader):
        return none
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            entries = loader(include_live=False)
        names = sorted({str(getattr(e, "name", "") or "") for e in entries} - {""})
    except Exception:
        return none
    return {"available": True, "via": "hermes_cli.bot_catalog", "detail": "This Hermes ships its own Bots catalog.", "names": names}


def _scalar(text: str) -> str:
    text = text.split(" #", 1)[0].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return text.strip()


def _flow_map(text: str) -> dict[str, str] | None:
    """A one-line flow mapping, ``{kind: bot-catalog, catalog_name: scout}``, as plain key/value text. None if it isn't one."""
    text = text.split(" #", 1)[0].strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    out: dict[str, str] = {}
    for part in text[1:-1].split(","):
        key, sep, value = part.partition(":")
        if sep and key.strip():
            out[_scalar(key)] = _scalar(value)
    return out


def _shallow_yaml(path: Path) -> dict[str, Any]:
    """The top-level keys of a small YAML file, and the plain values directly under each. Raises OSError if unreadable.

    Enough to read the two markers Hermes's catalog writes, block style or one-line flow style, without depending
    on a YAML library being importable.
    """
    return _shallow_text(path.read_text(encoding="utf-8", errors="replace")[:200_000])


def _shallow_text(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    parent: str | None = None
    child_indent: int | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("- "):
            continue
        match = re.match(r"^( *)([A-Za-z0-9_.-]+):(?:\s+(.*))?$", raw.rstrip())
        if not match:
            continue
        indent, key, value = len(match.group(1)), match.group(2), _scalar(match.group(3) or "")
        if indent == 0:
            parent, child_indent = key, None
            flow = _flow_map(match.group(3) or "")
            out[key] = flow if flow is not None else (value if value else {})
        elif parent is not None and isinstance(out.get(parent), dict):
            if child_indent is None:
                child_indent = indent
            if indent == child_indent and key not in out[parent]:
                out[parent][key] = value
    return out


def hermes_catalog_name(directory: Path) -> str | None:
    """The Hermes Bots catalog name this profile was added from, or None when Hermes's catalog didn't add it.

    Any ``bot-blueprint.yaml`` marks a catalog profile; so does ``profile.yaml`` saying ``kind: bot-catalog`` (under
    ``provenance``, where Hermes writes it, or at the top, block or flow style). Returns "" for a catalog profile whose
    name can't be read. Fails closed: a ``profile.yaml`` Cabinet can't read, a ``kind: bot-catalog`` it can't place, or
    any error while looking all count as Hermes Bots, so Cabinet leaves the profile alone.
    """
    try:
        blueprint = directory / BLUEPRINT_FILE
        if blueprint.exists() or blueprint.is_symlink():
            try:
                entry = _shallow_yaml(blueprint).get("catalog_entry")
            except OSError:
                return ""
            name = entry.get("name") if isinstance(entry, dict) else None
            return str(name or "")
        meta_file = directory / "profile.yaml"
        if not meta_file.exists() and not meta_file.is_symlink():
            return None
        text = meta_file.read_text(encoding="utf-8", errors="replace")[:200_000]
        meta = _shallow_text(text)
        provenance = meta.get("provenance")
        if isinstance(provenance, dict) and provenance.get("kind") == "bot-catalog":
            return str(provenance.get("catalog_name") or "")
        if meta.get("kind") == "bot-catalog":
            return str(meta.get("catalog_name") or meta.get("name") or "")
        if _CATALOG_KIND.search(text):
            return ""  # says it came from the catalog in a shape Cabinet doesn't read
        return None
    except Exception:
        return ""


def hermes_catalog_installs() -> dict[str, str]:
    """Catalog name to profile, for every profile Hermes's own Bots catalog added. Read only.

    Every profile is looked at, however many there are: a cap would quietly miss the ones past it.
    """
    found: dict[str, str] = {}
    root = profiles_root()
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return found
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir() or entry.name.startswith(".") or not safety.PROFILE_ID.match(entry.name):
            continue
        name = hermes_catalog_name(entry)
        if name and name not in found:
            found[name] = entry.name
    return found
