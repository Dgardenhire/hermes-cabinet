"""Add a Bot, set up a crew, keep a Bot Lab kit, and undo any of it. The logic behind the review screens.

The shape of every install, and why it is split in two:

    1. PLAN    Cabinet builds the exact profile (name, description, instructions) from the catalog entry and
               whatever the owner edited, checks every part (``safety``), and returns it for review. Nothing
               is written. The plan is kept so it can be matched up later.
    2. CREATE  The desktop page sends the plan's request to Hermes's gateway (``profiles.create``), the same
               call New Agent and Bot Lab use. Hermes makes the profile and gives it the owner's sign-in.
               Cabinet never handles a credential.
    3. RECORD  The page calls back and Cabinet looks at what Hermes actually made. Only if the profile is
               there, was made after the plan, and carries the planned instructions does Cabinet record it
               as its own, with a fingerprint of how it looked at that moment.

Undo works from that fingerprint, a list of every file in the profile: Cabinet removes only a profile it
recorded and only while every file still matches. Anything the owner has since added, removed, edited,
chatted in, given memories or scheduled stays, and Cabinet says why.

Cabinet never writes into, renames or deletes a profile it didn't create. A name that's already taken is
refused with a free suggestion.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from . import catalog, hermes_bridge as hermes, paths, safety
from .errors import ApiError
from .store import Store

PLAN_SLACK_SECONDS = 5  # a profile folder counts as made after the plan if it is at most this much older


def store() -> Store:
    return Store(paths.data_dir() / "cabinet.db")


def _iso(ts: float | None) -> str | None:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _same_text(path: Path, text: str) -> bool:
    """Does the file hold exactly this text, whatever line endings the platform wrote?

    Hermes writes SOUL.md in text mode, so on Windows every newline lands on disk as CRLF. Comparing raw bytes
    with the planned text would call every profile Hermes made on Windows "not the planned one".
    """
    try:
        on_disk = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return on_disk.replace("\r\n", "\n") == text.replace("\r\n", "\n")


def _bad(status: int, message: str, **extra: Any) -> ApiError:
    return ApiError(status, {"message": message, **extra})


def _title(text: str) -> str:
    return re.sub(r"[.\s]+$", "", str(text or ""))


# ------------------------------------------------------------------ the instructions --

def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items)


def bot_soul(bot: dict[str, Any], crew: dict[str, Any] | None = None) -> str:
    """A Bot's SOUL.md, built only from its catalog entry. The published role text goes in word for word."""
    role = bot.get("role") or (f"You are {bot['name']}, {bot['title'].lower()}. Your one job: {bot['outcome']}.")
    parts = [f"# {bot['name']}", "", role.strip(), ""]
    sections = (("What you are given", bot.get("inputs")), ("What you hand back", bot.get("outputs")),
                ("Ask the owner before", bot.get("approvals")), ("Never", bot.get("offLimits")))
    for heading, items in sections:
        if items:
            parts += [f"## {heading}", "", _bullets(items), ""]
    skills = bot.get("skills") or []
    if skills and skills[0].get("steps"):
        parts += ["## How you work", ""] + [f"{i}. {s}" for i, s in enumerate(skills[0]["steps"], 1)] + [""]
    if bot.get("firstMission"):
        parts += ["## Your first test", "", bot["firstMission"], ""]
    routine = bot.get("routine")
    parts += ["## When you work", "",
              (f"{routine['trigger']} " if routine else "") + "You do not run on a schedule unless the owner sets one up.", ""]
    if crew:
        parts += crew_section(crew)
    credit = [c["line"] for c in bot.get("credits", [])]
    parts += ["## Source", "", f"Set up from the {bot['name']} Bot on Bot Cabinet ({bot['href']})."] + credit + [""]
    return "\n".join(parts)


# ------------------------------------------------------------------ plans --

def _commit() -> str:
    return catalog.load()["siteCommit"]


def _create_request(profile: str, description: str, soul: str, no_skills: bool) -> dict[str, Any]:
    """The exact gateway request. No model is passed, so Hermes copies the owner's current model and any change
    goes through Hermes's cost guard. No alias, so nothing is added to the owner's command line."""
    return {"method": "profiles.create",
            "params": {"name": profile, "description": description, "soul": soul, "no_alias": True, "no_skills": bool(no_skills)}}


def _checked(field: str, fn, problems: list[dict[str, str]], fallback: Any = "") -> Any:
    try:
        return fn()
    except safety.Unsafe as exc:
        problems.append({"field": exc.field or field, "message": exc.message})
        return fallback


def _name_problem(profile: str, problems: list[dict[str, str]], base: str) -> str | None:
    """A taken name is a problem with a free suggestion, never an overwrite."""
    if hermes.name_taken(profile):
        suggestion = hermes.free_name(base or profile)
        problems.append({"field": "profile", "message": f"You already have a profile called '{profile}'. Cabinet won't change it. "
                                                       f"Use another name, such as '{suggestion}'."})
        return suggestion
    return None


def _profile_plan(kind: str, *, source: dict[str, Any], profile_raw: str, display_raw: str, description_raw: str, soul_raw: str,
                  no_skills: bool, extra: dict[str, Any] | None = None, persist: bool = True) -> dict[str, Any]:
    problems: list[dict[str, str]] = []
    display = _checked("displayName", lambda: safety.display_name(display_raw), problems, "")
    profile = _checked("profile", lambda: safety.profile_name(profile_raw), problems, "")
    description = _checked("description", lambda: safety.description(description_raw), problems, "")
    soul = _checked("soul", lambda: safety.instructions(soul_raw), problems, "")
    suggestion = _name_problem(profile, problems, profile) if profile else None
    where = None
    if profile:
        try:
            where = paths.display(hermes.profile_dir(profile))
        except (hermes.HermesError, safety.Unsafe) as exc:
            problems.append({"field": "profile", "message": str(exc)})
    plan: dict[str, Any] = {
        "kind": kind, "source": source, "profile": profile or str(profile_raw)[:64], "displayName": display or str(display_raw)[:60],
        "description": description, "soul": soul or str(soul_raw)[:safety.MAX_SOUL_BYTES], "noSkills": bool(no_skills),
        "where": where, "ready": not problems, "problems": problems, "suggestion": suggestion,
        "model": None,
        "modelNote": "The model you use now. You can change it later in Hermes.",
        "toolsNote": "Tools stay as Hermes sets them for a new bot. Cabinet doesn't change them.",
        "signInNote": "The same sign-in as your main profile. Cabinet never sees it.",
        "planId": None, "create": None,
    }
    if extra:
        plan.update(extra)
    if plan["ready"]:
        plan["create"] = _create_request(profile, description, soul, no_skills)
        if persist:
            plan["planId"] = store().add_plan(kind, {k: v for k, v in plan.items() if k != "planId"})
    return plan


def hermes_lane() -> tuple[set[str], dict[str, str]]:
    """Hermes's own Bots catalog, as Cabinet sees it: (catalog names this Hermes carries, catalog name -> profile it added).

    Cabinet matches a catalog name to a Cabinet Bot by its slug. Read only. Either part is empty when Hermes has no catalog.
    """
    det = hermes.detect_core_catalog()
    return (set(det["names"]) if det.get("available") else set()), hermes.hermes_catalog_installs()


def _lane_message(name: str, profile: str | None) -> str:
    if profile:
        return f"{name} is already in your bots: Hermes Bots added it as '{profile}'. Open a chat with it from your Bots list."
    return (f"{name} comes from Hermes's own Bots catalog, so add it there: open Capabilities, then the Bots tab, and "
            f"pick {name}.")


def plan_bot(slug: str, edits: dict[str, Any] | None = None) -> dict[str, Any]:
    """The review screen's profile for one catalog Bot. ``edits`` may change name, profile, description, instructions and skills.

    A Bot Hermes's own Bots catalog carries, or already added, is never planned: Cabinet hands it to Hermes Bots.
    """
    bot = catalog.bot(slug)
    if not bot:
        raise _bad(404, "No Bot by that name in the catalog.")
    carried, added = hermes_lane()
    if bot["slug"] in carried or bot["slug"] in added:
        raise _bad(409, _lane_message(bot["name"], added.get(bot["slug"])), hermesBots=True, catalogName=bot["slug"],
                   profile=added.get(bot["slug"]))
    edits = edits if isinstance(edits, dict) else {}
    if safety.body_size(edits) > safety.MAX_BODY_BYTES:
        raise _bad(413, "That's too much text for one bot.")
    display = edits.get("displayName", bot["name"])
    base = safety.suggest_profile_name(display if isinstance(display, str) else bot["name"])
    profile = edits.get("profile") or hermes.free_name(base)
    name_note = (f"You already have a profile called '{base}', so Cabinet suggests '{profile}'. Change it if you like."
                 if not edits.get("profile") and profile != base else None)
    description = edits.get("description", _title(bot["summary"])[:safety.MAX_DESCRIPTION])
    soul = edits.get("soul", bot_soul(bot))
    skills = [s["name"] for s in bot.get("skills", [])]
    routine = bot.get("routine")
    source = {"slug": bot["slug"], "name": bot["name"], "title": bot["title"], "href": bot["href"], "commit": _commit(),
              "credits": bot.get("credits", []), "riskLevel": bot.get("riskLevel", "")}
    extra = {"skills": skills, "nameNote": name_note,
             "skillsNote": ("The Bot's published skill is written into its instructions under 'How you work'." if skills else
                            "The catalog lists no separate skill files for this Bot."),
             "schedule": {"suggested": routine["trigger"] if routine else None, "willSet": False,
                          "note": "Cabinet never turns on a schedule. Run the first test by hand, then set one up in Hermes if you want it."}}
    return _profile_plan("bot", source=source, profile_raw=profile, display_raw=display, description_raw=description, soul_raw=soul,
                         no_skills=bool(edits.get("noSkills", True)), extra=extra)


def plan_lab(*, display: str, description: str, soul: str, profile: str | None, job: str = "") -> dict[str, Any]:
    """A Bot Lab plan as a profile, through the same checks and the same record as a catalog Bot."""
    base = safety.suggest_profile_name(display)
    source = {"slug": None, "name": display, "title": "Bot Lab plan", "href": None, "commit": _commit(), "credits": [], "job": job[:300]}
    extra = {"skills": [], "skillsNote": "A Bot Lab plan has no skill files. Its steps are in its instructions.",
             "schedule": {"suggested": None, "willSet": False, "note": "Cabinet never turns on a schedule."}}
    return _profile_plan("lab", source=source, profile_raw=profile or hermes.free_name(base), display_raw=display,
                         description_raw=description, soul_raw=soul, no_skills=True, extra=extra)


# ------------------------------------------------------------------ recording what Hermes made --

# What Hermes itself writes into a profile as it runs: caches, locks, snapshots, runtime state, downloaded tools and
# models. None of it is the owner's work, so it may come and go without keeping a profile from being removed.
#
# Source: Hermes 0.22.2 (hermes-agent d923a5eb48). hermes_cli/profiles.py DEFAULT_EXPORT_EXCLUDE_ROOT ("infrastructure
# / runtime state that is not profile data") and _CLONE_ALL_STRIP, hermes_cli/home_data_layout.py PM_RUNTIME_ROOT_DIRS,
# hermes_constants.py LOCAL_RUNTIME_ROOT_DIRS, plus the files Hermes's own code writes on first use:
# agent/model_metadata.py (context_length_cache.yaml), agent/models_dev.py (models_dev_cache.*), hermes_cli/models.py
# (provider_models_cache.json), agent/prompt_builder.py (.skills_prompt_snapshot.json), hermes_cli/config.py (.env.lock),
# hermes_cli/backup.py (.backup.lock, backups/, state-snapshots/), hermes_cli/active_sessions.py (runtime/),
# tui_gateway (state/, desktop/), Electron (desktop-plugins/, copies of installed plugins), cron's .tick.lock and the
# memory tool's *.lock. Copied here, not imported: a plugin may not import Hermes's private modules.
#
# Kept short on purpose. Anything not named counts as the owner's: SOUL.md, config.yaml, .env and auth.json (by size
# and time), profile.yaml, memories, skills, sessions, .hermes_history, workspace, home, plans, cron jobs, hooks,
# plugins, mcp-tokens (services the owner connected), pairing and platforms (channels the owner paired), skins,
# response_store.db, and every unknown name. Two stores Hermes makes on first open are checked by what they hold:
# state.db (any message the owner sent) and projects.db (any project the owner set up).
_RUNTIME_FILES = frozenset({
    "state.db", "state.db-wal", "state.db-shm", "state.db-journal",
    "projects.db", "projects.db-wal", "projects.db-shm", "projects.db-journal",
    "gateway.pid", "gateway.lock", "gateway_state.json", "processes.json", "active_profile",
    "auth.lock", ".env.lock", ".backup.lock", ".update_check", "errors.log",
    "context_length_cache.yaml", "models_dev_cache.json", "models_dev_cache.etag", "provider_models_cache.json",
    ".skills_prompt_snapshot.json",
})
_RUNTIME_DIRS = frozenset({
    "logs", "cache", "image_cache", "audio_cache", "document_cache", "browser_screenshots", "checkpoints", "sandboxes",
    "source-checks", "state-snapshots", "backups", "runtime", "state", "telemetry", "desktop", "desktop-plugins",
    "bin", "installs", "tools", "models", "runtimes", "node", "assets",
})
_RUNTIME_PATHS = frozenset({
    "cron/.tick.lock",
    "bot-desktop/launcher.pid", "bot-desktop/env", "bot-desktop/rfb.sock", "bot-desktop/lease.json", "bot-desktop/lease.lock",
    "bot-desktop/browser-profile/DevToolsActivePort", "bot-desktop/browser-profile/SingletonLock",
    "bot-desktop/browser-profile/SingletonCookie", "bot-desktop/browser-profile/SingletonSocket",
})
MAX_MANIFEST_FILES = 5000  # more than this and Cabinet won't vouch for the folder; the profile stays
_HASH_LIMIT = 8 * 1024 * 1024  # files bigger than this are compared by size and time, not read
_NEVER_HASHED = frozenset({".env", "auth.json"})  # credentials: compared by size and time, so no hash of a key is kept

OLD_RECORD = "An older Cabinet made it and kept no list of its files, so Cabinet can't tell what you added"
UNREADABLE = "Cabinet couldn't read all of its files"
TOO_MANY = "It has more files than Cabinet can check"
NO_HISTORY = "Cabinet couldn't read its chat history"


class _TooMany(Exception):
    pass


def _runtime(rel: str) -> bool:
    top, _, rest = rel.partition("/")
    if top in _RUNTIME_DIRS or rel in _RUNTIME_PATHS or (not rest and top in _RUNTIME_FILES):
        return True
    return top == "memories" and "/" not in rest and rest.endswith(".lock")  # the memory tool's write lock


def _scan(directory: Path) -> dict[str, os.stat_result]:
    """Every file and link in the profile, by its path inside it, except Hermes's own runtime files.

    Links are listed, never followed. Empty folders are left out: they hold nothing to lose. Raises OSError when
    any part can't be read, and _TooMany past MAX_MANIFEST_FILES.
    """
    found: dict[str, os.stat_result] = {}

    def fail(exc: OSError) -> None:
        raise exc

    for top, dirs, files in os.walk(directory, onerror=fail, followlinks=False):
        here = Path(top)
        prefix = here.relative_to(directory).as_posix()
        prefix = "" if prefix == "." else prefix + "/"
        walk_into = []
        for name in dirs:
            sub_dir = here / name
            if sub_dir.is_symlink() or getattr(sub_dir, "is_junction", lambda: False)():
                files.append(name)  # a link to a folder is a file here: recorded, not walked into
            elif not _runtime(prefix + name):
                walk_into.append(name)
        dirs[:] = walk_into
        for name in files:
            rel = prefix + name
            if _runtime(rel):
                continue
            found[rel] = os.lstat(here / name)
            if len(found) > MAX_MANIFEST_FILES:
                raise _TooMany()
    return found


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _entry(path: Path, st: os.stat_result) -> list[Any]:
    """How one file looked: ["link", target], ["file", size, mtime_ns, sha256 or ""] or ["other"]. Never its contents."""
    if stat.S_ISLNK(st.st_mode) or getattr(path, "is_junction", lambda: False)():
        return ["link", os.readlink(path)]
    if not stat.S_ISREG(st.st_mode):
        return ["other"]
    hashed = st.st_size <= _HASH_LIMIT and path.name not in _NEVER_HASHED
    return ["file", st.st_size, st.st_mtime_ns, _digest(path) if hashed else ""]


def manifest(directory: Path) -> dict[str, list[Any]]:
    return {rel: _entry(directory / rel, st) for rel, st in sorted(_scan(directory).items())}


def fingerprint(directory: Path) -> dict[str, Any]:
    """How the profile looked the moment Cabinet recorded it: every file (``files``), plus the old summary fields.

    ``files`` is None when the folder couldn't be listed then; such a profile is never removed automatically.
    """
    skills: list[str] = []
    root = directory / "skills"
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file():
                skills.append(str(p.relative_to(root)))
            if len(skills) >= 2000:
                break
    try:
        files: dict[str, list[Any]] | None = manifest(directory)
    except (OSError, _TooMany):
        files = None
    return {"soul": _sha(directory / "SOUL.md"), "config": _sha(directory / "config.yaml"), "skills": skills, "files": files}


def _same(path: Path, st: os.stat_result, was: list[Any]) -> bool:
    if not isinstance(was, list) or not was:
        return False
    if was[0] == "link":
        return (stat.S_ISLNK(st.st_mode) or getattr(path, "is_junction", lambda: False)()) and os.readlink(path) == was[1]
    if was[0] != "file" or not stat.S_ISREG(st.st_mode) or len(was) < 4 or st.st_size != was[1]:
        return False
    if was[3]:
        return _digest(path) == was[3]  # read again: same size and time is not proof enough
    return st.st_mtime_ns == was[2]


def _jobs_reason(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "It has a scheduled jobs file Cabinet can't read"
    return "It has scheduled jobs" if (data.get("jobs") if isinstance(data, dict) else data) else None


def _file_reason(directory: Path, rel: str, how: str) -> str | None:
    """The plain reason one added, removed or changed file keeps the profile."""
    top = rel.split("/", 1)[0]
    if rel == "SOUL.md":
        return "Its instructions have been edited"
    if rel == "config.yaml":
        return "Its settings (model, tools or skills) have been changed"
    if rel == ".env":
        return "Its keys file (.env) has been changed"
    if rel == "profile.yaml":
        return "Its name or description has been changed"
    if top == "skills":
        return "Skills have been added or removed"
    if top == "memories":
        return "It has saved memories"
    if top == "sessions" or rel == ".hermes_history":
        return "You have chatted with it"
    if rel == "cron/jobs.json" and how != "removed":
        return _jobs_reason(directory / rel)  # Hermes may write an empty jobs file on its own
    if top == "cron":
        return "It has scheduled jobs"
    return {"added": "Files have been added to it", "removed": "Files have been removed from it"}.get(how, "Files in it have been changed")


def _manifest_reasons(directory: Path, files: dict[str, Any]) -> list[str]:
    try:
        now = _scan(directory)
        out = []
        for rel in sorted(set(now) | set(files)):
            if rel not in files:
                out.append(_file_reason(directory, rel, "added"))
            elif rel not in now:
                out.append(_file_reason(directory, rel, "removed"))
            elif not _same(directory / rel, now[rel], files[rel]):
                out.append(_file_reason(directory, rel, "changed"))
        return [r for r in out if r]
    except _TooMany:
        return [TOO_MANY]
    except OSError:
        return [UNREADABLE]


def _legacy_reasons(directory: Path, fp: dict[str, Any]) -> list[str]:
    """The checks a record made before 0.3.11 can still support. They are never enough on their own to remove it."""
    reasons: list[str] = []
    if _sha(directory / "SOUL.md") != fp.get("soul"):
        reasons.append("Its instructions have been edited")
    if _sha(directory / "config.yaml") != fp.get("config"):
        reasons.append("Its settings (model, tools or skills) have been changed")
    try:
        if fingerprint(directory)["skills"] != fp.get("skills"):
            reasons.append("Skills have been added or removed")
        for folder, why in (("memories", "It has saved memories"), ("sessions", "You have chatted with it")):
            if (directory / folder).is_dir() and any(True for _ in (directory / folder).iterdir()):
                reasons.append(why)
    except OSError:
        reasons.append(UNREADABLE)
    jobs = directory / "cron" / "jobs.json"
    if jobs.is_file():
        why = _jobs_reason(jobs)
        if why:
            reasons.append(why)
    return reasons


def _rows(db_path: Path, table: str, where: str = "") -> int | None:
    """Rows in one table of a SQLite file, opened read only. 0 when the file or table isn't there; None when unreadable.

    The file: address comes from ``as_uri()``, which escapes '#', '?', '%' and spaces and writes Windows paths as
    file:///C:/..., so any folder name opens the right file.
    """
    if not db_path.exists() and not db_path.is_symlink():
        return 0
    try:
        uri = db_path.absolute().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                return 0  # Hermes made the file but nothing is in it yet
            row = db.execute(f"SELECT COUNT(*) FROM {table}{' WHERE ' + where if where else ''}").fetchone()
        return int(row[0] or 0)
    except (sqlite3.Error, ValueError, OSError):
        return None


def _user_messages(directory: Path) -> int | None:
    """How many messages the owner has sent in this profile's chat store. 0 when none; None when Cabinet can't tell."""
    return _rows(directory / "state.db", "messages", "role='user'")


def touched_reasons(directory: Path, fp: dict[str, Any]) -> list[str]:
    """Why this profile is no longer exactly what Cabinet made. Empty means untouched, and only then may it be removed.

    Every file counts: anything added, removed or changed since Cabinet recorded the profile keeps it, except the
    runtime files Hermes writes on its own (``_RUNTIME_FILES``, ``_RUNTIME_DIRS``, ``_RUNTIME_PATHS``). A record with no file list keeps it too.
    """
    files = fp.get("files") if isinstance(fp, dict) else None
    if isinstance(files, dict):
        reasons = _manifest_reasons(directory, files)
    else:
        reasons = _legacy_reasons(directory, fp if isinstance(fp, dict) else {}) + [OLD_RECORD]
    said = _user_messages(directory)
    if said is None:
        reasons.append(NO_HISTORY)
    elif said:
        reasons.append("You have chatted with it")
    projects = _rows(directory / "projects.db", "projects")
    if projects is None:
        reasons.append("Cabinet couldn't read its projects list")
    elif projects:
        reasons.append("It has projects you set up")
    return list(dict.fromkeys(reasons))


def _write_atomic(path: Path, text: str) -> None:
    """Write through a fresh temporary file in the same folder, then swap it in.

    ``mkstemp`` makes a new file with a name nobody could guess and never opens one that is already there, so a link
    planted next to SOUL.md can't redirect the write. ``os.replace`` swaps the file itself, never a link's target.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".cabinet")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))  # keep the old file's permissions, not mkstemp's 0600
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _birth_time(st: os.stat_result) -> float | None:
    """When the folder was made, where the system records it: macOS and BSD (st_birthtime), Windows (st_ctime)."""
    born = getattr(st, "st_birthtime", None)
    if born:
        return born
    return st.st_ctime if os.name == "nt" else None


def _born_after(directory: Path, planned_at: float) -> bool:
    """Was this folder made after the plan? Without a birth time (Linux) the folder's own time won't do: it moves each
    time a file is added. The oldest time on the folder or anything directly in it stands in instead, because a
    profile that was there before the plan still holds older files."""
    try:
        st = directory.stat()
        born = _birth_time(st)
        if born is None:
            times = [st.st_mtime]
            for n, entry in enumerate(directory.iterdir()):
                if n >= 2000:
                    break
                times.append(entry.lstat().st_mtime)
            born = min(times)
    except OSError:
        return False
    return born >= planned_at - PLAN_SLACK_SECONDS


def _hermes_bots_refusal(profile: str, directory: Path) -> ApiError | None:
    """A profile Hermes's own Bots catalog added is Hermes's. Cabinet never records it, writes into it or removes it."""
    if hermes.hermes_catalog_name(directory) is None:
        return None
    return _bad(409, f"Hermes Bots added the profile called '{profile}', so Cabinet leaves it alone and won't record it as its own.",
                hermesBots=True)


def _verify_created(profile: str, soul: str, planned_at: float, *, made_by_cabinet: bool = True) -> tuple[Path, str]:
    """Look at what Hermes made. Returns (folder, note) or raises with a plain reason.

    ``made_by_cabinet`` is True only when Cabinet's own profiles.create call succeeded. Then, and only then, Cabinet
    repairs the instructions file if Hermes didn't save it. Otherwise (the reply was lost, or the create failed) the
    folder counts only if its instructions are already exactly the planned ones.
    """
    try:
        directory = hermes.profile_dir(profile)
    except (hermes.HermesError, safety.Unsafe) as exc:
        raise _bad(422, str(exc)) from exc
    if directory.is_symlink() or not directory.is_dir():
        raise _bad(409, f"Hermes hasn't made a profile called '{profile}'.")
    refusal = _hermes_bots_refusal(profile, directory)
    if refusal is not None:
        raise refusal
    if not _born_after(directory, planned_at):
        raise _bad(409, f"A profile called '{profile}' was already there before this plan, so Cabinet won't record it as its own.")
    note = ""
    if not _same_text(directory / "SOUL.md", soul):
        if not made_by_cabinet:
            raise _bad(409, f"A profile called '{profile}' is there, but Cabinet didn't make it and its instructions aren't the planned ones, "
                            "so Cabinet won't record it as its own.")
        # Hermes saves the instructions best-effort. Cabinet's own create just made this folder, so write them.
        try:
            _write_atomic(directory / "SOUL.md", soul)
        except OSError as exc:
            raise _bad(500, "The new profile's instructions couldn't be saved.") from exc
        note = "Hermes didn't save the instructions, so Cabinet wrote them."
        if not _same_text(directory / "SOUL.md", soul):
            raise _bad(500, "The new profile's instructions couldn't be saved.")
    return directory, note


def _made_from_plan(profile: str, soul: str, planned_at: float) -> bool:
    """True when a profile of this name is on disk, new since the plan, with exactly the planned instructions,
    and Hermes's own Bots catalog didn't add it."""
    try:
        directory = hermes.profile_dir(profile)
    except (hermes.HermesError, safety.Unsafe):
        return False
    return (directory.is_dir() and not directory.is_symlink() and _born_after(directory, planned_at)
            and hermes.hermes_catalog_name(directory) is None and _same_text(directory / "SOUL.md", soul))


def _record(plan: dict[str, Any], *, made_by_cabinet: bool, crew_id: int | None = None,
            source: dict[str, Any] | None = None) -> dict[str, Any]:
    p = plan["payload"]
    directory, note = _verify_created(p["profile"], p["soul"], plan["createdAt"], made_by_cabinet=made_by_cabinet)
    shown = hermes.set_display_name(directory, p["displayName"]) if p.get("displayName") else False
    st = store()
    install_id = st.add_install(profile=p["profile"], kind=p["kind"] if crew_id is None else "crew-member",
                                source_slug=(source or p["source"]).get("slug") or "", source_name=(source or p["source"]).get("name") or "",
                                path=str(directory), fingerprint=fingerprint(directory), display_name=p.get("displayName", ""),
                                description=p.get("description", ""), crew_id=crew_id, catalog_commit=p["source"].get("commit", ""))
    st.log("install", p["profile"], f"{p['kind']} from {p['source'].get('name')}")
    return {"installId": install_id, "profile": p["profile"], "note": note, "displayNameSet": shown}


def confirm(plan_id: str, *, created: bool = True) -> dict[str, Any]:
    """Step 3 for one Bot or Lab plan: record the profile Hermes just made.

    ``created`` says whether the page's profiles.create call succeeded. When it didn't (a lost reply, a timeout),
    Cabinet records the profile only if it is exactly what the plan asked for, and changes nothing in it.
    """
    st = store()
    plan = st.get_plan(plan_id)
    if not plan or plan["kind"] not in ("bot", "lab"):
        raise _bad(404, "That plan isn't known any more. Open the review screen again.")
    if plan["status"] == "done":
        # A second confirm is only ever an answer about the first. A done plan is never recorded again: the profile of
        # that name may since have been deleted and made again by the owner, and recording would rewrite its SOUL.md.
        existing = st.active_install_for(plan["payload"]["profile"])
        if existing:
            return {"ok": True, "already": True, "install": _install_view(existing)}
        raise _bad(409, "That plan was already used, and Cabinet no longer tracks what it made. Open the review screen again.")
    if plan["status"] != "pending":
        raise _bad(410, "That plan has expired. Open the review screen again.")
    done = _record(plan, made_by_cabinet=bool(created))
    st.set_plan_status(plan_id, "done")
    return {"ok": True, "already": False, "note": done["note"], "install": _install_view(st.get_install(done["installId"]))}


def reconcile() -> int:
    """Record profiles that were created from a plan but never confirmed (the app closed in between)."""
    st = store()
    st.expire_plans()
    recorded = 0
    for plan in st.pending_plans():
        if plan["kind"] not in ("bot", "lab"):
            continue
        p = plan["payload"]
        try:
            directory = hermes.profile_dir(p["profile"])
        except (hermes.HermesError, safety.Unsafe):
            continue
        if not directory.is_dir() or directory.is_symlink() or st.active_install_for(p["profile"]):
            continue
        if _made_from_plan(p["profile"], p["soul"], plan["createdAt"]):
            try:
                _record(plan, made_by_cabinet=False)
                st.set_plan_status(plan["id"], "done")
                recorded += 1
            except ApiError:
                continue
    return recorded


# ------------------------------------------------------------------ crews --

# Words in a crew's handoff order that mean a person, not a bot. "Hiring manager" and "learner" are the owner
# in the crews that use them; every one of them is a point where the crew stops for you.
_PERSON = re.compile(r"\b(hiring manager|person|you|owner|learner)\b", re.I)


def _who(owner: str, members: list[dict[str, Any]]) -> dict[str, Any]:
    """The bots named at a step, in the order the step names them, and whether a person is there too."""
    named = [(owner.lower().find(m["name"].lower()), m["slug"]) for m in members
             if re.search(rf"\b{re.escape(m['name'])}\b", owner, re.I)]
    return {"bots": [slug for _, slug in sorted(named)], "you": bool(_PERSON.search(owner))}


def _owner_words(owner: str) -> str:
    """The step's owner as the page and the cards say it: the bots by name, a person as "you"."""
    text = re.sub(r"\bhiring manager\b", "you (as hiring manager)", owner, flags=re.I)
    text = re.sub(r"\b(person|owner|learner)\b", "you", text, flags=re.I)
    return text[:1].upper() + text[1:]


def handoff_steps(crew: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for i, step in enumerate(crew["handoffOrder"], 1):
        who = _who(step["owner"], crew["members"])
        out.append({"n": i, "step": step["timing"], "action": _title(step["action"]), "owner": _owner_words(step["owner"]),
                    "bots": who["bots"], "you": who["you"]})
    return out


def _names(step: dict[str, Any], names: dict[str, str], *, skip: str | None = None) -> str:
    """'Planner and the owner', 'Coach', or 'the owner': who is at a step, in plain words."""
    people = [names.get(s, s) for s in step["bots"] if s != skip]
    if step["you"]:
        people.append("the owner")
    if not people:
        return "the owner"
    return people[0] if len(people) == 1 else ", ".join(people[:-1]) + " and " + people[-1]


def crew_context(crew: dict[str, Any], slug: str) -> dict[str, Any]:
    """Where one bot sits in a crew, worked out from the crew's handoff order (not the order its members are listed in)."""
    steps = handoff_steps(crew)
    names = {m["slug"]: m["name"] for m in crew["members"]}
    member = next((m for m in crew["members"] if m["slug"] == slug), {"responsibility": ""})
    mine = []
    for idx, st in enumerate(steps):
        if slug not in st["bots"]:
            continue
        prev = steps[idx - 1] if idx else None
        nxt = steps[idx + 1] if idx + 1 < len(steps) else None
        shared = len(st["bots"]) > 1 or st["you"]
        mine.append({"n": st["n"], "step": st["step"], "with": _names(st, names, skip=slug) if shared else "",
                     "from": _names(prev, names) if prev else "", "to": _names(nxt, names) if nxt else ""})
    return {"name": crew["name"], "responsibility": _title(member.get("responsibility", "")), "total": len(steps),
            "order": [f"{st['n']}. {st['step']}: {_names(st, names)}. {st['action']}." for st in steps], "mine": mine,
            "approvals": list(crew.get("approvalActions") or []), "offLimits": list(crew.get("prohibitedActions") or [])}


def crew_section(c: dict[str, Any]) -> list[str]:
    """The part of a crew member's SOUL.md that says where it sits in the crew and how work reaches it."""
    out = [f"## Your place in the {c['name']} crew", "", f"Your part: {c['responsibility']}.", "", "The crew works in this order:", ""]
    out += c["order"] + [""]
    for m in c["mine"]:
        line = f"Step {m['n']} of {c['total']} ({m['step']}) is yours"
        line += f", together with {m['with']}." if m["with"] else "."
        line += f" Work reaches you from step {m['n'] - 1}: {m['from']}." if m["from"] else " It is the first step, so the owner starts it."
        line += f" Your result goes next to step {m['n'] + 1}: {m['to']}." if m["to"] else " It is the last step."
        out.append(line)
    if not c["mine"]:
        out.append("The handoff order doesn't name you for a step. Work when the owner asks you to.")
    if c.get("approvals"):
        out += ["", "For the whole crew, ask the owner before you:", ""] + [f"- {a}" for a in c["approvals"]]
    if c.get("offLimits"):
        out += ["", "For the whole crew, never:", ""] + [f"- {a}" for a in c["offLimits"]]
    out += ["",
            "Hermes does not pass work between the bots in a crew. When you finish your step, end with a short handoff "
            "note saying what the next step needs. The owner carries it on, or releases the next Kanban card.",
            "Stop and wait for the owner at every step that includes the owner. Do not start the next step yourself.", ""]
    return out


def what_hermes_does() -> dict[str, Any]:
    """The honest account of crews, shown on the crew screen. Cabinet only claims what it has built and tested.

    Group chats are Hermes's own (apps/desktop/src/plugins/hermes-bots/group-chat.ts and group-rounds.ts): one shared
    room, every member hears it, @name picks who answers, and one message from you starts at most three short rounds
    in which the bots take turns. There is no set order of steps and no stop for the owner, which is what a Crew Kit adds.
    """
    return {
        "can": ["Make one Hermes profile for each bot in the crew, each with its role written into its instructions.",
                "Keep a record of the crew: who is in it, the order of the handoffs, and where it stops for you.",
                "When you ask, make one Kanban card per step, in order, each assigned to the right bot."],
        "cannot": ["Run the crew by itself. Hermes does not read the handoff order and pass work along on its own.",
                   "Move work from one step to the next for you. You do that by releasing the next Kanban card, "
                   "or by asking each bot yourself.",
                   "Turn on any schedule, connect an account or send anything. Those stay yours."],
        "groupChat": "Hermes has group chats too: press + next to Bots in the sidebar, choose New group chat and add these bots. "
                     "Every bot hears the conversation, and you can @mention one by name. The bots reply in no set order, so "
                     "use it to talk the job through. The crew's order and its stops for you stay with the Kanban cards.",
        "cards": "The cards start blocked. Nothing runs until you release the first one in Kanban.",
    }


def plan_crew(slug: str, choices: dict[str, Any] | None = None) -> dict[str, Any]:
    crew = catalog.crew(slug)
    if not crew:
        raise _bad(404, "No Crew Kit by that name in the catalog.")
    choices = choices if isinstance(choices, dict) else {}
    if safety.body_size(choices) > safety.MAX_BODY_BYTES:
        raise _bad(413, "That's too much text for one crew.")
    if len(crew["members"]) > safety.MAX_CREW_MEMBERS:
        raise _bad(422, "This crew has more bots than Cabinet will set up at once.")
    st = store()
    existing = [n for n in hermes.existing_profiles() if n != "default"]
    carried, added = hermes_lane()
    steps = handoff_steps(crew)
    taken: set[str] = set()
    problems: list[dict[str, str]] = []
    members: list[dict[str, Any]] = []
    for m in crew["members"]:
        bot = catalog.bot(m["slug"]) or {}
        pick = choices.get(m["slug"]) if isinstance(choices.get(m["slug"]), dict) else {}
        base = safety.suggest_profile_name(bot.get("name", m["name"]))
        by_cabinet = next((i for i in st.installs("active") if i["source_slug"] == m["slug"] and i["profile"] in existing), None)
        incomplete = {c["id"] for c in st.crews("incomplete") if c["slug"] == slug}
        resume = bool(by_cabinet and by_cabinet.get("crew_id") in incomplete)
        candidates = [n for n in existing if n == base or n == m["slug"]]
        if by_cabinet and by_cabinet["profile"] not in candidates:
            candidates.insert(0, by_cabinet["profile"])
        # Hermes Bots' lane: a member it carries is never made here. The profile it added (under any name) is offered first.
        lane_profile = added.get(m["slug"]) if added.get(m["slug"]) in existing else None
        handed = m["slug"] in carried or bool(lane_profile)
        if lane_profile:
            candidates = [lane_profile] + [c for c in candidates if c != lane_profile]
        default_mode = "existing" if candidates or handed else "create"
        mode = pick.get("mode") if pick.get("mode") in ("create", "existing") else default_mode
        if handed:
            mode = "existing"
        item: dict[str, Any] = {"slug": m["slug"], "name": m["name"], "responsibility": _title(m["responsibility"]), "mode": mode,
                                "candidates": candidates, "byCabinet": bool(by_cabinet), "resume": resume, "profile": None,
                                "create": None, "soul": None, "problems": [],
                                "hermesBots": {"catalogName": m["slug"], "profile": lane_profile} if handed else None}
        mp: list[dict[str, str]] = []
        if mode == "existing":
            chosen = pick.get("profile") if isinstance(pick.get("profile"), str) else (candidates[0] if candidates else "")
            chosen = _checked("profile", lambda: safety.profile_name(chosen), mp, "") if chosen or not handed else ""
            if chosen and chosen not in existing:
                mp.append({"field": "profile", "message": f"There's no profile called '{chosen}'."})
            if chosen and chosen in taken:
                mp.append({"field": "profile", "message": f"'{chosen}' is already used by another bot in this crew."})
            item["profile"] = chosen
            # Cabinet never writes into a profile you already have, so it hands you the crew part to paste in yourself.
            item["brief"] = "\n".join(crew_section(crew_context(crew, m["slug"]))).strip() + "\n"
            if not chosen and not candidates:
                mp.append({"field": "profile", "message": (f"{m['name']} comes from Hermes's own Bots catalog. Add it there (Capabilities, then the Bots tab), then press "
                                                            "Check again and the crew uses it." if handed
                                                           else "Pick one of your profiles, or choose to make a new one.")})
            if chosen:
                taken.add(chosen)
        else:
            display = pick.get("displayName") if isinstance(pick.get("displayName"), str) else bot.get("name", m["name"])
            raw = pick.get("profile") if isinstance(pick.get("profile"), str) and pick.get("profile") else hermes.free_name(base)
            while raw in taken:  # two crew members can't pick one new name
                raw = hermes.free_name(raw + "-x")[:60]
            ctx = crew_context(crew, m["slug"])
            sub = _profile_plan("crew-member", source={"slug": m["slug"], "name": m["name"], "title": bot.get("title", ""),
                                                      "href": bot.get("href"), "commit": _commit(), "credits": bot.get("credits", [])},
                                profile_raw=raw, display_raw=display, description_raw=_title(bot.get("summary", m["responsibility"])),
                                soul_raw=bot_soul(bot, ctx) if bot else m["responsibility"], no_skills=True, persist=False)
            mp.extend(sub["problems"])
            item.update({"profile": sub["profile"], "displayName": sub["displayName"], "description": sub["description"],
                         "soul": sub["soul"], "where": sub["where"], "create": sub["create"], "suggestion": sub["suggestion"]})
            taken.add(sub["profile"])
        item["problems"] = mp
        problems.extend({"field": f"{m['slug']}.{x['field']}", "message": x["message"]} for x in mp)
        members.append(item)
    plan = {"kind": "crew", "crew": {"slug": crew["slug"], "name": crew["name"], "summary": _title(crew["summary"]), "href": crew.get("href"),
                                      "testStatus": crew["testStatus"]},
            "members": members, "steps": steps, "approvals": crew["approvalActions"], "offLimits": crew["prohibitedActions"],
            "existingProfiles": existing,
            "honest": what_hermes_does(), "kanban": {"available": hermes.kanban_available()},
            "ready": not problems, "problems": problems, "planId": None, "source": {"commit": _commit(), "name": crew["name"], "slug": crew["slug"]}}
    plan["willCreate"] = sum(1 for x in members if x["mode"] == "create")
    plan["willReuse"] = sum(1 for x in members if x["mode"] == "existing")
    if plan["ready"]:
        plan["planId"] = st.add_plan("crew", {k: v for k, v in plan.items() if k != "planId"})
    return plan


def confirm_crew(plan_id: str, created: list[str] | None) -> dict[str, Any]:
    """Record a crew: each profile Hermes made for it, and the ones the owner already had."""
    st = store()
    plan = st.get_plan(plan_id)
    if not plan or plan["kind"] != "crew":
        raise _bad(404, "That plan isn't known any more. Open the crew screen again.")
    if plan["status"] == "done":
        raise _bad(409, "That crew is already recorded.")
    if plan["status"] != "pending":
        raise _bad(410, "That plan has expired. Open the crew screen again.")
    p = plan["payload"]
    created = [c for c in (created or []) if isinstance(c, str)]
    members_out: list[dict[str, Any]] = []
    missing: list[str] = []
    verified: list[tuple[dict[str, Any], Path, str]] = []
    for m in p["members"]:
        entry = {"slug": m["slug"], "name": m["name"], "responsibility": m["responsibility"], "profile": m["profile"],
                 "mode": "existing" if m["mode"] == "existing" else "created", "installId": None}
        if m["mode"] == "existing":
            if m["profile"] not in hermes.existing_profiles():
                raise _bad(409, f"The profile '{m['profile']}' you chose for {m['name']} isn't there any more.")
            members_out.append(entry)
            continue
        if m["profile"] not in created and not _made_from_plan(m["profile"], m["soul"], plan["createdAt"]):
            # The page didn't report it and it isn't on disk as planned. (A reply Hermes sent too late still counts:
            # the profile is found on disk, new since the plan and carrying the planned instructions.)
            missing.append(m["name"])
            entry["mode"] = "missing"
            members_out.append(entry)
            continue
        directory, note = _verify_created(m["profile"], m["soul"], plan["createdAt"], made_by_cabinet=m["profile"] in created)
        verified.append((m, directory, note))
        members_out.append(entry)
    status = "active" if not missing else "incomplete"
    crew_id = st.add_crew(slug=p["crew"]["slug"], name=p["crew"]["name"], members=members_out, handoff=p["steps"],
                          catalog_commit=p["source"]["commit"], status=status)
    installs: dict[str, int] = {}
    for m, directory, _note in verified:
        if m.get("displayName"):
            hermes.set_display_name(directory, m["displayName"])
        installs[m["slug"]] = st.add_install(profile=m["profile"], kind="crew-member", source_slug=m["slug"], source_name=m["name"],
                                             path=str(directory), fingerprint=fingerprint(directory), display_name=m.get("displayName", ""),
                                             description=m.get("description", ""), crew_id=crew_id, catalog_commit=p["source"]["commit"])
    # Resume: profiles Cabinet made for an earlier, unfinished try of this crew move into the new record.
    old_ids = {c["id"] for c in st.crews("incomplete") if c["slug"] == p["crew"]["slug"] and c["id"] != crew_id}
    for m in p["members"]:
        if m["mode"] == "existing" and m.get("resume"):
            prior = st.active_install_for(m["profile"])
            if prior and prior.get("crew_id") in old_ids:
                st.set_install_crew(prior["id"], crew_id)
                installs[m["slug"]] = prior["id"]
    for e in members_out:
        if e["slug"] in installs:
            e["installId"] = installs[e["slug"]]
            e["mode"] = "created"
    st.update_crew(crew_id, members=members_out)
    for old in old_ids:
        st.update_crew(old, status="superseded")
    st.set_plan_status(plan_id, "done")
    st.log("crew", p["crew"]["name"], f"{len(verified)} made, {sum(1 for e in members_out if e['mode'] == 'existing')} reused, {len(missing)} missing")
    return {"ok": not missing, "crew": _crew_view(st.get_crew(crew_id)), "missing": missing}


def _cards_for(crew: dict[str, Any]) -> list[dict[str, Any]]:
    by_slug = {m["slug"]: m for m in crew["members"]}
    total = len(crew["handoff"])
    cards = []
    for step in crew["handoff"]:
        with_profile = [s for s in step["bots"] if s in by_slug and by_slug[s].get("profile")]
        owner_slug = with_profile[0] if with_profile else None
        assignee = by_slug[owner_slug]["profile"] if owner_slug else None
        lines = [f"Step {step['n']} of {total} in the {crew['name']} crew: {step['step']}.", "", f"What to do: {step['action']}.",
                 f"Who: {step['owner']}."]
        if assignee:
            lines += [f"This card is assigned to {by_slug[owner_slug]['name']} (profile {assignee})."]
        others = [by_slug[s] for s in with_profile[1:]]
        if others:
            lines += ["A Kanban card has one assignee, so ask " + " and ".join(f"{o['name']} (profile {o['profile']})" for o in others)
                      + " for their part of this step yourself."]
        if not assignee and not step["you"]:
            lines += ["No bot in this crew is named for this step. Do it yourself or assign the card in Kanban."]
        if step["you"]:
            lines += ["", "This step stops for the owner. Do the work, then wait for their go-ahead before anything moves on."]
        lines += ["", "This card was made by Cabinet and starts blocked. Release it in Kanban when you want the step to run."]
        cards.append({"n": step["n"], "title": f"{crew['name']}: {step['step']}", "assignee": assignee, "body": "\n".join(lines),
                      "key": f"cabinet:{crew['id']}:{step['n']}", "you": step["you"], "bots": step["bots"]})
    return cards


def cards_plan(crew_id: int) -> dict[str, Any]:
    st = store()
    crew = st.get_crew(crew_id)
    if not crew or crew["status"] not in ("active", "incomplete"):
        raise _bad(404, "That crew isn't recorded.")
    cards = _cards_for(crew)
    return {"crewId": crew_id, "available": hermes.kanban_available(), "created": bool(crew["cards"]), "cardIds": crew["cards"],
            "cards": [{k: v for k, v in c.items() if k != "key"} for c in cards],
            "note": "Each card starts blocked and waits on the one before it. Nothing runs until you release the first card in Kanban.",
            "requiresConfirm": True}


def create_cards(crew_id: int, confirm_flag: Any) -> dict[str, Any]:
    if confirm_flag is not True:
        raise _bad(400, "Cabinet only makes Kanban cards after you confirm.")
    st = store()
    crew = st.get_crew(crew_id)
    if not crew or crew["status"] != "active":
        raise _bad(409, "Set the crew up completely before making its cards.")
    if crew["cards"]:
        raise _bad(409, "This crew's cards are already made.")
    if not hermes.kanban_available():
        raise _bad(409, "This Hermes has no Kanban board Cabinet can reach.")
    cards = _cards_for(crew)
    try:
        ids = hermes.create_cards(cards)
    except hermes.HermesError as exc:
        raise _bad(502, str(exc)) from exc
    st.update_crew(crew_id, cards=ids)
    st.log("cards", crew["name"], f"{len(ids)} cards")
    return {"ok": True, "cardIds": ids, "crew": _crew_view(st.get_crew(crew_id))}


# ------------------------------------------------------------------ kits --

def save_kit(name: str, fields: dict[str, str], job: str) -> dict[str, Any]:
    problems: list[dict[str, str]] = []
    clean_name = _checked("name", lambda: safety.display_name(name, "name"), problems, "")
    clean: dict[str, str] = {}
    total = 0
    for key, value in (fields or {}).items():
        if not isinstance(value, str) or not isinstance(key, str) or len(key) > 40:
            continue
        text = _checked(key, lambda: safety.one_line(value.replace("\n", " "), key, 1000, label="That field"), problems, "")
        total += len(text)
        clean[key] = text
    if total > 12000:
        problems.append({"field": "fields", "message": "That plan is too long to save as a kit."})
    soul_probe = "\n".join(clean.values())
    secret = safety.contains_secret(soul_probe)
    if secret:
        problems.append({"field": "fields", "message": f"The plan looks like it contains {secret}. Take it out first. Cabinet never stores keys."})
    if problems:
        raise _bad(422, "The kit couldn't be saved.", problems=problems)
    kit_id = store().add_kit(clean_name, clean, safety.one_line(job or "", "job", 300, label="The job"))
    store().log("kit", clean_name, "saved")
    return {"ok": True, "kit": _kit_view(store().get_kit(kit_id))}


# ------------------------------------------------------------------ views --

def _install_view(inst: dict[str, Any] | None) -> dict[str, Any]:
    if not inst:
        return {}
    directory = Path(inst["path"])
    exists = directory.is_dir() and not directory.is_symlink()
    reasons = touched_reasons(directory, inst["fingerprint"]) if exists and inst["status"] == "active" else []
    if inst["status"] != "active":
        status, text = inst["status"], {"removed": "Removed", "released": "No longer tracked by Cabinet"}.get(inst["status"], inst["status"])
    elif not exists:
        status, text = "missing", "No longer in Hermes"
    elif reasons:
        status, text = "changed", "You've changed it since Cabinet made it"
    else:
        status, text = "ready", "Made by Cabinet, not changed yet"
    return {"id": inst["id"], "profile": inst["profile"], "displayName": inst["display_name"] or inst["profile"], "kind": inst["kind"],
            "source": {"slug": inst["source_slug"], "name": inst["source_name"]} if inst["source_slug"] else None,
            "createdAt": _iso(inst["created_at"]), "where": paths.display(inst["path"]), "status": status, "statusText": text, "reasons": reasons,
            "canRemove": inst["status"] == "active" and not reasons and inst["crew_id"] is None,
            "canForget": inst["status"] == "active" and not exists, "crewId": inst["crew_id"], "description": inst["description"]}


def _crew_view(crew: dict[str, Any] | None) -> dict[str, Any]:
    if not crew:
        return {}
    st = store()
    installs = {i["id"]: _install_view(i) for i in st.installs_for_crew(crew["id"])}
    members = []
    for m in crew["members"]:
        view = installs.get(m.get("installId")) if m.get("installId") else None
        members.append({**m, "install": view})
    changed = [v for v in installs.values() if v["status"] == "changed"]
    return {"id": crew["id"], "slug": crew["slug"], "name": crew["name"], "status": crew["status"], "createdAt": _iso(crew["created_at"]),
            "members": members, "steps": crew["handoff"], "cardIds": crew["cards"], "cardsMade": bool(crew["cards"]),
            "canRemove": crew["status"] in ("active", "incomplete"), "changedCount": len(changed),
            "statusText": {"active": "Ready", "incomplete": "Not finished: some bots weren't made",
                           "removed": "Removed", "superseded": "Replaced by a newer set-up"}.get(crew["status"], crew["status"])}


def _kit_view(kit: dict[str, Any] | None) -> dict[str, Any]:
    if not kit:
        return {}
    return {"id": kit["id"], "name": kit["name"], "job": kit["job"] or "", "createdAt": _iso(kit["created_at"]), "fields": kit["fields"]}


def my_bots() -> dict[str, Any]:
    """Everything Cabinet set up, with its live state. Also picks up any profile made from a plan that was never confirmed."""
    reconcile()
    st = store()
    bots = [_install_view(i) for i in st.installs("active") if i["crew_id"] is None]
    crews = [_crew_view(c) for c in st.crews() if c["status"] in ("active", "incomplete")]
    # A profile Cabinet made before Hermes had its own Bots catalog: say so, so adding it there isn't a silent second copy.
    carried, added = hermes_lane()
    overlaps = []
    for view in bots + [m["install"] for c in crews for m in c["members"] if m.get("install")]:
        slug = (view.get("source") or {}).get("slug")
        if slug and (slug in carried or slug in added):
            view["alsoInHermesBots"] = {"catalogName": slug, "profile": added.get(slug)}
            overlaps.append({"profile": view["profile"], "slug": slug, "name": (view.get("source") or {}).get("name") or slug,
                             "hermesProfile": added.get(slug),
                             "message": (f"{(view.get('source') or {}).get('name') or slug}: Cabinet made '{view['profile']}' before your "
                                         "Hermes had its own Bots catalog"
                                         + (f", and Hermes Bots added it too, as '{added[slug]}'. Keep the one you use and remove "
                                            "the other" if added.get(slug) else
                                            ". Keep using it; there's no need to add it again in Hermes Bots")
                                         + ".")})
    return {"bots": bots, "crews": crews, "kits": [_kit_view(k) for k in st.kits()], "hermesBots": hermes_bots_added(), "overlaps": overlaps,
            "history": [{"at": _iso(e["at"]), "action": e["action"], "subject": e["subject"], "detail": e["detail"]} for e in st.events(20)],
            "where": paths.display(hermes.profiles_root())}


def hermes_bots_added() -> list[dict[str, Any]]:
    """Cabinet's Bots that Hermes's own Bots catalog has already added, so the page offers no second copy.

    A catalog name matches a Cabinet Bot by its slug. Read only: Cabinet never records, edits or removes these.
    """
    out = []
    for name, profile in sorted(hermes.hermes_catalog_installs().items()):
        bot = catalog.bot(name)
        if bot:
            out.append({"slug": bot["slug"], "name": bot["name"], "catalogName": name, "profile": profile,
                        "where": paths.display(hermes.profiles_root() / profile)})
    return out


# ------------------------------------------------------------------ undo --

def removal_preview(kind: str, item_id: int) -> dict[str, Any]:
    """What Remove would do, without doing it."""
    st = store()
    if kind == "bot":
        inst = st.get_install(item_id)
        if not inst or inst["status"] != "active":
            raise _bad(404, "Cabinet has no record of that bot.")
        v = _install_view(inst)
        will = [] if v["status"] != "ready" and v["status"] != "missing" else [v["profile"]]
        return {"kind": kind, "id": item_id, "name": v["displayName"], "will": will,
                "keep": [] if will else [{"profile": v["profile"], "why": v["reasons"] or [v["statusText"]]}],
                "canRemove": bool(will) and v["crewId"] is None, "where": v["where"],
                "note": ("This profile belongs to a crew. Remove the crew instead." if v["crewId"] is not None else
                         "Cabinet removes a profile only if it made it and you haven't changed it.")}
    if kind == "crew":
        crew = st.get_crew(item_id)
        if not crew or crew["status"] not in ("active", "incomplete"):
            raise _bad(404, "Cabinet has no record of that crew.")
        will, keep, reused = [], [], []
        for i in st.installs_for_crew(item_id):
            if i["status"] != "active":
                continue
            v = _install_view(i)
            (will if v["status"] in ("ready", "missing") else keep).append(
                v["profile"] if v["status"] in ("ready", "missing") else {"profile": v["profile"], "why": v["reasons"]})
        reused = [m["profile"] for m in crew["members"] if m["mode"] == "existing"]
        return {"kind": kind, "id": item_id, "name": crew["name"], "will": will, "keep": keep, "reused": reused,
                "cards": len(crew["cards"]), "canRemove": True,
                "note": "Profiles you already had are never removed. Changed profiles stay, and Cabinet stops tracking them."}
    if kind == "kit":
        kit = st.get_kit(item_id)
        if not kit or kit["status"] != "active":
            raise _bad(404, "Cabinet has no record of that kit.")
        return {"kind": kind, "id": item_id, "name": kit["name"], "will": [], "keep": [], "canRemove": True,
                "note": "A kit is only a saved plan. Removing it doesn't touch any Hermes profile."}
    raise _bad(404, "Cabinet can remove a bot, a crew or a kit, and this is none of those.")


def _delete_untouched(st: Store, inst: dict[str, Any]) -> str:
    """Remove one recorded profile if it is still untouched. Returns 'removed', 'forgotten' or 'kept'."""
    directory = Path(inst["path"])
    if not directory.exists():
        st.mark_install_removed(inst["id"])
        return "forgotten"
    if touched_reasons(directory, inst["fingerprint"]) or _hermes_bots_refusal(inst["profile"], directory) is not None:
        st.release_install(inst["id"])
        return "kept"
    # The recorded path must still be the profile folder Hermes would use for this name.
    if directory.resolve(strict=False) != hermes.profile_dir(inst["profile"]).resolve(strict=False):
        st.release_install(inst["id"])
        return "kept"
    hermes.delete_profile(inst["profile"])
    if directory.exists():
        raise hermes.HermesError("Hermes ran the delete but the folder is still there.")
    st.mark_install_removed(inst["id"])
    return "removed"


def remove(kind: str, item_id: int, confirm_flag: Any) -> dict[str, Any]:
    if confirm_flag is not True:
        raise _bad(400, "Cabinet only removes things after you confirm.")
    removal_preview(kind, item_id)  # 404s unknown things
    st = store()
    try:
        if kind == "bot":
            inst = st.get_install(item_id)
            if inst["crew_id"] is not None:
                raise _bad(409, "This profile belongs to a crew. Remove the crew instead.")
            directory = Path(inst["path"])
            reasons = touched_reasons(directory, inst["fingerprint"]) if directory.exists() else []
            if reasons:
                raise _bad(409, "Cabinet won't remove it, because it isn't the way Cabinet left it. Delete it in Hermes if you're sure.",
                           reasons=reasons)
            result = _delete_untouched(st, inst)
            st.log("remove", inst["profile"], result)
            return {"ok": True, "result": result, "mybots": my_bots()}
        if kind == "crew":
            crew = st.get_crew(item_id)
            removed, kept = [], []
            for inst in st.installs_for_crew(item_id):
                if inst["status"] != "active":
                    continue
                outcome = _delete_untouched(st, inst)
                (removed if outcome in ("removed", "forgotten") else kept).append(inst["profile"])
            archived = hermes.archive_cards(crew["cards"])
            st.update_crew(item_id, status="removed", removed_at=time.time())
            st.log("remove-crew", crew["name"], f"removed {len(removed)}, kept {len(kept)}, cards archived {archived}")
            return {"ok": True, "removed": removed, "kept": kept, "cardsArchived": archived, "mybots": my_bots()}
        st.remove_kit(item_id)
        st.log("remove-kit", str(item_id), "removed")
        return {"ok": True, "mybots": my_bots()}
    except hermes.HermesError as exc:
        raise _bad(502, str(exc)) from exc


def forget(install_id: int) -> dict[str, Any]:
    """The owner removed the profile in Hermes; drop Cabinet's record of it."""
    st = store()
    inst = st.get_install(install_id)
    if not inst or inst["status"] != "active":
        raise _bad(404, "Cabinet has no record of that bot.")
    if Path(inst["path"]).exists():
        raise _bad(409, "That profile is still in Hermes. Use Remove instead.")
    st.mark_install_removed(install_id)
    st.log("forget", inst["profile"], "")
    return {"ok": True, "mybots": my_bots()}
