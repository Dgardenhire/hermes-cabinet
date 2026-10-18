"""Live sync: keep Cabinet's catalog current with botcabinet.com, and never depend on it.

When Cabinet opens (and when you press Refresh) it asks https://botcabinet.com/catalog.json for the
current catalog. What comes back is only believed if it passes ``catalog_schema``. A good copy is
kept in Cabinet's data folder. If the site can't be reached, or answers with anything wrong, Cabinet
uses the last good copy, and failing that the snapshot bundled with the plugin, and says which.

The rules for the one network call, enforced here and tested:

- ``https`` only, and only the host ``botcabinet.com``. A redirect is followed only to the same
  host over https, at most three times. Any other redirect is an error.
- The reply is read in full at most ``MAX_BYTES`` bytes, with a ``TIMEOUT`` second limit on each step.
- It must be ``application/json`` and pass the strict schema. A page that says "not found" in HTML,
  or a catalog with one stray unknown field, is refused and the saved copy stays.
- A copy older than the one already saved is refused, so an old file can't replace a new one.
- Nothing is sent but the request itself: no cookies, no account, no identifying header beyond the
  plugin's name and version.

The network door is a plain function (``open_fn(url, timeout)``), so the tests never touch the network.
"""

from __future__ import annotations

import datetime
import json
import os
import socket
import ssl
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from . import catalog, catalog_schema, paths

URL = "https://botcabinet.com/catalog.json"
ALLOWED_HOST = "botcabinet.com"
TIMEOUT = 4.0
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3
CACHE_NAME = "catalog-cache.json"
USER_AGENT = "bot-cabinet-v2-hermes-plugin/0.3.11"
# botcabinet.com publishes catalog.json (docs/site-catalog-contract.md), so a 404 is a fault like any other.
NOT_FOUND = "Cabinet couldn't find botcabinet.com's catalog file"

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"source": None, "updatedAt": None, "checkedAt": None, "error": None}


class SyncError(Exception):
    """A plain-English reason the catalog could not be refreshed."""


def check_url(url: str) -> str:
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = -1
    if parts.scheme != "https" or (parts.hostname or "").lower() != ALLOWED_HOST or parts.username or parts.password \
            or port not in (None, 443):
        raise SyncError(f"Cabinet only talks to https://{ALLOWED_HOST}, and was sent somewhere else")
    return url


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        target = check_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, target)


def _default_open(url: str, timeout: float):
    """An opener that can only do https, with the same-host redirect rule. No file:, ftp: or http:."""
    opener = urllib.request.OpenerDirector()
    for handler in (urllib.request.ProxyHandler(), urllib.request.HTTPSHandler(context=ssl.create_default_context()),
                    _SameHostRedirect(), urllib.request.HTTPErrorProcessor(), urllib.request.HTTPDefaultErrorHandler()):
        opener.add_handler(handler)
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    return opener.open(request, timeout=timeout)


def _content_type(resp: Any) -> str:
    headers = getattr(resp, "headers", None)
    value = headers.get("Content-Type", "") if headers is not None else ""
    return str(value).split(";")[0].strip().lower()


def fetch(open_fn: Callable[[str, float], Any] | None = None, url: str = URL, timeout: float = TIMEOUT) -> dict[str, Any]:
    """The site's catalog, checked, or ``SyncError`` saying why not."""
    check_url(url)
    opener = open_fn or _default_open
    try:
        resp = opener(url, timeout)
    except SyncError:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SyncError(NOT_FOUND) from exc
        raise SyncError(f"botcabinet.com answered with an error ({exc.code})") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError, ssl.SSLError, OSError) as exc:
        raise SyncError("Cabinet couldn't reach botcabinet.com") from exc
    try:
        check_url(resp.geturl() if hasattr(resp, "geturl") else url)
        status = getattr(resp, "status", 200)
        if status != 200:
            raise SyncError(f"botcabinet.com answered with an error ({status})")
        if _content_type(resp) not in ("application/json", "text/json"):
            raise SyncError("botcabinet.com's answer wasn't a catalog file")
        declared = getattr(resp, "headers", {}).get("Content-Length") if getattr(resp, "headers", None) is not None else None
        if declared and str(declared).isdigit() and int(declared) > MAX_BYTES:
            raise SyncError("botcabinet.com's catalog is larger than Cabinet accepts")
        raw = resp.read(MAX_BYTES + 1)
    except (socket.timeout, TimeoutError, OSError) as exc:
        raise SyncError("Cabinet couldn't finish reading botcabinet.com's catalog") from exc
    finally:
        try:
            resp.close()
        except Exception:
            pass
    if len(raw) > MAX_BYTES:
        raise SyncError("botcabinet.com's catalog is larger than Cabinet accepts")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SyncError("botcabinet.com's catalog wasn't readable") from exc
    try:
        return catalog_schema.validate_catalog(doc)
    except catalog_schema.SchemaError as exc:
        raise SyncError(f"botcabinet.com's catalog didn't pass Cabinet's checks ({exc})") from exc


# ------------------------------------------------------------------- the saved copy --

def cache_path(data: Path | None = None) -> Path:
    return (data or paths.data_dir()) / CACHE_NAME


def read_cache(data: Path | None = None) -> dict[str, Any] | None:
    """The last good copy, checked again on the way in. Anything wrong with it and it is ignored."""
    try:
        wrapped = json.loads(cache_path(data).read_text(encoding="utf-8"))
        doc = catalog_schema.validate_catalog(wrapped["doc"])
        return {"doc": doc, "fetchedAt": str(wrapped.get("fetchedAt") or "")}
    except (OSError, ValueError, KeyError, TypeError, catalog_schema.SchemaError):
        return None


def write_cache(doc: dict[str, Any], fetched_at: str, data: Path | None = None) -> None:
    """Save the copy through a fresh temporary file (``mkstemp``: an unguessable name, never an existing file or link)."""
    path = cache_path(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"fetchedAt": fetched_at, "doc": doc}, ensure_ascii=False))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ------------------------------------------------------------------- state --

def short_date(iso: str | None) -> str:
    try:
        d = datetime.date.fromisoformat(str(iso))
    except ValueError:
        return ""
    return f"{_MONTHS[d.month - 1]} {d.day}"


def _set(source: str, doc: dict[str, Any], error: str | None, checked_at: str | None = None) -> None:
    _STATE.update({"source": source, "updatedAt": doc["generatedAt"], "error": error,
                   "checkedAt": checked_at or _STATE.get("checkedAt")})


def status() -> dict[str, Any]:
    """What the page shows under the catalog: where it came from and how fresh it is."""
    if _STATE["source"] is None:
        activate_best()
    source, when = _STATE["source"], short_date(_STATE["updatedAt"])
    if source == "live":
        label = f"Updated {when} from botcabinet.com"
    else:
        label = f"Showing the saved copy from {when}"
    doc = catalog.load()
    error = _STATE["error"]
    # What the page shows beside the label: nothing to worry about, or what went wrong and that the copy was kept.
    if not error:
        note, tone = ("The catalog that came with Cabinet." if source == "bundled" else None), "calm"
    else:
        note, tone = f"{error}. Cabinet kept the copy it had.", "warn"
    return {"source": source, "label": label, "updatedAt": _STATE["updatedAt"], "checkedAt": _STATE["checkedAt"],
            "error": error, "note": note, "tone": tone, "url": URL, "bots": len(doc["bots"]), "crews": len(doc["crews"]),
            "siteCommit": doc["siteCommit"]}


def _best_saved(cached: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """The newer of the last good fetched copy and the bundled snapshot, as (source, doc).

    A plugin update can ship a snapshot fresher than an old saved copy; then the snapshot wins.
    """
    if cached and cached["doc"]["generatedAt"] >= catalog.bundled()["generatedAt"]:
        return "cache", cached["doc"]
    return "bundled", catalog.bundled()


def _use_saved(cached: dict[str, Any] | None, error: str | None, checked_at: str | None = None) -> None:
    source, doc = _best_saved(cached)
    catalog.set_active(doc if source == "cache" else None)
    _set(source, doc, error, checked_at)


def activate_best(data: Path | None = None) -> dict[str, Any]:
    """Startup: use the newer of the last good fetched copy and the bundled snapshot."""
    with _LOCK:
        _use_saved(read_cache(data), None)
    return status()


def refresh(data: Path | None = None, open_fn: Callable[[str, float], Any] | None = None,
            now: datetime.datetime | None = None) -> dict[str, Any]:
    """Ask botcabinet.com for the catalog once. Always returns a status; never raises for a network problem."""
    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _LOCK:
        cached = read_cache(data)
        try:
            doc = fetch(open_fn)
            held = _best_saved(cached)[1]
            if doc["generatedAt"] < held["generatedAt"]:
                raise SyncError("botcabinet.com's catalog is older than the copy Cabinet already has")
            # A date in the future would freeze every later catalog out, so it is refused.
            if doc["generatedAt"][:10] > (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%d"):
                raise SyncError("botcabinet.com's catalog is dated in the future")
            write_cache(doc, stamp, data)
            catalog.set_active(doc)
            _set("live", doc, None, stamp)
        except SyncError as exc:
            _STATE["checkedAt"] = stamp
            _use_saved(cached, str(exc), stamp)
        except OSError as exc:  # the data folder couldn't be written: use the fresh copy this run anyway
            catalog.set_active(doc)
            _set("live", doc, f"Cabinet couldn't save the copy ({exc.__class__.__name__})", stamp)
    return status()
