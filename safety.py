"""Everything that decides whether text may become part of a Hermes profile.

Cabinet writes three things into a new profile: its name (a folder name), its description, and its
instructions (SOUL.md). All three can end up shaped by text from the catalog, from the Bot Lab plan, or
from what the owner typed in a review screen, so each is checked here before a plan is ever shown, and
checked again before it is recorded.

The rules, each with a test:

- A profile name is a folder name. It must be 1 to 64 characters of lowercase letters, digits, ``-`` and
  ``_``, starting with a letter or digit (Hermes's own rule), and not one of Hermes's reserved names.
  A name someone TYPED that holds a slash, a backslash, ``..``, a null or a space is refused, not tidied:
  it is either a mistake or an attempt to leave the profiles folder. A name DERIVED from a display name
  ("Client Intake") is turned into a safe one ("client-intake").
- Instructions are capped at 16 KB, may not hold control or hidden direction-changing characters, and
  are refused if they look like they contain a password, key or token. The refusal never repeats the
  value it found.
- Nothing here reads, copies or writes credentials. Hermes copies the owner's sign-in into a new profile
  itself, the same as when you press New Agent.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# Hermes's reserved names (hermes_cli.profiles._RESERVED_NAMES), plus names that would shadow a system command, plus
# the device names Windows won't allow as a folder name (com1-com9 and lpt1-lpt9 too, in any case, with or without an
# extension).
RESERVED = frozenset({"hermes", "default", "test", "tmp", "root", "sudo", "cabinet", "con", "nul", "prn", "aux",
                      *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))})

MAX_NAME = 60
MAX_DESCRIPTION = 200
MAX_SOUL_BYTES = 16 * 1024
MAX_BODY_BYTES = 256 * 1024
MAX_CREW_MEMBERS = 12

_HIDDEN = re.compile("[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")

_SECRETS = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "an API key"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}"), "a GitHub token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS key"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "a Slack token"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "a Google key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{24,}"), "a bearer token"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "a login token"),
    (re.compile(r"(?i)\b(?:api[_ -]?key|secret|token|passw(?:or)?d|passphrase)\b\s*[:=]\s*['\"]?[^\s'\"]{12,}"), "a password or key"),
)


class Unsafe(ValueError):
    """Text or a name that must not be used, with a message written for the owner."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def one_line(value: Any, field: str, max_len: int, *, label: str = "") -> str:
    label = label or field
    if not isinstance(value, str):
        raise Unsafe(field, f"{label} must be text.")
    if _HIDDEN.search(value):
        raise Unsafe(field, f"{label} has hidden or control characters in it. Retype it.")
    text = unicodedata.normalize("NFC", re.sub(r"\s+", " ", value)).strip()
    if len(text) > max_len:
        raise Unsafe(field, f"{label} is {len(text)} characters. The limit is {max_len}.")
    return text


def display_name(value: Any, field: str = "displayName") -> str:
    text = one_line(value, field, MAX_NAME, label="The name")
    if not any(ch.isalnum() for ch in text):
        raise Unsafe(field, "Give it a name with at least one letter or number.")
    return text


def description(value: Any, field: str = "description") -> str:
    text = one_line(value, field, MAX_DESCRIPTION, label="The description")
    return re.sub(r"[.\s]+$", "", text)


def suggest_profile_name(display: str) -> str:
    """A safe profile id from a display name: 'Client Intake' becomes 'client-intake'."""
    folded = unicodedata.normalize("NFKD", str(display or "")).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", folded).strip("-_")[:64].strip("-_")
    return slug or "new-bot"


def profile_name(raw: Any, field: str = "profile") -> str:
    """A profile name someone typed or a plan carries. Exactly the safe form, or an error. Never tidied."""
    if not isinstance(raw, str):
        raise Unsafe(field, "The profile name must be text.")
    text = raw.strip()
    if not text:
        raise Unsafe(field, "Give the new bot a profile name.")
    if "/" in text or "\\" in text or ".." in text or "\x00" in text or text.startswith("~"):
        raise Unsafe(field, "A profile name can't hold a slash, '..' or '~'. It becomes a folder name.")
    canon = text.lower()
    if not PROFILE_ID.match(canon):
        raise Unsafe(field, f"Use lowercase letters, numbers, '-' or '_', up to 64 characters. For example: {suggest_profile_name(text)}")
    if canon in RESERVED or canon.split(".", 1)[0] in RESERVED:
        raise Unsafe(field, f"'{canon}' is a name Hermes keeps for itself. Pick a different name.")
    return canon


def contains_secret(text: str) -> str | None:
    for rx, what in _SECRETS:
        if rx.search(text):
            return what
    return None


def instructions(value: Any, field: str = "soul") -> str:
    """The text for a profile's SOUL.md."""
    if not isinstance(value, str):
        raise Unsafe(field, "The instructions must be text.")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if _HIDDEN.search(text.replace("\n", "").replace("\t", "")):
        raise Unsafe(field, "The instructions have hidden or control characters in them. Retype that part.")
    text = unicodedata.normalize("NFC", text)
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip() + "\n"
    if len(text.strip()) < 20:
        raise Unsafe(field, "The instructions are empty or too short to be useful.")
    if len(text.encode("utf-8")) > MAX_SOUL_BYTES:
        raise Unsafe(field, f"The instructions are over {MAX_SOUL_BYTES // 1024} KB. Shorten them.")
    found = contains_secret(text)
    if found:
        raise Unsafe(field, f"The instructions look like they contain {found}. A bot's instructions are plain text that "
                            "anyone with the profile can read, so take it out. Cabinet never stores keys.")
    return text


def inside(child: Path, parent: Path) -> bool:
    """True when ``child`` is really inside ``parent`` once links are resolved."""
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def body_size(body: Any) -> int:
    import json
    try:
        return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return MAX_BODY_BYTES + 1
