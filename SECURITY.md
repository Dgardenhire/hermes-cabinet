# Security

## Reporting a problem

Use GitHub's private vulnerability reporting on this repository (Security tab, "Report a vulnerability") for anything
that should not be public first. Anything else: open an issue at https://github.com/Dgardenhire/hermes-cabinet/issues.

## What Cabinet is

A Hermes plugin with two halves:

- **Backend** (`__init__.py`, `dashboard/plugin_api.py` and the modules it calls). It registers no tools, no hooks and
  no middleware. It serves the page's routes inside Hermes's plugin host.
- **Desktop page** (`desktop/plugin.js`). One readable ESM file that imports only `@hermes/plugin-sdk`, `react` and
  `react/jsx-runtime`. It talks to the backend routes and to the local Hermes gateway (`profiles.create`,
  `bots.catalog`).

## Boundaries Cabinet holds itself to

**One network request.** `sync.py` fetches `https://botcabinet.com/catalog.json` over https, same-host redirects
only, at most 2 MB, and `catalog_schema.py` checks it against a strict schema before anything uses it. A bad or
missing file leaves the last good copy (or the copy Cabinet shipped with) in place. The file is data, never code.

**Profiles go through Hermes.** Cabinet never creates a profile folder itself. The page sends `profiles.create` to the
gateway and Cabinet then checks what Hermes made (it exists, is newer than the plan, holds the planned instructions,
and was not added by Hermes's own Bots catalog) before recording it with a fingerprint: a list of every file in the
profile, by size and SHA-256 hash (`.env` and `auth.json` by size and time only, so no hash of a key is kept, and
never any file's contents). Remove goes through Hermes's `delete_profile`, and only for a profile Cabinet recorded
whose files all still match that list. Any file added, removed or changed anywhere in the folder, hidden files
included, keeps the profile. The only files that may change are the ones Hermes writes by itself, taken from Hermes
0.22.2's own lists of runtime state it never exports (`installer.py` names each source):

- caches: `cache/`, `image_cache/`, `audio_cache/`, `document_cache/`, `browser_screenshots/`,
  `context_length_cache.yaml`, `models_dev_cache.json`, `models_dev_cache.etag`, `provider_models_cache.json`,
  `.skills_prompt_snapshot.json`, `source-checks/`;
- locks and running state: `gateway.pid`, `gateway.lock`, `gateway_state.json`, `processes.json`, `active_profile`,
  `auth.lock`, `.env.lock`, `.backup.lock`, `.update_check`, `cron/.tick.lock`, `memories/*.lock`, `runtime/`, `state/`,
  `desktop/`, `telemetry/`, and the Bot Desktop's pid, lease and browser lock files;
- logs and snapshots: `logs/`, `errors.log`, `checkpoints/`, `state-snapshots/`, `backups/`, `sandboxes/`;
- downloaded tools and models: `bin/`, `installs/`, `tools/`, `models/`, `runtimes/`, `node/`, `assets/`,
  `desktop-plugins/` (Hermes Desktop's copies of installed plugins);
- `state.db` and `projects.db` with their `-wal`, `-shm` and `-journal` files, which Hermes makes on first open. Cabinet
  reads them instead: any message you sent in `state.db`, or any project in `projects.db`, keeps the profile.

Everything else counts as yours, including `SOUL.md`, `config.yaml`, `.env`, `auth.json`, `profile.yaml`, `memories/`,
`skills/`, `sessions/`, `.hermes_history`, `workspace/`, `home/`, `plans/`, `cron/` jobs, `hooks/`, `plugins/`,
`mcp-tokens/`, `pairing/`, `skins/`, `response_store.db` and any name Cabinet doesn't know. Links are listed, never
followed. If Cabinet can't read a file, the chat history or the projects list, finds more than 5,000 files, or holds a record
from Cabinet 0.3.10 or older (which kept no file list), it keeps the profile and says why. Right before deleting,
Cabinet checks again that Hermes's own Bots catalog didn't add the profile; if it can't tell, it leaves it alone.

**Text that becomes instructions is checked.** Everything that can end up in a profile's name, display name or
`SOUL.md` passes `safety.py`: names are restricted to a safe pattern, hidden and control characters are refused, and
instructions that look like they hold a key or password are refused.

**No credentials.** Cabinet asks for no keys, declares no `requires_env`, and never reads, stores or passes on a key,
token or model setting. A new bot uses the model and sign-in Hermes gives it.

**No shell, no background processes, no telemetry, no self-updater.**

**Private Hermes modules, read through one door.** `hermes_bridge.py` is the only file that imports Hermes internals
(`hermes_cli.profiles`, `hermes_cli.kanban_db`, `hermes_cli.kanban_db_connect`, `hermes_cli.bot_catalog`), because the
plugin API has no public seam for the profile list or the Kanban board yet. Nothing in Hermes is patched or replaced.

## Data

Cabinet's own record is a SQLite file and the cached catalog in `$HERMES_HOME/state/bot-cabinet-v2/`. Deleting that
folder makes Cabinet forget everything; the bots it made stay.

## What a reviewer can check

- `hermes plugins validate . --install-deps`: passes; warnings are the private imports above.
- `grep -n "urllib\|https://" sync.py`: the only outbound request.
- `grep -rn "subprocess\|Popen\|os.system" --include='*.py' .`: nothing outside tests and dev tools.
- `cd tests && python3 -m unittest discover -p "test_*.py"`: the suite, including the test that fails if the real
  `~/.hermes` changes during a run.
