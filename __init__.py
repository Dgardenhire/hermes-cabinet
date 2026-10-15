"""Bot Cabinet v2 for Hermes (``bot-cabinet-v2``).

Cabinet answers one question first: what is the smallest setup that does this job? The modules:

- ``fit``: the Fit Test decision engine (a rules table; every answer traced to its question)
- ``catalog``: the published Bots, Crew Kits and job guides, snapshotted, with plain word matching
- ``api``: the page view models (Fit Test, crew plan, Bot Lab draft plan, the live catalog, add and undo)
- ``installer``, ``store``, ``safety``, ``sync``, ``hermes_bridge``: add a Bot or crew, the record of it, the checks on every name and
  instruction, the live catalog fetch, and the guarded door into Hermes

The dashboard routes live in ``dashboard/plugin_api.py`` and the page in ``desktop/plugin.js``.
``register(ctx)`` adds no tools and no hooks yet, matching ``plugin.yaml``.
"""

from __future__ import annotations

import logging

PLUGIN_ID = "bot-cabinet-v2"
TOOLS: tuple[str, ...] = ()
HOOKS: tuple[str, ...] = ()

_log = logging.getLogger("hermes.plugins.bot-cabinet-v2")


def register(ctx) -> None:
    """Nothing runs in the agent yet. The Fit Test and Bot Lab live behind the dashboard routes."""
    _log.info("bot-cabinet-v2: Fit Test and Bot Lab available on the Cabinet page")
