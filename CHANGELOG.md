# Changelog

## 0.3.8 (2026-10-16)

Hermes Bots' lane, enforced everywhere. Checked against the real Hermes Bots catalog code
(NousResearch/hermes-agent#130283, which includes #119260) for the four Bots both catalogs carry: Daily Newspaper,
Receipt, Coach and Professor.

- Cabinet's backend refuses to add a Bot that Hermes's own Bots catalog carries or has already added, even if asked
  directly, and names the Hermes Bots profile when there is one.
- Crews: a member the Hermes catalog carries is never made by Cabinet. The crew uses the profile Hermes Bots added,
  under whatever name, or asks you to add it there first.
- A Bot Cabinet made before your Hermes had its own catalog is flagged in My Bots, so you keep one copy.
- The page hands over using the catalog the backend reads from Hermes itself, so a slow gateway no longer lets a
  carried Bot fall back to Cabinet's own Add.
- The manual ships in the repository (docs/MANUAL.md, with screenshots), and the README opens with Cabinet's card.

## 0.3.7 (2026-10-16)

Every page reads cleanly.

- A Bot's first task shows as numbered steps when it has more than one, on its page and after you add it. How-to
  text (the group chat tip, the hand-over to Hermes Bots, crew permissions, why a Bot can't be removed) is a list.
- Fit Test answers are in plain English: each summary, reason, first test, done check and schedule step.
- "Asks you before" lists one action per line, and Bot Lab's default approvals are four plain items.
- The catalog is botcabinet.com's corrected copy (Oct 16): "Off limits" lists what a Bot never does instead of
  orders to the Bot, jobs read in the second person, crew members say what they do, and Daily Newspaper's repeated
  approval line is gone.
- The install check states its date once, beside its heading.
- Plainer Start Here and Test Records wording, and the README's promise about removal matches what Cabinet does: it
  removes only what it made and you haven't changed.

## 0.3.6 (2026-10-15)

- Start Here links the two places the Hermes community keeps its know-how: Hermes Atlas (Kevin Simback) and the Hermes
  Bible (iamlukethedev).
- README: botcabinet.com now publishes its catalog file, and the verified list says what was checked on Oct 15, 2026.

## 0.3.5 (2026-10-15)

- Profile folders show as `~/.hermes/profiles/<name>`, so a shared screenshot never shows the person's user name. Only
  the display changes; records and file operations keep the full path.
- My Bots shows the day a bot was added in the person's own time zone (an Oklahoma evening read as the next day).
- The screenshot harness mirrors a real install (Hermes at `~/.hermes`), has a stand-in Kanban board so crew setup
  shows its cards, and can use the live catalog.

## 0.3.4 (2026-10-15)

- Needs Hermes 0.22.0 or later, the version Cabinet's add, remove and live-catalog paths were checked on.
- The install steps were run against a fresh copy of Hermes 0.22.1 and the README now says what a first install shows:
  the expected "unreviewed source" notice until Cabinet is in the catalog, Rescan or a gateway restart as alternatives
  to restarting Desktop, git as a requirement, and where Cabinet keeps its record (`$HERMES_HOME/state/bot-cabinet-v2/`).
- botcabinet.com now publishes its catalog file, so Cabinet shows the live catalog ("Updated <date> from botcabinet.com").

## 0.3.3

- Fixed: Cabinet could record, and rewrite the instructions of, a profile it didn't make. After a failed create the
  page still asked the backend to record the plan, and the backend accepted any folder of the planned name made after
  the plan, rewrote its SOUL.md if it differed and recorded it as Cabinet's. That could take over a bot Hermes's own
  Bots catalog had just installed under the same name. Now the page tells the backend whether its own
  `profiles.create` succeeded. Only then may Cabinet repair the instructions. After a failed create it records the
  profile only when the instructions are already exactly the planned ones, as My Bots' catch-up already did. When
  Hermes says the profile already exists, nothing is recorded. A profile with `bot-blueprint.yaml`, or whose
  `profile.yaml` says `kind: bot-catalog`, is never recorded, written to or removed, for a single Bot or a crew.
- Detection of Hermes's own Bots catalog (NousResearch/hermes-agent#119260) is now the gateway answering
  `bots.catalog`, in place of a guess at a module name. A yes is kept for half an hour, a no is asked again after a
  minute, and Refresh asks straight away. On the Python side, `detect_core_catalog` asks
  `hermes_cli.bot_catalog.load_bot_catalog(include_live=False)`.
- Hand-over: a Bot that catalog carries gets "Add in Hermes Bots", which opens Capabilities, Bots tab, and names the
  Bot to pick. The "Add … with Cabinet anyway" path is gone. Bots the catalog doesn't carry keep Cabinet's own Add.
- No second copies: The Cabinet, a Bot's Add screen and My Bots show "Already added through Hermes Bots" for a Bot
  Hermes's catalog already added.
- The notice about the catalog names the real place (Capabilities, Bots tab, or "Browse Bot Marketplace…" in the
  + menu next to Bots in the sidebar) and appears only when the gateway answers `bots.catalog`.
- Crews and group chats: the crew screen's account and the set-up done screen point to Hermes's own group chats for
  talking a job through with the crew's bots. Crew Kits stay the ordered handoff with blocked Kanban cards.
- `tests/run_with_hermes.py` takes the Hermes clone from `CABINET_HERMES_SRC` only; there is no default folder.

## 0.3.2

- Test Records and each Bot's test record in plain words. botcabinet.com writes its records for testers ("exact package",
  "prompt-contract", "fixture", "import check"). Cabinet now shows the same facts plainly: Passed twice, Passed once,
  Run twice, one passed, Partly recorded, Test planned. A record the site adds later falls back to the site's wording.
  The Publishing Desk card now says Not passed yet, matching its crew test.
- Chat buttons send. "Ask Hermes now", "Draft the skill with Hermes", "Test it once with Hermes" and Bot Lab's
  "Ask Hermes to help" open a new chat and send the message there (host.composer.submit, tried every 125 ms for up
  to 3 seconds while the chat opens). The clipboard is used only when the app has no composer to send through.
- Bot Lab: no more pressing Keep on all eight lines before Create works. Change any line, then create the Bot.
  The "Standard wording · edit this" tags now read "Suggested".
- One set of names for the five answers. The ladder on Start Here and the Fit Test said "Assignment, Skill,
  Routine, Bot, Crew" next to answers named "Ask Hermes once, Save it as a skill, Run it on a schedule, One Bot,
  A crew". Both now use the answer names. The duplicate list of answers beside the Fit Test and its answer is gone.
- Fit Test answers: the "Copy the plan" button is gone; the main button does the work. Plainer wording for the
  first test, "done when" and Bot Lab's starting lines (no "assignment" or "continuing conversation").
- Start Here: the panel explaining how Cabinet sits beside the Hermes Bots catalog is gone. When that catalog is
  in your Hermes, a short notice says so.
- My Bots: a new user sees "My Bots" and an empty state, not "Everything Cabinet has set up for you" with a
  folder path. Crews read "Ready" instead of "Set up"; "Kanban cards…" is now "Make Kanban cards". A changed bot
  says "Its instructions have been edited" (no SOUL.md).
- Test Records: the Inspection Desk explainer cards, the repeated caveat and the "fixture" line are gone; the
  label legend stays. Crew results read "Passed", "Failed" or "Not tested" everywhere ("Failed acceptance" and
  "Correction prepared; not tested" were site terms). Crew counts on Crew Kits and Test Records always add up.
- Add and Set up: shorter leads; no SOUL.md, "cost check" or "the way New Agent does". Set up a crew folds the
  "what Hermes can and can't do" panel into one line with the detail behind a toggle.
- Bot pages: "Asks you before" lines no longer start with "Ask before", and repeated lines are dropped. The
  portrait caption is gone.
- The catalog line under each page shows only how fresh the Bots are, plus a note when something actually went
  wrong. It no longer explains that botcabinet.com doesn't publish a live catalog yet.

## 0.3.1

- Crews: every bot's instructions now carry the crew's own "ask the owner before" and "never" lists. The crew page
  showed them, but only each Bot's own limits reached its instructions.
- Crews: a crew that failed its published crew test says so on its page and on the set-up screen, with a link to
  the test record.
- Crew counts read "4 steps" (they said "4 handoffs" for four steps, which is three handoffs).
- The crew page's Permissions line is in plain words; the site's line spoke of "Passports".
- Fit Test: a Crew answer with no close Crew Kit used to show a "Plan a crew" button that did nothing. It now opens
  the Crew Kits. A Crew answer with a kit offers "Set up" directly.
- My Bots: Remove, Kanban cards and Make a bot from it open inside their own card, next to the button, instead of
  at the foot of the page. Cards no longer stretch to match their neighbour.
- Hand-over: the secondary button reads "Add Scout with Cabinet anyway".
- Screenshots: `harness/shoot.py` waits for each scene to be ready (with Python Playwright and the installed Chrome),
  so scenes that take long to seed no longer come out blank. Four new scenes: My Bots at narrow width, a failed
  crew's set-up, a Crew answer with no kit, and Kanban cards in My Bots.
- The harness's default port is 8893.
- Tests use Kanban's current connection module; the fallback to the old one applies only when the new one is missing.

## 0.3.0

- Crews: each bot's instructions now follow the crew's handoff order (its own step, who it gets work from, who it
  hands to next). Before, they followed the order the members were listed in, which was wrong for most crews.
- Crews: "hiring manager" and "learner" steps count as steps that stop for you; a step's bots are listed in the order
  it names them; Kanban cards name every bot in a shared step and say when no bot is named.
- Crews: a profile you already have comes with its part in the crew to copy and paste; Cabinet still never writes
  into it.
- Adding: the page waits up to two minutes for Hermes to make a profile (it seeds files in a separate process; the
  gateway's default is 30 seconds). If the reply is lost, Cabinet still finds and records the profile, for single
  Bots, Bot Lab, kits and crew members.
- Hand-over: when the Hermes Bots catalog is detected, the Add screen points to it and Cabinet's own Add becomes
  "Add with Cabinet anyway". Start Here explains how Cabinet sits beside the catalog.
- Fit Test: a Bot answer offers "Add it to my bots" directly.
- Live sync: botcabinet.com not publishing catalog.json yet reads as a calm note, not an error; real problems say the
  copy was kept.
- Look: test badges no longer collide with the Add button; tabs fit at narrow widths; crew portraits are lifted so
  faces read at small sizes.
- Tests: end to end through Hermes's real profiles.create and SOUL.md loader for every Bot and crew
  (`tests/test_e2e_gateway.py`); every page call matched to a backend route; no personal paths in tests.
- REVIEWER-GUIDE.md, CREDITS.md and a rewritten README.

## 0.2.0

- Add: every Bot has an Add button and a review screen; Cabinet plans, checks and records the profile Hermes makes. Never overwrites a profile you have.
- Set up a crew: review screen, reuse of bots you already have, honest account of what Hermes can and cannot do, optional blocked Kanban cards on confirmation.
- My Bots: what Cabinet set up, live status, a link into each bot, Remove for anything unchanged.
- Bot Lab: plans go through the same checks and record; save a plan as a reusable kit.
- Live sync with https://botcabinet.com/catalog.json, strict schema, saved copy, bundled fallback; the catalog snapshot is now schema version 2.
- Hand-over detection for a future Hermes Bots catalog.
- Safety checks on every name and instruction; isolation tests that fail if the real ~/.hermes changes.
- docs/site-catalog-contract.md and tools/make_site_catalog.py for the website.

## 0.1.0

- Fit Test, Bot Lab draft plan, read-only Crew Kits, Test Records.
